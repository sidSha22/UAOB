"""Part B: PDF manuals -> Document / ManualSection / Chunk.

For each PDF in manuals/:
  1. Extract text per page (pypdf).
  2. Try to detect sections from heading-like lines (title-case / numbered
     lines, optionally followed by "Rev ..." / "Revision ..."). If detection
     yields too few sections to be trustworthy, fall back to one section per
     page and stamp Document.section_detection = 'page_fallback'.
  3. Chunk each section's text into 400-600 token windows (bge tokenizer)
     with 80-token overlap, MERGE'd as Chunk -[:FROM]-> ManualSection.

Not yet run against real data: manuals/ does not exist in this repo yet
(see CONTEXT.md ingestion notes). Resumable like the other ingest scripts --
MERGE on stable ids, so re-running only fills gaps left by a crash.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "graph"))
from db import run_query  # noqa: E402

from embedder import count_tokens  # noqa: E402

MANUALS_DIR = Path(__file__).resolve().parent.parent / "manuals"
PIPELINE_VERSION = "v0-manual-chunks"

CHUNK_MIN_TOKENS = 400
CHUNK_MAX_TOKENS = 600
CHUNK_OVERLAP_TOKENS = 80
MIN_SECTIONS_PER_PAGE_RATIO = 0.15  # below this, section detection is untrustworthy

# Heading heuristic: a numbered heading ("3.2 Cooling System") or a short
# ALL-CAPS/Title-Case line, optionally followed by a revision marker.
_HEADING_RE = re.compile(
    r"^(?:(?P<num>\d+(?:\.\d+)*)\s+)?(?P<title>[A-Z][A-Za-z0-9/&\-,() ]{2,80})$"
)
_REVISION_RE = re.compile(r"\bRev(?:ision)?\.?\s*([A-Za-z0-9.]+)", re.IGNORECASE)


def extract_pages(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def detect_sections(pages):
    """Returns list of {title, revision, start_page, text} or None if
    detection is unreliable."""
    sections = []
    current = None
    for page_num, page_text in enumerate(pages):
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _HEADING_RE.match(line)
            if m and len(line.split()) <= 12:
                if current is not None:
                    sections.append(current)
                revision = None
                rev_m = _REVISION_RE.search(line)
                if rev_m:
                    revision = rev_m.group(1)
                current = {
                    "title": m.group("title").strip(),
                    "revision": revision,
                    "start_page": page_num,
                    "text_parts": [],
                }
                continue
            if current is not None:
                current["text_parts"].append(line)
            else:
                # text before the first detected heading: drop into an
                # implicit front-matter section
                current = {
                    "title": "Front Matter",
                    "revision": None,
                    "start_page": page_num,
                    "text_parts": [line],
                }
    if current is not None:
        sections.append(current)

    if len(sections) < max(2, int(len(pages) * MIN_SECTIONS_PER_PAGE_RATIO)):
        return None

    for s in sections:
        s["text"] = "\n".join(s["text_parts"]).strip()
        del s["text_parts"]
    return [s for s in sections if s["text"]]


def page_fallback_sections(pages):
    sections = []
    for page_num, page_text in enumerate(pages):
        text = page_text.strip()
        if not text:
            continue
        sections.append({
            "title": f"Page {page_num + 1}",
            "revision": None,
            "start_page": page_num,
            "text": text,
        })
    return sections


def chunk_text(text):
    """Token-window chunking with overlap, using the bge tokenizer so token
    counts here match what's embedded (no re-tokenization drift)."""
    from embedder import get_model
    tokenizer = get_model().tokenizer
    ids = tokenizer.encode(text, add_special_tokens=False)
    if not ids:
        return []

    chunks = []
    stride = CHUNK_MAX_TOKENS - CHUNK_OVERLAP_TOKENS
    start = 0
    while start < len(ids):
        end = min(start + CHUNK_MAX_TOKENS, len(ids))
        window = ids[start:end]
        if len(window) < CHUNK_MIN_TOKENS and start != 0 and end == len(ids):
            # tail shorter than the minimum: merge into previous chunk's tail
            # by just emitting it as-is (better than dropping content)
            pass
        chunk_str = tokenizer.decode(window)
        chunks.append({"text": chunk_str, "tokens": len(window)})
        if end == len(ids):
            break
        start += stride
    return chunks


def process_pdf(pdf_path, existing_chunk_ids):
    doc_id = f"doc::{pdf_path.stem}"
    pages = extract_pages(pdf_path)
    sections = detect_sections(pages)
    section_detection = "heading_regex"
    if sections is None:
        sections = page_fallback_sections(pages)
        section_detection = "page_fallback"

    run_query("""
        MERGE (d:Document {id: $id})
        SET d.type = 'manual',
            d.uri = $uri,
            d.section_detection = $section_detection,
            d.embedding_model = 'BAAI/bge-small-en-v1.5',
            d.embedding_convention = 'no_prefix_indexing__query_prefix_at_retrieval',
            d.pipeline_version = $pipeline_version,
            d.synthetic = false
    """, {
        "id": doc_id, "uri": str(pdf_path), "section_detection": section_detection,
        "pipeline_version": PIPELINE_VERSION,
    })

    chunk_rows = []
    for sec_idx, sec in enumerate(sections):
        section_id = f"{doc_id}::sec{sec_idx:04d}"
        run_query("""
            MATCH (d:Document {id: $doc_id})
            MERGE (s:ManualSection {id: $section_id})
            SET s.doc = $doc_id,
                s.section = $section_id,
                s.title = $title,
                s.revision = $revision,
                s.start_page = $start_page,
                s.pipeline_version = $pipeline_version,
                s.synthetic = false
            MERGE (s)-[:PART_OF]->(d)
        """, {
            "section_id": section_id, "doc_id": doc_id, "title": sec["title"],
            "revision": sec["revision"], "start_page": sec["start_page"],
            "pipeline_version": PIPELINE_VERSION,
        })

        for chunk_idx, chunk in enumerate(chunk_text(sec["text"])):
            chunk_id = f"{section_id}::chunk{chunk_idx:04d}"
            if chunk_id in existing_chunk_ids:
                continue
            chunk_rows.append({
                "chunk_id": chunk_id,
                "section_id": section_id,
                "text": chunk["text"],
                "tokens": chunk["tokens"],
            })

    cypher = """
    UNWIND $rows AS row
    MATCH (s:ManualSection {id: row.section_id})
    MERGE (c:Chunk {id: row.chunk_id})
    ON CREATE SET c.text = row.text,
                  c.tokens = row.tokens,
                  c.source_type = 'manual',
                  c.pipeline_version = $pipeline_version,
                  c.synthetic = false
    MERGE (c)-[:FROM]->(s)
    """
    for i in range(0, len(chunk_rows), 500):
        run_query(cypher, {"rows": chunk_rows[i:i + 500], "pipeline_version": PIPELINE_VERSION})

    print(f"{pdf_path.name}: {len(sections)} sections ({section_detection}), "
          f"{len(chunk_rows)} new chunks")


def main():
    if not MANUALS_DIR.exists():
        print(f"No manuals/ directory found at {MANUALS_DIR} -- nothing to do.")
        return

    pdfs = sorted(MANUALS_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {MANUALS_DIR} -- nothing to do.")
        return

    existing = run_query("MATCH (c:Chunk {source_type: 'manual'}) RETURN c.id AS id")
    existing_ids = {row["id"] for row in existing}
    print(f"{len(existing_ids)} manual Chunks already exist (resuming, will skip)")

    for pdf_path in pdfs:
        process_pdf(pdf_path, existing_ids)


if __name__ == "__main__":
    main()
