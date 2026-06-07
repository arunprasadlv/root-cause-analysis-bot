# ESGA DataPower RCA Assistant

AI-powered incident troubleshooting chatbot for ESGA DataPower support engineers. Ask about an error code, describe a symptom, or ask who to escalate to — the assistant retrieves relevant sections from the official ESGA runbooks and generates grounded, cited recommendations.

---

## Architecture

```
User (browser)
      │
      ▼
FastAPI  (api/index.py)
      │
      ├── POST /api/analyze  (api/analyze.py)
      │         │
      │         ├── 1. Query reformulation  ─── OpenAI GPT (context resolution)
      │         │
      │         ├── 2. Dense retrieval  ──────── Supabase pgvector
      │         │                                text-embedding-3-small
      │         │
      │         ├── 3. Sparse retrieval  ─────── Supabase FTS (tsvector)
      │         │
      │         ├── 4. Error code retrieval  ─── Supabase pgvector
      │         │                                filtered by error_codes metadata
      │         │
      │         ├── 5. RRF merge + re-rank  ──── Reciprocal Rank Fusion
      │         │                                deduplicate_by_pattern
      │         │                                promote_error_signatures
      │         │
      │         └── 6. Answer generation  ─────── OpenAI GPT
      │                                           grounded to retrieved context
      │
      └── GET /eval-report  ──────────────────── Serves static HTML eval reports
                                                  from eval/reports/
```

**Knowledge base:** 9 ESGA runbooks (`source/`) parsed into ~79 chunks, indexed in Supabase with pgvector embeddings + full-text search. Ingestion is a local CLI step (`ingest.py`) — not part of the deployed app.

**Key design decisions:**
- Parser: `markdown-it-py` over `##`/`###` boundaries
- Chunking: `##` for all sections; `###` only within Section 5 (Diagnostic Procedures)
- Sections 3, 8, 9 excluded from indexing
- Chunk header `[Pattern Name | Section Title]` injected before embedding
- RRF merges dense + sparse + error-code-filtered results; max 3 chunks per pattern; error signature chunks promoted to top slots for error code queries

---

## Running the App

### Prerequisites

- Python 3.11+
- A Supabase project with pgvector enabled and the schema applied (`supabase/schema.sql`)
- An OpenAI API key

### 1. Clone and install

```bash
git clone <repo-url>
cd RootCauseAnalyzer-Chatbot
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your credentials:

```
SUPABASE_URL=https://<project-id>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
OPENAI_API_KEY=<your-openai-api-key>
```

### 3. Ingest runbooks (one-time)

This parses the runbooks in `source/`, creates embeddings, and loads them into Supabase. Only needed when runbook content changes.

```bash
python ingest.py
```

### 4. Start the server

```bash
python -m uvicorn api.index:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Vercel deployment

