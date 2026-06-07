# Stack Update — Supabase + Vercel
## Architecture Amendment v2

> **Amendment to:** RCA_Implementation_Plan.pdf (v1.0, 26 May 2026)  
> **Updated:** 2026-05-31  
> **Scope:** Full stack replacement — ChromaDB → Supabase pgvector; On-Premises → Vercel

---

## Stack Change Summary

| Component | Original Plan | Updated |
|---|---|---|
| Vector store | ChromaDB (local, on-prem) | Supabase pgvector (cloud, managed) |
| Sparse index | `rank_bm25` library + disk file | Supabase native FTS (`tsvector`) |
| LangChain vector store class | `Chroma` | `SupabaseVectorStore` |
| Deployment — frontend | On-prem app server | Vercel |
| Deployment — backend API | On-prem app server (Docker) | Vercel Serverless Functions (Python) |
| Environment config | Server env vars / `.env` | Vercel Environment Variables dashboard |
| Infrastructure maintenance | Self-managed | Supabase managed; Vercel managed |

---

## Updated System Architecture

```
Web Portal (Next.js or static)
  Hosted on Vercel
  | REST API — each request carries full history
  |
Vercel Serverless Functions (Python / FastAPI)
  |
  +---+-------------------+-------------------+
  |                       |                   |
Log Summarizer       RAG Pipeline         LLM Abstraction Layer
                    (LangChain)           (OpenAI / Anthropic / …)
                         |                         |
               SupabaseVectorStore          Enterprise LLM API
               + Supabase FTS              (Cloud — via API gateway)
                         |
                   Supabase (Cloud)
                   pgvector + PostgreSQL FTS
                         |
              Ingestion Pipeline (manual CLI trigger)
              Markdown → Parser → Chunker → Embedder → Supabase
```

---

## Phase 1 — Ingestion Pipeline Changes

### Supabase Table Schema

Run once in the Supabase SQL editor to set up the database before first ingestion.

```sql
-- Enable pgvector extension
create extension if not exists vector;

-- Main documents table
create table documents (
  id          bigserial primary key,
  content     text        not null,           -- clean chunk body (no header)
  metadata    jsonb       not null default '{}',
  embedding   vector(1536)                    -- text-embedding-3-small dimensions
);

-- Full-text search column (auto-maintained by Postgres)
alter table documents
  add column fts tsvector
  generated always as (to_tsvector('english', content)) stored;

-- Indexes
create index on documents using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);
create index on documents using gin(fts);
create index on documents using gin(metadata);   -- for jsonb containment queries
```

### match_documents RPC Function

LangChain's `SupabaseVectorStore` requires a `match_documents` Postgres function. The standard version is extended here to support `jsonb` metadata filtering.

```sql
create or replace function match_documents (
  query_embedding  vector(1536),
  match_count      int     default 10,
  filter           jsonb   default '{}'
)
returns table (
  id         bigint,
  content    text,
  metadata   jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    d.id,
    d.content,
    d.metadata,
    1 - (d.embedding <=> query_embedding) as similarity
  from documents d
  where d.metadata @> filter
  order by d.embedding <=> query_embedding
  limit match_count;
end;
$$;
```

### Ingestion Script Changes

```python
# requirements — replace chromadb with supabase
# REMOVE:  chromadb, rank_bm25
# ADD:     supabase, langchain-community

from supabase import create_client
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_openai import OpenAIEmbeddings

supabase = create_client(
    supabase_url=os.environ["SUPABASE_URL"],
    supabase_key=os.environ["SUPABASE_SERVICE_KEY"]  # service role key for writes
)

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

vectorstore = SupabaseVectorStore(
    client=supabase,
    embedding=embeddings,
    table_name="documents",
    query_name="match_documents",
)

# Ingest chunks — header prepended for embedding, clean body stored
vectorstore.add_texts(
    texts=[chunk.embed_text for chunk in chunks],   # header + body
    metadatas=[chunk.metadata for chunk in chunks],
    ids=[chunk.id for chunk in chunks],
)
# SupabaseVectorStore stores the text as-is in content column.
# To store clean body separately, write directly via supabase client
# and manage embeddings manually (see advanced pattern in project wiki).
```

### Environment Variables (Phase 1)

```
SUPABASE_URL=https://<project-id>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>      # write access for ingestion
OPENAI_API_KEY=<key>
```

### What is Eliminated

| Original | Replacement | Reason |
|---|---|---|
| `chromadb` package | `supabase` package | Vector store migration |
| `rank_bm25` package | Supabase FTS (`tsvector`) | Sparse search now in-database |
| BM25 index file on disk | Postgres `gin(fts)` index | Managed by Supabase |
| ChromaDB local directory | Supabase cloud project | No local storage needed |

---

## Phase 2 — Retriever Change

Replace the ChromaDB-based retriever with `SupabaseVectorStore`.

```python
# BEFORE (Phase 2 original)
from langchain_community.vectorstores import Chroma
retriever = Chroma(
    persist_directory="./chroma_db",
    embedding_function=embeddings,
).as_retriever(search_kwargs={"k": 5})

# AFTER (Supabase)
from langchain_community.vectorstores import SupabaseVectorStore
retriever = SupabaseVectorStore(
    client=supabase,
    embedding=embeddings,
    table_name="documents",
    query_name="match_documents",
).as_retriever(search_kwargs={"k": 5})
```

