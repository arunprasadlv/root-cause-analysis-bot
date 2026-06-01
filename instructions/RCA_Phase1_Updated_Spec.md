# Phase 1 — Updated Specification
## Ingestion Pipeline & Knowledge Base

> **Amendment to:** RCA_Implementation_Plan.pdf — Phase 1 (v1.0, 26 May 2026)  
> **Updated:** 2026-05-31  
> **Reason:** Source files are Markdown (not Word); chunking boundary and metadata schema gaps identified after reviewing actual runbook content.  
> **Stack note:** ChromaDB references in this document are superseded by Supabase pgvector. See `RCA_Stack_Update_Supabase_Vercel.md` for full stack change details.

---

## Changes from Original Plan

### Change 1 — Document Parser: python-docx → markdown-it-py

The original plan specifies `python-docx` as the document parser. The actual source files are `.md` (Markdown), not `.docx`. `python-docx` cannot parse Markdown.

**Replace:**
```python
# REMOVED — cannot parse .md files
import docx
```

**With:**
```python
# markdown-it-py: preserves heading hierarchy and fenced code blocks
from markdown_it import MarkdownIt

md = MarkdownIt()
tokens = md.parse(raw_text)
```

The parser must:
- Extract heading levels (`#`, `##`, `###`) to drive chunk boundaries
- Preserve table content as plain text (convert pipe-table rows to readable strings)
- Preserve fenced code blocks (Splunk queries, bash commands, JavaScript snippets) intact as a single string — never split mid-block
- Retain the heading text as the `section_title` metadata field

---

### Change 2 — Chunking Boundary: Clarified Split Rules

The original plan states "chunk by heading structure" without specifying how to handle Section 5, which has nested sub-sections (`##` and `###` levels) and accounts for ~60% of each document's content.

**Explicit split rules:**

| Heading Level | Section | Rule |
|---|---|---|
| `##` | Sections 1, 2, 3, 4, 6, 7, 8, 9 | One chunk per `##` section |
| `###` | Section 5 sub-sections (5.1, 5.2, 5.3, 5.4) | One chunk per `###` sub-section |

**Why Section 5 must split at `###`:**  
Section 5 combined is 1,500–2,000+ tokens across 3–4 sub-sections. Each sub-section (e.g. "5.2 Timeout Config Mismatch") covers a distinct failure scenario with its own symptoms, resolution steps, and validation commands. Embedding them together would dilute retrieval precision — an operator querying a timeout config issue should retrieve section 5.2, not the entire troubleshooting block.

**Sections to exclude from Supabase** (low retrieval value, add noise):

| Section | Content | Action |
|---|---|---|
| Section 3 — Architecture Context | ASCII art diagrams | Exclude from indexing |
| Section 9 — Revision History | Version/date table | Exclude from indexing |
| Section 8 — Related Runbooks | Cross-reference links | Exclude from indexing (captured in metadata instead) |

---

### Change 3 — Chunk Header Injection (New)

Each chunk must have a plain-text context header prepended **before embedding**. This ensures chunks retrieved in isolation remain self-identifying and improves embedding alignment for partial-match queries.

**Format:**
```
[ESGA {pattern_name} | {section_title}]

{chunk_body}
```

**Example:**
```
[ESGA Pattern 1 — Backend Connection Timeout | Troubleshooting: 5.2 Timeout Config Mismatch]

**Symptoms:** Specific backend times out intermittently under load. Errors increase during
peak traffic windows. Backend team confirms processing time exceeds DataPower timeout.
...
```

The header is part of the embedded text only. It is **not** stored as the chunk document body in Supabase — the clean body is stored separately in the `content` column and returned to the LLM as context. The header is prepended only at embedding time.

---

### Change 4 — Metadata Schema: Enriched

The original plan defines: `runbook_name, section_title, pattern_id, chunk_index`

This is insufficient for metadata pre-filtering in Phase 3 hybrid retrieval. The following fields are added, all derivable from the document content during ingestion with no LLM call required.

**Full metadata schema:**