The project includes a `vercel.json` for serverless deployment. Set `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `OPENAI_API_KEY` as Vercel environment variables. The ingestion script is local-only and should not be deployed.

---

## Example Queries

**Error code lookup**
> What does error code `0x00d30003` indicate on DataPower?

The assistant identifies it as a backend connection timeout — the backend accepted the TCP connection but failed to respond within the configured timeout window, with a citation to Pattern 1 | Error Signatures.

---

**Symptom-based triage**
> DataPower is returning HTTP 503 errors. What should I check first?

The assistant walks through the triage decision path: check backend health, verify DNS resolution, inspect connection pool exhaustion indicators — each step cited to the relevant runbook section.

---

**Error code with resolution steps**
> We're seeing `DNS_RESOLVE_FAILED` errors. How do I fix this?

The assistant surfaces the Error Signatures section confirming the error code, then lists the diagnostic steps from the Diagnostic Procedures section for Pattern 2 (DNS Resolution Failure).

---

**Escalation query**
> Who do I escalate a certificate expiry incident to?

The assistant returns the escalation path from Pattern 8 (Certificate Expiration), including the contact group and the conditions that trigger escalation vs. self-resolution.

---

**Out-of-scope query**
> How do I configure a new API service on DataPower?

The assistant responds: *"No relevant runbook found for this query. The available runbooks cover incident troubleshooting patterns only."* — it does not hallucinate configuration steps.

---

## Eval Report

The assistant ships with a built-in evaluation report accessible at:

```
http://localhost:8000/eval-report
```

Or click the **📊 Eval Report** link in the top-right corner of the UI.

### What the report shows

The report covers two categories of metrics evaluated against a 57-case golden test set:

**Retrieval metrics** — does the system surface the right chunks?

| Metric | Description | Target |
|---|---|---|
| Hit Rate @ 5 | At least one expected chunk in top-5 | ≥ 0.85 |
| MRR @ 5 | Mean reciprocal rank of the first relevant hit | ≥ 0.75 |
| Context Precision @ 5 | Fraction of top-5 chunks from the expected pattern | ≥ 0.70 |
| Context Recall | Expected chunks present anywhere in results | ≥ 0.80 |
| Error Code Routing | Error Signatures chunk in top-3 for error code queries | ≥ 0.90 |
| Escalation Routing | Escalation chunk in top-3 for escalation queries | ≥ 0.90 |
| Negative Handling | No runbook returned for out-of-scope queries | = 1.00 |

**Generation metrics** — does the LLM answer correctly and faithfully?

| Metric | Description | Target |
|---|---|---|
| Faithfulness | Claims traceable to retrieved context (RAGAS) | ≥ 0.90 |
| Answer Relevancy | Answer directly addresses the question (RAGAS) | ≥ 0.85 |
| Context Precision | Retrieved context relevance (RAGAS) | ≥ 0.70 |
| Context Recall | Context coverage vs. ground truth (RAGAS) | ≥ 0.80 |
| Answer Correctness | Semantic similarity to ground truth answer (RAGAS) | ≥ 0.75 |
| Citation Accuracy | Every answer cites the correct pattern | ≥ 0.90 |
| Hallucination Rate | `1 - faithfulness` | ≤ 0.05 |

### Running an eval

```bash
# Retrieval eval
python eval/run_retrieval_eval.py --phase <phase-name>

# Generation eval (requires OpenAI key; --no-supabase uses cached retrieval results)
python eval/run_generation_eval.py --phase <phase-name> --no-supabase

# Generate HTML report from results JSON
python eval/generate_report.py --phase <phase-name>
```

Results are saved to `eval/results/<phase>.json`. HTML reports are saved to `eval/reports/<phase>.html` and served at `/eval-report?phase=<phase-name>`.

### Current results (phase8c_v2)

| Metric | Score | Target | Status |
|---|---|---|---|
| Hit Rate @ 5 | 1.000 | ≥ 0.85 | PASS |
| MRR @ 5 | 0.957 | ≥ 0.75 | PASS |
| Error Code Routing | 0.944 | ≥ 0.90 | PASS |
| Faithfulness | 0.892 | ≥ 0.90 | FAIL |
| Answer Relevancy | 0.696 | ≥ 0.85 | FAIL |
| Answer Correctness | 0.634 | ≥ 0.75 | FAIL |
| Citation Accuracy | 1.000 | ≥ 0.90 | PASS |
| Hallucination Rate | 0.109 | ≤ 0.05 | FAIL |

Remaining failures are tracked in `instructions/RCA_Implementation_Plan_remediation_fix.md`.

---

## Project Structure

```
api/
  index.py          — FastAPI app, serves frontend + eval report
  analyze.py        — /api/analyze endpoint (retrieval + generation)
  health.py         — /api/health endpoint
eval/
  golden_test_set.csv         — 57-case ground truth for evaluation
  run_retrieval_eval.py       — retrieval metrics runner
  run_generation_eval.py      — generation metrics runner (RAGAS)
  generate_report.py          — HTML report generator
  results/                    — JSON results per eval phase
  reports/                    — generated HTML reports
frontend/
  index.html        — single-page chat UI
source/
  ESGA_Pattern_*.md — 9 runbook Markdown files (the knowledge base)
supabase/
  schema.sql        — pgvector + FTS table definitions
ingest.py           — local ingestion CLI (parse → embed → upsert)
```
