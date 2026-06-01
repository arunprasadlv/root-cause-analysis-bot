# Phase 7 — RAG Pipeline Evaluation Strategy

> **Amendment to:** RCA_Implementation_Plan.pdf (v1.0, 26 May 2026)  
> **Updated:** 2026-05-31  
> **Scope:** Evaluation strategy for retrieval quality, generation quality, and system-specific correctness

---

## 1. Evaluation Goals

The evaluation strategy answers three questions at each phase gate:

| Question | Dimension | When |
|---|---|---|
| Are the right chunks being retrieved? | Retrieval quality | After Phase 2, 3 |
| Is the generated answer grounded and correct? | Generation quality | After Phase 2, 3, 6 |
| Are error codes and citations routed correctly? | System correctness | After Phase 3, 6 |

Evaluation runs are **never automated in the deployment pipeline** — they are manual, triggered locally after each phase milestone. Results are stored in a dedicated Supabase table to track improvement across phases.

---

## 2. Golden Test Set

### 2.1 Design Principles

- One test set is built once before Phase 2 begins and reused across all phases
- Queries are written from the perspective of a support engineer describing a live incident — not a researcher looking up documentation
- Each test case has a known correct retrieval target (pattern + section) and a reference answer drawn directly from the runbook
- The test set includes **negative cases** — queries that have no matching runbook — to measure hallucination resistance

### 2.2 Query Types

Six query types are defined to cover the full range of operator inputs:

| Type | Description | Example |
|---|---|---|
| `error_code` | Query contains an exact error code or HTTP status | "We're seeing `JWT_SIGNATURE_INVALID` in the logs" |
| `symptom` | Query describes observed behaviour in plain language | "Backend keeps timing out during peak hours" |
| `triage` | Operator asks how to start diagnosing a class of error | "Getting 503s across multiple services, where do I start?" |
| `procedure` | Operator asks how to perform a specific action | "How do I flush the DNS cache on DataPower?" |
| `escalation` | Operator asks who to contact or what the SLA is | "LDAP server is down affecting all users, who do I call?" |
| `negative` | No relevant runbook exists for this query | "How do I configure a DataPower XML Firewall?" |

### 2.3 Test Cases

**57 test cases total** — 52 positive (error_code, symptom, triage, procedure, escalation) and 5 negative.

**File:** `eval/golden_test_set.csv`

Distribution: Pattern 1 (6), Pattern 2 (5), Pattern 3 (6), Pattern 4 (5), Pattern 5 (5), Pattern 6 (5), Pattern 8 (5), Pattern 9 (5), Pattern 10 (5), Negatives (5).

**Loading the test set:**

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

List fields (`expected_pattern_ids`, `expected_section_types`, `expected_section_titles`) use `|` as the delimiter within the CSV cell. Empty cells for negative cases parse to empty lists. `ground_truth_answer` is `None` for negative cases.

#### Representative examples from the test set (see CSV for complete set)

