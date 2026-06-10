#!/usr/bin/env python3
"""
ingest.py — Local CLI ingestion pipeline for runbook Markdown files.

Run:      python ingest.py --docs ./source/
Dry-run:  python ingest.py --docs ./source/ --dry-run

Requires: SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENAI_API_KEY in .env or environment.
"""

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_openai import OpenAIEmbeddings
from supabase import create_client

load_dotenv()

# ─── Constants ────────────────────────────────────────────────────────────────

EXCLUDED_SECTION_NUMBERS = {"3", "8", "9"}

SECTION_TYPE_MAP = {
    "overview": "overview",
    "error signatures": "error_signatures",
    "triage decision tree": "triage",
    "troubleshooting steps": "troubleshooting",
    "common mistakes to avoid": "mistakes",
    "escalation matrix": "escalation",
}


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    content: str     # clean body — stored in Supabase, returned to LLM
    embed_text: str  # [header]\n\ncontent — used for embedding only
    metadata: dict


# ─── Markdown parser ──────────────────────────────────────────────────────────

def parse_sections(text: str) -> list[dict]:
    """
    Split markdown into a flat list of {"level", "title", "body"} dicts.
    Heading detection is suppressed inside fenced code blocks so that
    headings inside code examples are never treated as section boundaries.
    """
    sections = []
    current_level = None
    current_title = None
    current_lines: list[str] = []
    in_fence = False

    for line in text.split("\n"):
        stripped = line.rstrip()

        if stripped.startswith("```"):
            in_fence = not in_fence

        if not in_fence and stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= level <= 3 and len(stripped) > level and stripped[level] == " ":
                if current_title is not None:
                    sections.append({
                        "level": current_level,
                        "title": current_title,
                        "body": "\n".join(current_lines).strip(),
                    })
                current_level = level
                current_title = stripped[level + 1:].strip()
                current_lines = []
                continue

        if current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections.append({
            "level": current_level,
            "title": current_title,
            "body": "\n".join(current_lines).strip(),
        })

    return sections


# ─── Metadata helpers ─────────────────────────────────────────────────────────

def extract_section_num(title: str) -> str:
    """'1. Overview' → '1';  'Document Information' → ''"""
    m = re.match(r"^(\d+)\.", title.strip())
    return m.group(1) if m else ""


def map_section_type(heading_title: str) -> str:
    """Map heading text to section_type enum value. Defaults to 'troubleshooting'."""
    normalized = re.sub(r"^\d+\.\s*", "", heading_title).lower().strip()
    return SECTION_TYPE_MAP.get(normalized, "troubleshooting")


def extract_error_codes(text: str) -> list[str]:
    codes: list[str] = []
    # Hex error codes: `0x00d30003`
    codes.extend(re.findall(r"`(0x[0-9A-Fa-f]+)`", text))
    # All-caps symbolic codes: `ETIMEDOUT`, `ECONNREFUSED`, `JWT_SIGNATURE_INVALID`
    codes.extend(re.findall(r"`([A-Z][A-Z_0-9]{4,})`", text))
    return list(dict.fromkeys(codes))  # deduplicate, preserve order


def extract_http_status_codes(text: str) -> list[str]:
    codes: list[str] = []
    codes.extend(re.findall(r"HTTP\s+([2-5][0-9]{2})", text))
    codes.extend(re.findall(r"`([2-5][0-9]{2})`", text))
    return list(dict.fromkeys(codes))


def extract_doc_meta(text: str, sections: list[dict], filename: str) -> dict:
    """Extract document-level metadata shared across all chunks of one runbook."""
    pattern_id_m = re.search(r"Pattern_(\d+)", filename)
    pattern_id = f"Pattern_{pattern_id_m.group(1)}" if pattern_id_m else ""

    # Pattern name: "# Pattern N Runbook: <Name>" → "<Name>"
    title_m = re.search(r"^#\s+.*?Runbook:\s+(.+)$", text, re.MULTILINE)
    pattern_name = title_m.group(1).strip() if title_m else ""

    # Category and severity from Document Information table
    category_m = re.search(r"\|\s*\*\*Category\*\*\s*\|\s*(.+?)\s*\|", text)
    severity_m = re.search(r"\|\s*\*\*Severity\*\*\s*\|\s*(.+?)\s*\|", text)
    category = category_m.group(1).strip() if category_m else ""
    severity = severity_m.group(1).strip() if severity_m else ""

    # Related patterns from Section 8 body
    sec8 = next((s for s in sections if extract_section_num(s["title"]) == "8"), None)
    related_raw = re.findall(r"Pattern_(\d+)", sec8["body"] if sec8 else "")
    related_patterns = [f"Pattern_{n}" for n in related_raw]

    return {
        "runbook_name": Path(filename).stem,
        "pattern_id": pattern_id,
        "pattern_name": pattern_name,
        "category": category,
        "severity": severity,
        "related_patterns": related_patterns,
    }


# ─── Chunk builder ────────────────────────────────────────────────────────────