```python
{
    # Original fields (unchanged)
    "runbook_name":     str,   # e.g. "ESGA_Pattern_1_Backend_Connection_Timeout"
    "section_title":    str,   # e.g. "Troubleshooting: 5.2 Timeout Config Mismatch"
    "pattern_id":       str,   # e.g. "Pattern_1"
    "chunk_index":      int,   # sequential index within the document

    # New fields
    "pattern_name":     str,   # e.g. "Backend Connection Timeout"
    "category":         str,   # e.g. "Backend Connectivity"
    "severity":         str,   # e.g. "P2 - High"
    "section_type":     str,   # one of: overview | error_signatures | triage |
                               #         troubleshooting | mistakes | escalation
    "error_codes":      list,  # e.g. ["0x00d30003", "ETIMEDOUT", "ECONNREFUSED"]
    "http_status_codes": list, # e.g. ["503", "404"]
    "related_patterns": list,  # e.g. ["Pattern_2", "Pattern_9", "Pattern_10"]
}
```

**Extraction logic (no LLM required):**

| Field | Source |
|---|---|
| `pattern_name` | Document `# Title` heading, strip "ESGA Pattern N Runbook: " prefix |
| `category` | Document Information table, "Category" row |
| `severity` | Document Information table, "Severity" row |
| `section_type` | Map heading text → enum at parse time (see mapping below) |
| `error_codes` | Regex scan of chunk text: `` `0x[0-9A-Fa-f]+` ``, `` `[A-Z_]{5,}` `` in error tables |
| `http_status_codes` | Regex scan: `HTTP [0-9]{3}` or `` `[0-9]{3}` `` adjacent to status label |
| `related_patterns` | Section 8 link list, extract `Pattern_N` from each link |

**Section type mapping:**

```python
SECTION_TYPE_MAP = {
    "overview":                     "overview",
    "error signatures":             "error_signatures",
    "triage decision tree":         "triage",
    "troubleshooting steps":        "troubleshooting",  # parent — not chunked
    "common mistakes to avoid":     "mistakes",
    "escalation matrix":            "escalation",
}
# Section 5.x sub-sections inherit section_type = "troubleshooting"
```

**Supabase pre-filter examples (Phase 3):**

```python
# Error code query — pre-filter before vector search (via LangChain SupabaseVectorStore)
vectorstore.similarity_search(
    query=hyde_query,
    k=5,
    filter={"error_codes": "JWT_SIGNATURE_INVALID"}
)

# Escalation query — only return escalation matrix chunks
vectorstore.similarity_search(
    query=hyde_query,
    k=3,
    filter={"section_type": "escalation"}
)
```

Supabase stores metadata in a `jsonb` column. The `filter` dict maps to a SQL `WHERE metadata @> '{"key": "value"}'` clause via LangChain's `SupabaseVectorStore`. For array containment (e.g. `error_codes`), the `match_documents` RPC function is extended with a `jsonb @>` operator. See `RCA_Stack_Update_Supabase_Vercel.md` for the full SQL schema.

---

## Updated Phase 1 Component Summary

| Component | Original | Updated |
|---|---|---|
| Document Parser | `python-docx` | `markdown-it-py` |
| Vector Store | ChromaDB (local) | Supabase pgvector (cloud) |
| Sparse Index | `rank_bm25` disk file | Supabase FTS (`tsvector`) |
| Split rule | "by heading structure" | `##` for all sections; `###` within Section 5 |
| Excluded sections | Not specified | Section 3, 8, 9 excluded |
| Chunk header injection | Not specified | Prepend `[Pattern Name \| Section]` before embedding |
| Metadata fields | 4 fields | 12 fields (see schema above) |
| Metadata extraction | Not specified | Regex + heading parse, no LLM call |

## Updated Deliverables

| # | Deliverable | Status |
|---|---|---|
| 1 | Ingestion script (`ingest.py`) using `markdown-it-py` | Updated |
| 2 | Supabase `documents` table populated with enriched metadata | Updated |
| 3 | Supabase FTS index (`tsvector`) configured for sparse retrieval | Updated |
| 4 | Metadata schema defined and documented | **This document** |
| 5 | Chunk header injection applied at embedding time | New |
| 6 | Section exclusion list applied at parse time | New |