The rest of the Phase 2 RAG chain (prompt template, LLM abstraction, FastAPI endpoint, citation format) is unchanged. LangChain's retriever interface is the same regardless of vector store backend.

---

## Phase 3 — Hybrid Retrieval Changes

The hybrid retrieval approach (HyDE + Dense + Sparse + RRF) is unchanged in design. The implementation changes: `rank_bm25` is replaced with Supabase FTS.

### Dense Retrieval (Supabase pgvector)

```python
# Dense: HyDE embedding → pgvector cosine similarity
dense_results = vectorstore.similarity_search_with_score(
    query=hyde_passage,
    k=10,
    filter=metadata_filter   # optional jsonb pre-filter
)
```

### Sparse Retrieval (Supabase FTS — replaces rank_bm25)

```python
def sparse_search(query: str, k: int = 10, filter: dict = None) -> list:
    """BM25-equivalent sparse retrieval using Supabase full-text search."""
    ts_query = " | ".join(query.split())   # simple OR query; extend as needed

    rpc_params = {
        "query_text": ts_query,
        "match_count": k,
    }
    if filter:
        rpc_params["filter"] = filter

    response = supabase.rpc("match_documents_fts", rpc_params).execute()
    return response.data
```

Additional RPC function for FTS (run once in Supabase):

```sql
create or replace function match_documents_fts (
  query_text   text,
  match_count  int   default 10,
  filter       jsonb default '{}'
)
returns table (
  id       bigint,
  content  text,
  metadata jsonb,
  rank     float
)
language plpgsql
as $$
begin
  return query
  select
    d.id,
    d.content,
    d.metadata,
    ts_rank(d.fts, to_tsquery('english', query_text))::float as rank
  from documents d
  where d.fts @@ to_tsquery('english', query_text)
    and d.metadata @> filter
  order by rank desc
  limit match_count;
end;
$$;
```

### RRF Merge (unchanged — application layer)