```python
# eval/golden_test_set.csv — sample rows shown as dicts for readability
SAMPLE_CASES = [

    # Pattern 1 — Backend Connection Timeout
    {
        "id": "p1_symptom_1",
        "query": "Multiple backends started failing at the same time with timeouts",
        "query_type": "symptom",
        "expected_pattern_ids": ["Pattern_1", "Pattern_10"],
        "expected_section_types": ["troubleshooting"],
        "expected_section_titles": ["5.1 All Backends Timing Out"],
        "ground_truth_answer": (
            "When multiple unrelated backends timeout simultaneously, the likely cause "
            "is a network or infrastructure issue rather than a backend problem. Check "
            "for recent network change records in ServiceNow, verify DataPower network "
            "interfaces are UP, and engage the Network team with the failure timestamp."
        ),
    },
    {
        "id": "p1_error_code_1",
        "query": "DataPower error code 0x00d30003",
        "query_type": "error_code",
        "expected_pattern_ids": ["Pattern_1"],
        "expected_section_types": ["error_signatures"],
        "expected_section_titles": ["Error Signatures"],
        "ground_truth_answer": "Error 0x00d30003 means 'Connection timed out' — the backend was unresponsive within the timeout threshold.",
    },
    {
        "id": "p1_escalation_1",
        "query": "Backend timeout issue has persisted for over 30 minutes, who do I escalate to?",
        "query_type": "escalation",
        "expected_pattern_ids": ["Pattern_1"],
        "expected_section_types": ["escalation"],
        "expected_section_titles": ["Escalation Matrix"],
        "ground_truth_answer": "If the issue persists more than 30 minutes, escalate to the DataPower SME Team with a 15-minute response SLA.",
    },

    # Pattern 2 — DNS Resolution Failure
    {
        "id": "p2_symptom_1",
        "query": "Backend connection failing after server migration, worked yesterday",
        "query_type": "symptom",
        "expected_pattern_ids": ["Pattern_2"],
        "expected_section_types": ["troubleshooting"],
        "expected_section_titles": ["5.1 DNS Record Missing or Incorrect"],
        "ground_truth_answer": (
            "After a backend migration, DNS resolution may fail if the hostname was renamed. "
            "Run nslookup from an ops workstation to confirm if the record exists. "
            "Check ServiceNow for recent backend migration change records and contact "
            "the DNS team to verify the A/CNAME record status."
        ),
    },
    {
        "id": "p2_error_code_1",
        "query": "Getting NXDOMAIN error when DataPower tries to reach the backend",
        "query_type": "error_code",
        "expected_pattern_ids": ["Pattern_2"],
        "expected_section_types": ["error_signatures", "troubleshooting"],
        "expected_section_titles": ["Error Signatures", "5.1 DNS Record Missing or Incorrect"],
        "ground_truth_answer": "NXDOMAIN means the hostname does not exist in DNS — the record was deleted, renamed, or misspelled.",
    },

    # Pattern 3 — Auth/AuthZ Failures
    {
        "id": "p3_error_code_1",
        "query": "JWT_SIGNATURE_INVALID after key rotation",
        "query_type": "error_code",
        "expected_pattern_ids": ["Pattern_3"],
        "expected_section_types": ["troubleshooting"],
        "expected_section_titles": ["5.1 JWT Signature / Validation Failure"],
        "ground_truth_answer": (
            "After a key rotation event, DataPower's Crypto Key object must be updated "
            "to reference the new public key. Decode the JWT header to find the 'kid' "
            "field and confirm the key alias in DataPower matches it. Upload the new "
            "public key to Crypto Certificate and update the AAA Policy JWT Validation."
        ),
    },
    {
        "id": "p3_triage_1",
        "query": "Client getting 403 Forbidden but authentication appears to succeed",
        "query_type": "triage",
        "expected_pattern_ids": ["Pattern_3"],
        "expected_section_types": ["troubleshooting", "triage"],
        "expected_section_titles": ["5.4 Authorization Policy Denial", "Triage Decision Tree"],
        "ground_truth_answer": (
            "A 403 after successful authentication is an authorization failure. "
            "Check the AAA Authorization policy for the failing endpoint's role-to-resource mapping. "
            "Verify the client token carries the required OAuth scope or role claim."
        ),
    },

    # Pattern 6 — Security Policy Violations
    {
        "id": "p6_error_code_1",
        "query": "HTTP 429 Too Many Requests from a specific API consumer",
        "query_type": "error_code",
        "expected_pattern_ids": ["Pattern_6"],
        "expected_section_types": ["troubleshooting"],
        "expected_section_titles": ["5.1 Rate Limit Exceeded"],
        "ground_truth_answer": (
            "HTTP 429 indicates the consumer has exceeded their rate limit quota. "
            "Identify the consumer via the API key or client ID in request logs. "
            "Review the current Rate Limit Policy limits against the consumer's "
            "legitimate traffic volume. If the limit is appropriate, notify the client "
            "team. If adjustment is needed, raise a change request with API governance."
        ),
    },

    # Pattern 9 — Resource Exhaustion
    {
        "id": "p9_symptom_1",
        "query": "All services are slow and returning 503, not just one backend",
        "query_type": "symptom",
        "expected_pattern_ids": ["Pattern_9"],
        "expected_section_types": ["troubleshooting", "triage"],
        "expected_section_titles": ["Triage Decision Tree"],
        "ground_truth_answer": (
            "When all services are degraded simultaneously, suspect DataPower resource "
            "exhaustion rather than a backend issue. Check the DataPower System Dashboard "
            "for CPU, memory, and thread pool usage. High CPU (>85%), memory exhaustion, "
            "or thread pool full are the three primary causes."
        ),
    },

    # Negative cases
    {
        "id": "neg_1",
        "query": "How do I configure a DataPower XML Firewall policy from scratch?",
        "query_type": "negative",
        "expected_pattern_ids": [],
        "expected_section_types": [],
        "expected_section_titles": [],
        "ground_truth_answer": None,  # system should acknowledge no relevant runbook found
    },
    {
        "id": "neg_2",
        "query": "What is the process for onboarding a new API onto the gateway?",
        "query_type": "negative",
        "expected_pattern_ids": [],
        "expected_section_types": [],
        "expected_section_titles": [],
        "ground_truth_answer": None,
    },
]
```