def build_chunk(
    section_title: str,
    content: str,
    doc_meta: dict,
    chunk_index: int,
    section_type: str,
) -> Chunk:
    pattern_id = doc_meta["pattern_id"]
    pattern_name = doc_meta["pattern_name"]
    chunk_id = f"{pattern_id}_chunk_{chunk_index}"

    # Header prepended before embedding only — not stored in content column
    header = f"[{pattern_name} | {section_title}]"
    embed_text = f"{header}\n\n{content}"

    metadata = {
        "runbook_name": doc_meta["runbook_name"],
        "section_title": section_title,
        "pattern_id": pattern_id,
        "chunk_index": chunk_index,
        "pattern_name": pattern_name,
        "category": doc_meta["category"],
        "severity": doc_meta["severity"],
        "section_type": section_type,
        "error_codes": extract_error_codes(content),
        "http_status_codes": extract_http_status_codes(content),
        "related_patterns": doc_meta["related_patterns"],
    }

    return Chunk(
        chunk_id=chunk_id,
        content=content,
        embed_text=embed_text,
        metadata=metadata,
    )


# ─── Document processor ───────────────────────────────────────────────────────

def process_document(filepath: str) -> list[Chunk]:
    """Parse one runbook Markdown file and return its chunks."""
    text = Path(filepath).read_text(encoding="utf-8")
    filename = Path(filepath).name
    sections = parse_sections(text)
    doc_meta = extract_doc_meta(text, sections, filename)

    chunks: list[Chunk] = []
    chunk_index = 0
    i = 0

    while i < len(sections):
        sec = sections[i]

        # Level 1 is the document title — metadata only, not a chunk
        if sec["level"] == 1:
            i += 1
            continue

        if sec["level"] == 2:
            section_num = extract_section_num(sec["title"])

            # Skip "Document Information" (no number) and excluded sections 3, 8, 9
            if not section_num or section_num in EXCLUDED_SECTION_NUMBERS:
                i += 1
                while i < len(sections) and sections[i]["level"] > 2:
                    i += 1
                continue

            # Section 5 (Troubleshooting Steps): skip the ## parent,
            # create one chunk per ### sub-section
            if section_num == "5":
                i += 1
                while i < len(sections) and sections[i]["level"] > 2:
                    sub = sections[i]
                    sub_title = f"Troubleshooting: {sub['title']}"
                    if sub["body"].strip():
                        chunks.append(
                            build_chunk(sub_title, sub["body"], doc_meta, chunk_index, "troubleshooting")
                        )
                        chunk_index += 1
                    i += 1
                continue

            # All other ## sections: combine own body + any ### children into one chunk
            body_parts: list[str] = []
            if sec["body"].strip():
                body_parts.append(sec["body"])

            i += 1
            while i < len(sections) and sections[i]["level"] > 2:
                sub = sections[i]
                sub_heading = "#" * sub["level"] + " " + sub["title"]
                if sub["body"].strip():
                    body_parts.append(f"{sub_heading}\n\n{sub['body']}")
                else:
                    body_parts.append(sub_heading)
                i += 1

            combined_body = "\n\n".join(body_parts)
            if combined_body.strip():
                section_type = map_section_type(sec["title"])
                chunks.append(
                    build_chunk(sec["title"], combined_body, doc_meta, chunk_index, section_type)
                )
                chunk_index += 1
            continue

        # Orphan level-3 outside section 5 — skip
        i += 1

    return chunks


# ─── Supabase upload ──────────────────────────────────────────────────────────

def upload_chunks(chunks: list[Chunk]) -> None:
    supabase = create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_KEY"],
    )
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    vectorstore = SupabaseVectorStore(
        client=supabase,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents",
    )

    print(f"Uploading {len(chunks)} chunks to Supabase...")
    vectorstore.add_texts(
        texts=[c.embed_text for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    print("Upload complete.")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest runbook Markdown files into Supabase."
    )
    parser.add_argument("--docs", default="./source/", help="Directory of .md runbook files")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print chunk summaries without uploading",
    )
    args = parser.parse_args()

    doc_files = sorted(Path(args.docs).glob("*.md"))
    if not doc_files:
        print(f"No .md files found in {args.docs}")
        return

    print(f"Found {len(doc_files)} file(s) in {args.docs}\n")

    all_chunks: list[Chunk] = []
    for filepath in doc_files:
        chunks = process_document(str(filepath))
        print(f"  {filepath.name}: {len(chunks)} chunk(s)")
        all_chunks.extend(chunks)

    print(f"\nTotal: {len(all_chunks)} chunks")

    if args.dry_run:
        print()
        for c in all_chunks:
            print(f"[{c.chunk_id}]")
            print(f"  section_title:     {c.metadata['section_title']}")
            print(f"  section_type:      {c.metadata['section_type']}")
            print(f"  error_codes:       {c.metadata['error_codes']}")
            print(f"  http_status_codes: {c.metadata['http_status_codes']}")
            print(f"  content[:120]:     {c.content[:120]!r}")
            print()
        return

    upload_chunks(all_chunks)


if __name__ == "__main__":
    main()