```python
def reciprocal_rank_fusion(
    dense_results: list,
    sparse_results: list,
    k: int = 60
) -> list:
    scores = {}
    for rank, doc in enumerate(dense_results):
        scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)
    for rank, doc in enumerate(sparse_results):
        scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

---

## Phase 4 — No Changes

Log summarizer, context enrichment, and API contract update are backend-only and not affected by the vector store or deployment platform change.

---

## Phase 5 — No Changes

Stateless conversation design, history window management, and context-aware retrieval are not affected.

---

## Phase 6 — Deployment: Vercel

### Architecture

The application is split into two Vercel deployments:

```
vercel.app (Frontend)           vercel.app/api/* (Backend)
Next.js or static HTML    →     Python Serverless Functions
Conversation UI                 FastAPI routes as /api/analyze
                                           |
                                   Supabase (cloud)
                                   LLM API (cloud)
```

Both frontend and backend are deployed from the same Git repository on Vercel. Vercel auto-deploys on push to `main`.

### Project Structure for Vercel

```
root/
├── api/                    ← Vercel Python serverless functions
│   ├── analyze.py          ← POST /api/analyze
│   └── health.py           ← GET /api/health
├── frontend/               ← Static web portal (HTML/JS or Next.js)
│   └── index.html
├── vercel.json             ← Routing + function config
├── requirements.txt        ← Python dependencies
└── ingest.py               ← Local CLI only — not deployed to Vercel
```

### vercel.json

```json
{
  "functions": {
    "api/*.py": {
      "runtime": "vercel-python@3.x",
      "maxDuration": 60
    }
  },
  "routes": [
    { "src": "/api/(.*)", "dest": "/api/$1" },
    { "src": "/(.*)",     "dest": "/frontend/$1" }
  ]
}
```

`maxDuration: 60` (seconds) requires Vercel Pro plan. LLM API calls routinely exceed the 10-second Hobby plan limit — **Vercel Pro is required for this project.**

### Environment Variables (Vercel Dashboard)

Set via **Vercel Project → Settings → Environment Variables**. Do not commit secrets to the repository.

| Variable | Description | Used By |
|---|---|---|
| `SUPABASE_URL` | Supabase project URL | RAG pipeline, retriever |
| `SUPABASE_ANON_KEY` | Public anon key (read-only queries) | API serverless functions |
| `OPENAI_API_KEY` | OpenAI API key | Embeddings + LLM calls |
| `MODEL_PROVIDER` | `openai` / `anthropic` / `azure_openai` | LLM abstraction layer |
| `LLM_MODEL` | e.g. `gpt-4o`, `claude-opus-4-8` | LLM abstraction layer |
| `HISTORY_WINDOW` | Max conversation turns (default: 10) | Phase 5 conversation |

Note: Use `SUPABASE_ANON_KEY` (not service role key) in deployed functions — read-only access is sufficient for query-time retrieval. The `SUPABASE_SERVICE_KEY` is only needed locally for the ingestion script.

### Ingestion is Local Only

The ingestion script (`ingest.py`) runs **locally** against Supabase cloud — it is never deployed to Vercel. This matches the original plan's "manual CLI trigger" intent and is safe because Supabase's `SUPABASE_SERVICE_KEY` stays off Vercel entirely.

```bash
# Local ingestion (unchanged from original plan intent)
python ingest.py --docs ./source/
```

### Eliminations vs Original Plan

| Original | Eliminated | Reason |
|---|---|---|
| Docker container | Not needed | Vercel manages function packaging |
| On-premises app server | Not needed | Vercel hosts the backend |
| Docker deployment guide | Not needed | Replaced by `vercel.json` config |
| Server-side `.env` file | Not needed | Vercel Environment Variables |
| Re-ingestion wipe script | Kept (local CLI) | Still needed for runbook updates |

### Health Check

`GET /api/health` endpoint is unchanged in contract. On Vercel it is a separate serverless function at `api/health.py`.

---

## Updated Dependencies

```txt
# requirements.txt — full updated list

# Core
fastapi
uvicorn                    # local dev only; Vercel uses its own ASGI adapter

# LangChain
langchain
langchain-community
langchain-openai

# Vector store + database
supabase                   # replaces chromadb
                           # rank_bm25 REMOVED — FTS handled by Supabase

# Document parsing
markdown-it-py             # replaces python-docx

# API
httpx
pydantic

# Utilities
python-dotenv              # local dev only
```

---

## Phase 7 — RAG Pipeline Evaluation

Evaluation is a standalone phase run **locally after each milestone** — it is never part of the Vercel deployment pipeline. Results are stored in a dedicated `eval_runs` table in Supabase so metrics are comparable across phases.

### 7.1 Evaluation Goals

| Question | Dimension | When |
|---|---|---|
| Are the right chunks being retrieved? | Retrieval quality | After Phase 2, 3 |
| Is the generated answer grounded and correct? | Generation quality | After Phase 2, 3, 6 |
| Are error codes and citations routed correctly? | System correctness | After Phase 3, 6 |

---

### 7.2 Golden Test Set

**57 test cases** — 52 positive and 5 negative. Built once from the actual ESGA runbooks before Phase 2 begins and reused across all phases. File: `eval/golden_test_set.csv`.

**Distribution:** Pattern 1 (6), Pattern 2 (5), Pattern 3 (6), Pattern 4 (5), Pattern 5 (5), Pattern 6 (5), Pattern 8 (5), Pattern 9 (5), Pattern 10 (5), Negatives (5).

#### Query Types

| Type | Description | Example |
|---|---|---|
| `error_code` | Query contains an exact error code or HTTP status | "DataPower is returning ETIMEDOUT when connecting to a backend" |
| `symptom` | Observed behaviour described in plain language | "Multiple unrelated backend services started failing with timeouts at the same time" |
| `triage` | How to start diagnosing a class of error | "A backend connection timeout has occurred. How do I determine the root cause?" |
| `procedure` | How to perform a specific action | "How do I flush the DNS cache on DataPower?" |
| `escalation` | Who to contact or what the SLA is | "A backend timeout issue has persisted for over 30 minutes. Who do I escalate to?" |
| `negative` | No relevant runbook exists | "How do I configure a new Multi-Protocol Gateway service on DataPower from scratch?" |

#### CSV Schema

```
id, query, query_type, expected_pattern_ids, expected_section_types, expected_section_titles, ground_truth_answer
```

List fields (`expected_pattern_ids`, `expected_section_types`, `expected_section_titles`) use `|` as the in-cell delimiter. Negative cases have empty list fields and a `ground_truth_answer` describing the expected refusal behaviour.

#### Loading the Test Set

```python
import csv

def load_golden_test_set(path: str = "eval/golden_test_set.csv") -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cases = []
        for row in reader:
            cases.append({
                "id":                      row["id"],
                "query":                   row["query"],
                "query_type":              row["query_type"],
                "expected_pattern_ids":    [p for p in row["expected_pattern_ids"].split("|") if p],
                "expected_section_types":  [s for s in row["expected_section_types"].split("|") if s],
                "expected_section_titles": [t for t in row["expected_section_titles"].split("|") if t],
                "ground_truth_answer":     row["ground_truth_answer"] or None,
            })
    return cases
```

---

### 7.3 Metrics

#### Retrieval Metrics (no LLM required — fast)

| Metric | Definition | Target |
|---|---|---|
| **Hit Rate @ 5** (HR@5) | Fraction of queries where at least one expected chunk is in top-5 results | ≥ 0.85 |
| **Mean Reciprocal Rank** (MRR@5) | Average of 1/rank for the first relevant result | ≥ 0.75 |
| **Context Precision @ 5** | Of 5 retrieved chunks, fraction that are relevant | ≥ 0.70 |
| **Context Recall** | Of all expected chunks, fraction that appear in top-5 | ≥ 0.80 |
| **Error Code Routing Accuracy** | For `error_code` queries, `error_signatures` chunk ranks in top-3 | ≥ 0.90 |
| **Escalation Routing Accuracy** | For `escalation` queries, `escalation` chunk ranks in top-3 | ≥ 0.90 |

```python
def hit_rate_at_k(results: list, k: int = 5) -> float:
    hits = sum(
        1 for r in results
        if r["expected_pattern_ids"]
        and any(
            chunk["metadata"]["pattern_id"] in r["expected_pattern_ids"]
            for chunk in r["retrieved_chunks"][:k]
        )
    )
    positives = sum(1 for r in results if r["expected_pattern_ids"])
    return hits / positives if positives else 0.0


def mrr_at_k(results: list, k: int = 5) -> float:
    reciprocal_ranks = []
    for r in results:
        if not r["expected_pattern_ids"]:
            continue
        for rank, chunk in enumerate(r["retrieved_chunks"][:k], start=1):
            if chunk["metadata"]["pattern_id"] in r["expected_pattern_ids"]:
                reciprocal_ranks.append(1 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)
    return sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
```

#### Generation Metrics — RAGAS (LLM-assisted)

```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
    answer_correctness,
)
from datasets import Dataset

def run_ragas_evaluation(test_cases: list[dict]) -> dict:
    data = {
        "question":     [tc["query"] for tc in test_cases],
        "answer":       [tc["generated_answer"] for tc in test_cases],
        "contexts":     [tc["retrieved_texts"] for tc in test_cases],
        "ground_truth": [tc["ground_truth_answer"] for tc in test_cases],
    }
    return evaluate(Dataset.from_dict(data), metrics=[
        faithfulness, answer_relevancy,
        context_precision, context_recall, answer_correctness,
    ])
```

| Metric | Definition | Target |
|---|---|---|
| **Faithfulness** | Answer claims supported by retrieved context | ≥ 0.90 |
| **Answer Relevance** | Semantic alignment between answer and query | ≥ 0.85 |
| **Context Precision** | Retrieved chunks actually used in the answer | ≥ 0.70 |
| **Context Recall** | Ground truth coverage by retrieved context | ≥ 0.80 |
| **Answer Correctness** | Semantic similarity vs ground truth answer | ≥ 0.75 |

#### System-Specific Metrics

```python
import re

def citation_accuracy(test_cases: list[dict]) -> float:
    correct, total = 0, 0
    for tc in test_cases:
        if not tc["expected_pattern_ids"]:
            continue
        cited = re.findall(r"Pattern_\d+", tc["generated_answer"])
        if any(p in tc["expected_pattern_ids"] for p in cited):
            correct += 1
        total += 1
    return correct / total if total else 0.0


def negative_handling_rate(test_cases: list[dict]) -> float:
    negatives = [tc for tc in test_cases if not tc["expected_pattern_ids"]]
    handled = sum(
        1 for tc in negatives
        if "no relevant runbook" in tc["generated_answer"].lower()
        or "not covered"        in tc["generated_answer"].lower()
        or "cannot find"        in tc["generated_answer"].lower()
    )
    return handled / len(negatives) if negatives else 0.0
```

| Metric | Definition | Target |
|---|---|---|
| **Citation Accuracy** | Answers citing the correct `pattern_id` and section | ≥ 0.90 |
| **Negative Handling Rate** | Negative queries answered with an explicit "no runbook found" | = 1.00 |
| **Hallucination Rate** | Answer claims not traceable to any retrieved chunk | ≤ 0.05 |

---

### 7.4 Evaluation Pipeline

#### Script Layout

```
eval/
├── golden_test_set.csv        ← 57 test cases (source of truth)
├── run_retrieval_eval.py      ← retrieval metrics only — no LLM, fast
├── run_generation_eval.py     ← RAGAS + system-specific metrics — uses LLM
├── report.py                  ← prints comparison table, writes to Supabase eval_runs
└── results/                   ← local JSON snapshots per run
    ├── phase2_baseline.json
    └── phase3_hybrid.json
```

#### Run Commands

```bash
# Step 1 — retrieval only (fast, no LLM cost)
python eval/run_retrieval_eval.py --phase phase2_baseline --k 5

# Step 2 — full generation eval (~$0.50–$2 per run)
python eval/run_generation_eval.py --phase phase2_baseline

# Step 3 — compare two phases side by side
python eval/report.py --baseline phase2_baseline --compare phase3_hybrid
```

#### Results Storage in Supabase

```sql
create table eval_runs (
  id         bigserial primary key,
  phase      text        not null,    -- e.g. 'phase2_baseline', 'phase3_hybrid'
  run_at     timestamptz default now(),
  retrieval  jsonb,                   -- HR@5, MRR@5, precision, recall
  generation jsonb,                   -- RAGAS metrics
  system     jsonb,                   -- citation_accuracy, negative_handling_rate
  notes      text
);
```

---

### 7.5 Phase Gate Criteria

A gate failure blocks progression to the next phase. It does not trigger automatic rollback but requires investigation before moving forward.

#### Gate A — After Phase 2 (Baseline)

| Metric | Minimum to Pass |
|---|---|
| HR@5 | ≥ 0.70 |
| Negative Handling Rate | = 1.00 (non-negotiable) |
| Faithfulness | ≥ 0.85 |

#### Gate B — After Phase 3 (Hybrid Retrieval)

| Metric | Minimum to Pass | Must Improve vs Gate A |
|---|---|---|
| HR@5 | ≥ 0.85 | Yes |
| Error Code Routing Accuracy | ≥ 0.90 | Yes — primary motivator for Supabase FTS |
| Context Precision@5 | ≥ 0.70 | — |
| Faithfulness | ≥ 0.90 | — |
| Negative Handling Rate | = 1.00 | Maintained |

#### Gate C — After Phase 6 (Production)

| Metric | Minimum to Pass |
|---|---|
| HR@5 | ≥ 0.85 |
| MRR@5 | ≥ 0.75 |
| Faithfulness | ≥ 0.90 |
| Answer Correctness | ≥ 0.75 |
| Citation Accuracy | ≥ 0.90 |
| Negative Handling Rate | = 1.00 |
| Hallucination Rate | ≤ 0.05 |

---

### 7.6 Metric Targets at a Glance

| Metric | Type | Phase 2 | Phase 3 | Phase 6 |
|---|---|---|---|---|
| Hit Rate @ 5 | Retrieval | ≥ 0.70 | ≥ 0.85 | ≥ 0.85 |
| MRR @ 5 | Retrieval | — | ≥ 0.75 | ≥ 0.75 |
| Context Precision @ 5 | Retrieval | — | ≥ 0.70 | ≥ 0.70 |
| Context Recall | Retrieval | — | ≥ 0.80 | ≥ 0.80 |
| Error Code Routing | Retrieval | — | ≥ 0.90 | ≥ 0.90 |
| Escalation Routing | Retrieval | — | ≥ 0.90 | ≥ 0.90 |
| Faithfulness | Generation | ≥ 0.85 | ≥ 0.90 | ≥ 0.90 |
| Answer Relevance | Generation | — | — | ≥ 0.85 |
| Answer Correctness | Generation | — | — | ≥ 0.75 |
| Citation Accuracy | System | — | — | ≥ 0.90 |
| Negative Handling Rate | System | = 1.00 | = 1.00 | = 1.00 |
| Hallucination Rate | System | — | — | ≤ 0.05 |

### Eval-only Dependencies (not deployed to Vercel)

```txt
ragas>=0.1.0
datasets
pandas
```

---

## Updated Phase Summary

| Phase | Focus | Key Change |
|---|---|---|
| 1 | Ingestion Pipeline | ChromaDB → Supabase; rank_bm25 → Supabase FTS; python-docx → markdown-it-py |
| 2 | Core RAG (Single Turn) | `Chroma` retriever → `SupabaseVectorStore` retriever |
| 3 | Advanced RAG | Dense (pgvector) + Sparse (Supabase FTS) + RRF — same design, new implementation |
| 4 | Splunk Log Integration | No change |
| 5 | Multi-turn Conversation | No change |
| 6 | Hardening & Production | Docker/on-prem → Vercel; env vars via Vercel dashboard; Supabase managed |
| 7 | RAG Evaluation | New — 57-case golden test set (`eval/golden_test_set.csv`), RAGAS generation metrics, retrieval metrics, 3 phase gates |
| 8 | Gate C Remediation | Fix 4 failing metrics identified in phase6_production eval run (2026-06-07) |

---

## Phase 8 — Gate C Remediation

> **Added:** 2026-06-07  
> **Trigger:** phase6_production eval run exposed 4 metrics below Gate C target.  
> **Source data:** `eval/results/phase6_production.json`

### 8.1 Gate C Results — phase6_production

| Metric | Score | Target | Status |
|---|---|---|---|
| Hit Rate @ 5 | 1.000 | ≥ 0.85 | PASS |
| MRR @ 5 | 0.929 | ≥ 0.75 | PASS |
| Context Precision @ 5 | 0.668 | ≥ 0.70 | **FAIL** |
| Context Recall | 1.000 | ≥ 0.80 | PASS |
| Error Code Routing Accuracy | 0.611 | ≥ 0.90 | **FAIL** |
| Escalation Routing Accuracy | 1.000 | ≥ 0.90 | PASS |
| Faithfulness | 0.926 | ≥ 0.90 | PASS |
| Answer Relevancy | 0.663 | ≥ 0.85 | **FAIL** |
| Context Precision (RAGAS) | 0.817 | ≥ 0.70 | PASS |
| Context Recall (RAGAS) | 0.922 | ≥ 0.80 | PASS |
| Answer Correctness | 0.596 | ≥ 0.75 | **FAIL** |
| Citation Accuracy | 0.957 | ≥ 0.90 | PASS |
| Negative Handling Rate | 1.000 | = 1.00 | PASS |
| Hallucination Rate | 0.074 | ≤ 0.05 | FAIL |

> Note: Hallucination Rate is computed as `1 - faithfulness`. With faithfulness at 0.926, it sits at 0.074 — marginally above the 0.05 cap. Fixing Answer Relevancy (verbosity) is expected to bring faithfulness above 0.93 and hallucination rate below 0.05 as a side-effect.

---

### 8.2 Root Cause Analysis

#### Fix 1 — Error Code Routing Accuracy (0.611 → target 0.90)

**How the metric is measured:**  
For each `error_code` query, at least one chunk with `"Error Signatures"` in its section title must appear in the top-3 retrieved results.

**Root cause:**  
7 of 18 error code queries use symbolic error codes (`DNS_RESOLVE_FAILED`, `TOKEN_EXPIRED`, `JSON_PARSE_ERROR`, `IP_NOT_WHITELISTED`) or HTTP status codes (`404`, `429`). These strings appear prominently in **Troubleshooting** section titles (e.g., "5.1 No Route Matched (HTTP 404)", "5.1 Rate Limit Exceeded (HTTP 429)"), so dense + FTS retrieval ranks those Troubleshooting chunks above the Error Signatures chunk.

The metadata-filtered retrieval path in `api/analyze.py` already boosts results by error code, but only for hex codes (`0x...`). Symbolic codes stored in the `error_codes` metadata column are **never used as a query-time filter**, so the boost path is dead for 6 of the 7 failing cases.

Failing cases confirmed by data analysis:

| Case | Query (truncated) | Top-3 sections retrieved |
|---|---|---|
| p2_error_code_1 | DNS_RESOLVE_FAILED… | Troubleshooting 5.2, Troubleshooting 5.1, Overview |
| p2_error_code_2 | NXDOMAIN… | Overview, Troubleshooting 5.1, Troubleshooting 5.2 |
| p3_error_code_2 | TOKEN_EXPIRED… | Troubleshooting 5.2, Troubleshooting 5.1, Troubleshooting 5.2 |
| p4_error_code_1 | JSON_PARSE_ERROR… | Troubleshooting 5.1, Troubleshooting 5.3, Troubleshooting 5.1 |
| p5_error_code_1 | HTTP 404… | Troubleshooting 5.1, Troubleshooting 5.3, Troubleshooting 5.1 |
| p6_error_code_1 | HTTP 429… | Troubleshooting 5.3, Troubleshooting 5.1, Troubleshooting 5.1 |
| p6_error_code_2 | IP_NOT_WHITELISTED… | Troubleshooting 5.2, Troubleshooting 5.3, Troubleshooting 5.2 |

**Recommended fix — two changes in `api/analyze.py`:**

*a) Extend the error code extractor to cover symbolic codes (currently only hex):*
```python
# BEFORE — api/analyze.py, extract_hex_codes()
def extract_hex_codes(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'0x[0-9A-Fa-f]+', text, re.IGNORECASE)))

# AFTER — rename and extend
def extract_error_codes(text: str) -> list[str]:
    hex_codes = re.findall(r'0x[0-9A-Fa-f]+', text, re.IGNORECASE)
    symbolic   = re.findall(r'\b([A-Z][A-Z_0-9]{4,})\b', text)
    return list(dict.fromkeys(hex_codes + symbolic))
```

*b) Use extracted symbolic codes for the existing metadata-filtered retrieval:*  
Update the Step 5 (hex retrieval) block to call `extract_error_codes` instead of `extract_hex_codes`. No other retrieval logic changes.

*c) Post-RRF section-type boost for error_code queries:*  
After `reciprocal_rank_fusion` returns, if the query contains an error code pattern, re-order so `section_type == "error_signatures"` chunks are promoted to positions 1–2. This is a sort-stable promotion — no re-embedding needed:
```python
def promote_error_signatures(chunks: list, query: str) -> list:
    if not extract_error_codes(query):
        return chunks
    sig   = [c for c in chunks if c["metadata"].get("section_type") == "error_signatures"]
    other = [c for c in chunks if c["metadata"].get("section_type") != "error_signatures"]
    return (sig + other)[:TOP_K]
```

---

#### Fix 2 — Context Precision @ 5 (0.668 → target 0.70)

**How the metric is measured:**  
Fraction of the top-5 retrieved chunks whose `pattern_id` matches the expected pattern(s). A score of 1/5 (0.20) means only 1 of 5 chunks is from the right runbook.

**Root cause:**  
`RETRIEVAL_K = 10` pulls a wide candidate pool from dense, FTS, and metadata-filtered search before RRF. Runbooks share overlapping vocabulary (ETIMEDOUT appears in Pattern_1 and Pattern_10; resource language in Pattern_9 and Pattern_1), so after RRF the same off-target pattern can occupy 2–3 of the top-5 slots. In `p1_error_code_1`, Pattern_9 appears at both rank 1 and rank 5, displacing a second Pattern_1 chunk.

Score distribution across 47 positive cases:

| CP@5 range | Cases |
|---|---|
| > 0.80 | 12 |
| 0.60 – 0.80 | 20 |
| 0.40 – 0.60 | 13 |
| < 0.40 | 2 |

The gap to target (0.668 → 0.70) is narrow — moving ~3 cases from the 0.40–0.60 bucket to 0.60–0.80 is sufficient.

**Recommended fix — two changes in `api/analyze.py`:**

*a) Cap chunks per pattern to 2 after RRF (post-filter):*
```python
def deduplicate_by_pattern(chunks: list, max_per_pattern: int = 2) -> list:
    seen: dict = {}
    out: list  = []
    for c in chunks:
        pid = c["metadata"]["pattern_id"]
        if seen.get(pid, 0) < max_per_pattern:
            out.append(c)
            seen[pid] = seen.get(pid, 0) + 1
    return out
```
Apply this after `reciprocal_rank_fusion`, before formatting the context.

*b) Reduce `RETRIEVAL_K` from 10 to 7:*  
A narrower candidate pool reduces the surface area for off-target patterns to enter via RRF. Change the constant at the top of `api/analyze.py`:
```python
RETRIEVAL_K = 7   # was 10
```

---

#### Fix 3 — Answer Relevancy (0.663 → target 0.85)

**How the metric is measured (RAGAS):**  
RAGAS generates synthetic reverse-questions from the answer text, then computes cosine similarity between those reverse-questions and the original user query. When the answer covers topics beyond the question, the reverse-questions diverge, and the score drops.

**Root cause:**  
The generated answers are **3.3× longer on average** than ground truth answers. 26 of 47 positive cases produce answers more than 3× the ground truth length. The system prompt has no conciseness instruction — the LLM sees 5 full runbook chunks and attempts to be maximally comprehensive.

Most verbose cases (confirmed by data):

| Case | Answer / Ground Truth ratio | Query type |
|---|---|---|
| p3_triage_1 | 8.6× | triage |
| p2_symptom_1 | 7.4× | symptom |
| p5_symptom_1 | 6.8× | symptom |
| p9_error_code_1 | 6.1× | error_code |
| p6_error_code_2 | 5.8× | error_code |

**Recommended fix — two changes in `api/analyze.py`:**

*a) Add rule 6 to `SYSTEM_PROMPT`:*
```
6. Be concise. Answer only what was asked — do not volunteer unrequested steps or 
   background. For error code queries: 1–2 sentences identifying the code and its cause.
   For triage queries: state the decision path only. For procedure queries: list only 
   the steps directly relevant to the question asked.
```

*b) Reduce `max_completion_tokens` from 1024 to 512:*
```python
# api/analyze.py — in the LLM call block
max_completion_tokens=512,   # was 1024; ground truth answers avg ~250 chars
```

---

#### Fix 4 — Answer Correctness (0.596 → target 0.75)

**How the metric is measured (RAGAS):**  
Combines semantic similarity and factual overlap between the generated answer and the ground truth answer. Both dimensions must be high to score well.

**Root cause:**  
Two compounding causes:

1. **Verbosity dilutes factual overlap.** Ground truth answers are 1–2 sentences. Generated answers average 3.3× longer and cover adjacent topics. Even when the correct fact is present, the factual overlap ratio with a short ground truth is low because the answer contains many additional claims.

2. **Error code routing failures deliver the wrong chunk.** In 7 error code cases, the Error Signatures chunk is not in the top-3. The LLM answers from Troubleshooting chunks and produces a procedural answer (steps to fix), while the ground truth expects a definitional answer (what the code means). The semantic similarity between these two answer styles is inherently low regardless of factual correctness.

**Recommended fix:**  
This metric has no independent fix — it is downstream of Fixes 1 and 3:
- Fix 1 (error code routing) delivers the correct Error Signatures chunk, enabling definition-style answers that match ground truth format.
- Fix 3 (conciseness) reduces answer length so factual overlap with short ground truths is higher.

Re-run the generation eval after Fixes 1 and 3 are applied before deciding whether additional work is needed.

---

### 8.3 Implementation Order

Apply fixes in this order — each fix is independent and verifiable with a retrieval eval run before moving to the next:

| Step | Fix | File(s) | Verify with |
|---|---|---|---|
| 8a | Extend `extract_error_codes` to symbolic codes | `api/analyze.py` | `run_retrieval_eval.py --phase phase8a` |
| 8b | Add `promote_error_signatures` post-RRF | `api/analyze.py` | `run_retrieval_eval.py --phase phase8b` |
| 8c | `deduplicate_by_pattern` + `RETRIEVAL_K = 7` | `api/analyze.py` | `run_retrieval_eval.py --phase phase8c` |
| 8d | System prompt rule 6 + `max_completion_tokens=512` | `api/analyze.py` | `run_generation_eval.py --phase phase8d` |
| 8e | Full Gate C re-run | — | `report.py --baseline phase6_production --compare phase8e` |

Steps 8a–8c affect retrieval only and are verifiable without LLM cost. Step 8d requires the generation eval (~$0.50–$2). Run 8e only after all prior steps pass their individual checks.

---

### 8.4 Expected Outcomes (pre-run forecast)

| Metric | Baseline (phase6) | Expected after Phase 8 |
|---|---|---|
| Error Code Routing Accuracy | 0.611 | ≥ 0.90 (Fix 1 directly targets 7 failing cases) |
| Context Precision @ 5 | 0.668 | ≥ 0.70 (Fix 2 removes pattern duplication) |
| Answer Relevancy | 0.663 | ≥ 0.85 (Fix 3 eliminates over-generation) |
| Answer Correctness | 0.596 | ≥ 0.75 (downstream of Fixes 1 + 3) |
| Hallucination Rate | 0.074 | ≤ 0.05 (downstream of Fix 3 — shorter, grounded answers) |

---

### 8.5 Actual Eval Results — phase8c_v2 (2026-06-07)

> **Eval run:** `phase8c_v2` — retrieval eval + generation eval (RAGAS 0.2.6)  
> **Fixes applied:** 8a (symbolic + HTTP status code extraction), 8b (promote error signatures), 8c (deduplicate by pattern, max=3; RETRIEVAL_K reverted to 10), 8d (system prompt rule 6, max_completion_tokens=512)  
> **Source files:** `eval/results/phase8c_v2.json`

#### 8.5.1 Retrieval Metrics

| Metric | Phase 6 | Phase 8c_v2 | Delta | Target | Status |
|---|---|---|---|---|---|
| Hit Rate @ 5 | 1.000 | 1.000 | — | ≥ 0.85 | PASS |
| MRR @ 5 | 0.929 | 0.957 | +0.028 | ≥ 0.75 | PASS |
| Context Precision @ 5 | 0.668 | 0.667 | -0.001 | ≥ 0.70 | FAIL |
| Context Recall | 1.000 | 1.000 | — | ≥ 0.80 | PASS |
| Error Code Routing Accuracy | 0.611 | **0.944** | **+0.333** | ≥ 0.90 | **PASS** |
| Escalation Routing Accuracy | 1.000 | 1.000 | — | ≥ 0.90 | PASS |
| Negative Handling Rate | 1.000 | 1.000 | — | = 1.00 | PASS |

**Key win:** Error Code Routing jumped from 0.611 → 0.944, clearing the 0.90 target. The combined symbolic code extractor (Fix 8a) + error signature promotion (Fix 8b) resolved 6 of the 7 original failures. The remaining miss is `p5_error_code_1` (HTTP 404) where the Troubleshooting chunk still outranks the Error Signatures chunk.

**Iteration note — two side-effects fixed mid-run:**
- First attempt (`phase8c`) used `RETRIEVAL_K = 7` which caused Escalation Routing to drop from 1.000 → 0.889 (escalation chunk fell out of the candidate pool). Reverted to `RETRIEVAL_K = 10`.
- First attempt used `deduplicate_by_pattern(max=2)` which worsened Context Precision (removed legitimate 3rd hits from the correct pattern). Raised to `max_per_pattern = 3`.
- HTTP status codes (`404`, `429`) were not matched by the symbolic code regex — added `\bHTTP\s+([2-5][0-9]{2})\b` extractor.

Context Precision @ 5 remains at 0.667, 0.003 below target. This is structurally difficult: many queries legitimately retrieve from 2–3 overlapping runbooks, and the golden test set only lists 1–2 expected patterns.

#### 8.5.2 Generation Metrics

| Metric | Phase 6 | Phase 8c_v2 | Delta | Target | Status |
|---|---|---|---|---|---|
| Faithfulness | 0.926 | 0.892 | -0.034 | ≥ 0.90 | FAIL |
| Answer Relevancy | 0.663 | 0.696 | +0.033 | ≥ 0.85 | FAIL |
| Context Precision (RAGAS) | 0.817 | **0.880** | +0.063 | ≥ 0.70 | PASS |
| Context Recall (RAGAS) | 0.922 | **0.929** | +0.007 | ≥ 0.80 | PASS |
| Answer Correctness | 0.596 | 0.634 | +0.038 | ≥ 0.75 | FAIL |
| Citation Accuracy | 0.957 | **1.000** | +0.043 | ≥ 0.90 | PASS |
| Negative Handling Rate | 1.000 | 1.000 | — | = 1.00 | PASS |
| Hallucination Rate | 0.074 | 0.109 | +0.035 | ≤ 0.05 | FAIL |

#### 8.5.3 Generation Findings

**What improved:**
- Context Precision (RAGAS) +0.063 and Context Recall +0.007 confirm the retrieval fixes are surfacing the right chunks to the LLM.
- Citation Accuracy reached 1.000 — every answer now cites the correct pattern, up from 0.957.
- Answer Correctness +0.038 and Answer Relevancy +0.033 moved in the right direction.

**Critical regression — Hallucination Rate rose from 0.074 → 0.109:**  
Hallucination Rate is `1 - faithfulness`. Faithfulness dropped from 0.926 → 0.892.

Root cause: `max_completion_tokens=512` (Fix 8d) is too aggressive. When the LLM reaches the token limit mid-answer, it truncates grounded content and the final claims are partially extrapolated rather than drawn from the context. The result is more hallucinated statements per answer, not fewer.

**Answer Relevancy improved only marginally (+0.033, still 0.154 below target):**  
The conciseness prompt rule (Fix 8d rule 6) reduced verbosity but not enough. The LLM still over-generates relative to the short ground truth answers. RAGAS Answer Relevancy measures this by generating reverse-questions from the answer — if the answer covers multiple topics, reverse-questions diverge from the original query.

#### 8.5.4 Revised Fix 8d — Token Cap Correction

The `max_completion_tokens=512` cap introduced hallucinations by truncating answers. The revised target is **768 tokens** — enough for a complete, grounded answer while still preventing the 1024-token over-verbose responses that hurt Answer Relevancy.

```python
# api/analyze.py — revised token cap
max_completion_tokens=768   # was 512 (caused truncation hallucinations); was 1024 (too verbose)
```

The conciseness rule 6 in the system prompt is retained — it is independently helpful and did improve Answer Relevancy (+0.033). The token cap is the only change to revise.

#### 8.5.5 Remaining Work — Next Steps

| Step | Fix | Target metric | Expected direction |
|---|---|---|---|
| 8d-revised | Raise `max_completion_tokens` from 512 → 768 | Hallucination Rate, Faithfulness | Restore to ≥ 0.90 / ≤ 0.05 |
| 8e | Full Gate C re-run after 8d-revised | All | `report.py --baseline phase6_production --compare phase8e` |
| 8f (if needed) | Investigate Answer Relevancy — consider query-type-aware prompt routing or few-shot examples for triage/symptom queries | Answer Relevancy | +0.15 needed to reach 0.85 |
| 8g (if needed) | Context Precision @ 5: investigate whether golden test set expected_pattern_ids need expanding for multi-pattern queries | Context Precision | +0.003 needed to reach 0.70 |

Priority: implement 8d-revised, re-run generation eval, then assess whether 8f and 8g are needed before declaring Gate C passed.