---

## 3. Metrics

### 3.1 Retrieval Metrics

Computed against the golden test set without involving the LLM.

| Metric | Definition | Target |
|---|---|---|
| **Hit Rate @ k** (HR@k) | Fraction of queries where at least one expected chunk appears in top-k results | HR@5 ≥ 0.85 |
| **Mean Reciprocal Rank** (MRR@k) | Average of 1/rank for the first relevant result across all queries | MRR@5 ≥ 0.75 |
| **Context Precision @ k** | Of the k retrieved chunks, fraction that are relevant to the query | ≥ 0.70 |
| **Context Recall** | Of all expected chunks for a query, fraction that appear in top-k results | ≥ 0.80 |
| **Error Code Routing Accuracy** | For `error_code` queries, fraction where the `error_signatures` chunk ranks in top-3 | ≥ 0.90 |
| **Escalation Routing Accuracy** | For `escalation` queries, fraction where an `escalation` section chunk ranks in top-3 | ≥ 0.90 |

```python
def hit_rate_at_k(results: list[RetrievalResult], k: int = 5) -> float:
    hits = sum(
        1 for r in results
        if any(
            chunk.metadata["pattern_id"] in r.expected_pattern_ids
            for chunk in r.retrieved_chunks[:k]
        )
        if r.expected_pattern_ids  # skip negatives
    )
    positives = sum(1 for r in results if r.expected_pattern_ids)
    return hits / positives if positives else 0.0


def mrr_at_k(results: list[RetrievalResult], k: int = 5) -> float:
    reciprocal_ranks = []
    for r in results:
        if not r.expected_pattern_ids:
            continue
        for rank, chunk in enumerate(r.retrieved_chunks[:k], start=1):
            if chunk.metadata["pattern_id"] in r.expected_pattern_ids:
                reciprocal_ranks.append(1 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)
    return sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
```

### 3.2 Generation Metrics (RAGAS)

Computed using [RAGAS](https://docs.ragas.io/) — an LLM-assisted evaluation framework that scores answers without requiring exact string matches.

| Metric | Definition | Target |
|---|---|---|
| **Faithfulness** | Fraction of answer claims that are supported by retrieved context | ≥ 0.90 |
| **Answer Relevance** | Semantic alignment between the answer and the query | ≥ 0.85 |
| **Context Precision** | Retrieved chunks that were actually used to generate the answer | ≥ 0.70 |
| **Context Recall** | Ground truth answer coverage by the retrieved context | ≥ 0.80 |
| **Answer Correctness** | Semantic similarity between generated answer and ground truth | ≥ 0.75 |

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
        "question":   [tc["query"] for tc in test_cases],
        "answer":     [tc["generated_answer"] for tc in test_cases],
        "contexts":   [tc["retrieved_texts"] for tc in test_cases],
        "ground_truth": [tc["ground_truth_answer"] for tc in test_cases],
    }
    dataset = Dataset.from_dict(data)
    result = evaluate(
        dataset,
        metrics=[
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
            answer_correctness,
        ],
    )
    return result
```

### 3.3 System-Specific Metrics

| Metric | Definition | Target |
|---|---|---|
| **Citation Accuracy** | Fraction of answers where cited `pattern_id` and `section_title` match retrieved source | ≥ 0.90 |
| **Negative Handling Rate** | Fraction of negative-case queries where the system explicitly states no relevant runbook was found (no hallucination) | 1.00 |
| **Hallucination Rate** | Fraction of answer claims not traceable to any retrieved chunk | ≤ 0.05 |

```python
import re

def citation_accuracy(test_cases: list[dict]) -> float:
    correct = 0
    total = 0
    for tc in test_cases:
        if not tc["expected_pattern_ids"]:
            continue
        cited_patterns = re.findall(r"Pattern_\d+", tc["generated_answer"])
        if any(p in tc["expected_pattern_ids"] for p in cited_patterns):
            correct += 1
        total += 1
    return correct / total if total else 0.0


def negative_handling_rate(test_cases: list[dict]) -> float:
    negatives = [tc for tc in test_cases if not tc["expected_pattern_ids"]]
    handled = sum(
        1 for tc in negatives
        if "no relevant runbook" in tc["generated_answer"].lower()
        or "not covered" in tc["generated_answer"].lower()
        or "cannot find" in tc["generated_answer"].lower()
    )
    return handled / len(negatives) if negatives else 0.0
```

---

## 4. Evaluation Pipeline

### 4.1 Scripts

```
eval/
├── golden_test_set.py     ← test case definitions (from Section 2.3)
├── run_retrieval_eval.py  ← retrieval metrics only (no LLM, fast)
├── run_generation_eval.py ← RAGAS + system-specific metrics (LLM, slower)
├── report.py              ← prints summary table + writes to Supabase
└── results/               ← local JSON snapshots per run
    ├── phase2_baseline.json
    └── phase3_hybrid.json
```

### 4.2 Run Commands

```bash
# Step 1 — retrieval only (fast, no LLM cost)
python eval/run_retrieval_eval.py --phase phase3_hybrid --k 5

# Step 2 — full generation eval (uses LLM, ~$0.50–$2 per run)
python eval/run_generation_eval.py --phase phase3_hybrid

# Step 3 — print comparison report
python eval/report.py --baseline phase2_baseline --compare phase3_hybrid
```

### 4.3 Results Storage in Supabase

Results are written to a dedicated `eval_runs` table so all phases are comparable.

```sql
create table eval_runs (
  id           bigserial primary key,
  phase        text        not null,    -- e.g. 'phase2_baseline', 'phase3_hybrid'
  run_at       timestamptz default now(),
  retrieval    jsonb,                   -- HR@5, MRR@5, precision, recall
  generation   jsonb,                   -- RAGAS metrics
  system       jsonb,                   -- citation_accuracy, negative_handling_rate
  notes        text
);
```

---

## 5. Phase Gate Criteria

Evaluation must pass the following gates before the next phase begins. A gate failure blocks progression — it does not trigger automatic rollback, but requires investigation before moving forward.

### Gate A — After Phase 2 (Baseline)

Purpose: establish baseline before advanced retrieval is added.

| Metric | Minimum to Pass |
|---|---|
| HR@5 | ≥ 0.70 (baseline — lower threshold before hybrid) |
| Negative Handling Rate | = 1.00 (zero hallucination on negatives, non-negotiable) |
| Faithfulness | ≥ 0.85 |

### Gate B — After Phase 3 (Hybrid Retrieval)

Purpose: confirm hybrid retrieval improves over baseline — justify the added complexity.

| Metric | Minimum to Pass | Must Improve vs Phase 2 |
|---|---|---|
| HR@5 | ≥ 0.85 | Yes — must be higher than Gate A result |
| Error Code Routing Accuracy | ≥ 0.90 | Yes — this is the primary motivator for BM25/FTS |
| Context Precision@5 | ≥ 0.70 | — |
| Faithfulness | ≥ 0.90 | — |
| Negative Handling Rate | = 1.00 | Maintained |

### Gate C — After Phase 6 (Production Readiness)

Purpose: final acceptance test before live deployment.

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

## 6. Updated Dependencies

Add to `requirements.txt` for the `eval/` scripts only. These are dev/eval dependencies — not deployed to Vercel.

```txt
# eval dependencies (not in production requirements.txt)
ragas>=0.1.0
datasets
pandas
```

---

## 7. Evaluation Summary Table

| Metric | Type | Phase 2 Target | Phase 3 Target | Phase 6 Target |
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
