"""
Generation evaluation — Phase 7.

Reads retrieval results from eval/results/{phase}.json (written by run_retrieval_eval.py),
fetches chunk content from Supabase to build RAGAS contexts, runs RAGAS generation metrics,
and computes system-specific metrics (Citation Accuracy, NHR, Hallucination Rate).

Updates eval/results/{phase}.json with generation + system metrics and writes a
combined row to Supabase eval_runs.

Requires:
    pip install ragas datasets pandas

Usage:
    python eval/run_generation_eval.py --phase phase3_hybrid [--no-supabase]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RESULTS_DIR = Path("eval/results")
NEGATIVE_PHRASES = ["no relevant runbook", "not covered", "cannot find"]

GATE_C_GENERATION = {
    "faithfulness":       (0.90, True),
    "answer_relevancy":   (0.85, True),
    "context_precision":  (0.70, True),
    "context_recall":     (0.80, True),
    "answer_correctness": (0.75, True),
}

GATE_C_SYSTEM = {
    "citation_accuracy":       (0.90, True),
    "negative_handling_rate":  (1.00, True),
    "hallucination_rate":      (0.05, False),  # lower is better
}

GATE_C_LABELS = {
    "faithfulness":           "Faithfulness",
    "answer_relevancy":       "Answer Relevance",
    "context_precision":      "Context Precision",
    "context_recall":         "Context Recall",
    "answer_correctness":     "Answer Correctness",
    "citation_accuracy":      "Citation Accuracy",
    "negative_handling_rate": "Negative Handling Rate",
    "hallucination_rate":     "Hallucination Rate",
}


# ── RAGAS import (supports 0.1.x and 0.2.x) ──────────────────────────────────

def _try_import_ragas():
    """Returns (evaluate_fn, metrics_list, api_version) or raises ImportError."""
    # Try 0.2.x / 0.4.x first (use collections path if available to avoid deprecation warnings)
    try:
        from ragas import evaluate, EvaluationDataset, SingleTurnSample  # noqa: F401
        try:
            from ragas.metrics.collections import (
                Faithfulness, ResponseRelevancy,
                LLMContextPrecisionWithReference, LLMContextRecall, AnswerCorrectness,
            )
        except ImportError:
            from ragas.metrics import (  # type: ignore[no-redef]
                Faithfulness, ResponseRelevancy,
                LLMContextPrecisionWithReference, LLMContextRecall, AnswerCorrectness,
            )
        return "v2", {
            "faithfulness":       Faithfulness(),
            "answer_relevancy":   ResponseRelevancy(),
            "context_precision":  LLMContextPrecisionWithReference(),
            "context_recall":     LLMContextRecall(),
            "answer_correctness": AnswerCorrectness(),
        }
    except (ImportError, Exception):
        pass

    # Fall back to 0.1.x
    try:
        from ragas import evaluate  # noqa: F401
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
            answer_correctness,
        )
        from datasets import Dataset  # noqa: F401
        return "v1", {
            "faithfulness":       faithfulness,
            "answer_relevancy":   answer_relevancy,
            "context_precision":  context_precision,
            "context_recall":     context_recall,
            "answer_correctness": answer_correctness,
        }
    except ImportError:
        raise ImportError("ragas not installed. Run: pip install 'ragas>=0.1.0' datasets")


def _ragas_llm_and_embeddings():
    """Build LangChain-wrapped LLM and embeddings using the project's configured model."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    model = os.environ.get("LLM_MODEL", "gpt-5.4-mini")
    llm = LangchainLLMWrapper(ChatOpenAI(model=model, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model="text-embedding-3-small"))
    return llm, embeddings


def run_ragas_v2(positives: list[dict], metrics: dict) -> dict:
    from ragas import evaluate, EvaluationDataset, SingleTurnSample
    samples = [
        SingleTurnSample(
            user_input=r["query"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth_answer"],
        )
        for r in positives
        if r["ground_truth_answer"] and r["contexts"]
    ]
    if not samples:
        return {}
    llm, embeddings = _ragas_llm_and_embeddings()
    dataset = EvaluationDataset(samples=samples)
    result = evaluate(dataset, metrics=list(metrics.values()), llm=llm, embeddings=embeddings)
    # Use to_pandas() to extract per-metric mean scores (ragas 0.4.x result format).
    # Column names come from metric.name (e.g. LLMContextPrecisionWithReference →
    # "llm_context_precision_with_reference"), so map via the metric objects.
    df = result.to_pandas()
    out = {}
    for our_key, metric_obj in zip(metrics.keys(), metrics.values()):
        col = metric_obj.name
        if col in df.columns:
            out[our_key] = round(float(df[col].mean()), 4)
    return out


def run_ragas_v1(positives: list[dict], metrics: dict) -> dict:
    from ragas import evaluate
    from datasets import Dataset
    valid = [r for r in positives if r["ground_truth_answer"] and r["contexts"]]
    if not valid:
        return {}
    data = {
        "question":     [r["query"]                for r in valid],
        "answer":       [r["answer"]               for r in valid],
        "contexts":     [r["contexts"]             for r in valid],
        "ground_truth": [r["ground_truth_answer"]  for r in valid],
    }
    dataset = Dataset.from_dict(data)
    result = evaluate(dataset, metrics=list(metrics.values()))
    return {k: round(float(result[k]), 4) for k in metrics if k in result}


# ── System-specific metrics ───────────────────────────────────────────────────

def citation_accuracy(results: list[dict]) -> float:
    positives = [r for r in results if r["expected_pattern_ids"]]
    if not positives:
        return 0.0
    correct = 0
    for r in positives:
        # Normalise "Pattern 1", "Pattern_1", "Pattern1" → "Pattern_N" for comparison
        cited = {
            f"Pattern_{m.group(1)}"
            for m in re.finditer(r"Pattern[_ ]?(\d+)", r["answer"])
        }
        if any(p in cited for p in r["expected_pattern_ids"]):
            correct += 1
    return round(correct / len(positives), 4)


def negative_handling_rate(results: list[dict]) -> float:
    negatives = [r for r in results if not r["expected_pattern_ids"]]
    if not negatives:
        return 0.0
    handled = sum(
        1 for r in negatives
        if any(p in r["answer"].lower() for p in NEGATIVE_PHRASES)
    )
    return round(handled / len(negatives), 4)


# ── Supabase chunk lookup ─────────────────────────────────────────────────────

def build_chunk_lookup() -> dict:
    """Returns {(pattern_id, section_title): content} for all 79 chunks."""
    from supabase import create_client
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    rows = supabase.table("documents").select("content, metadata").execute().data or []
    return {
        (row["metadata"].get("pattern_id", ""), row["metadata"].get("section_title", "")): row["content"]
        for row in rows
    }


def attach_contexts(results: list[dict], chunk_lookup: dict) -> None:
    """Adds a 'contexts' key (list[str]) to each result dict in-place."""
    for r in results:
        r["contexts"] = [
            chunk_lookup.get((s["pattern_id"], s["section_title"]), "")
            for s in r["sources"]
        ]


# ── Gate C display ────────────────────────────────────────────────────────────

def print_gate_results(gen_metrics: dict, sys_metrics: dict) -> bool:
    print("\n" + "=" * 68)
    print("GENERATION METRICS — GATE C TARGETS")
    print("=" * 68)
    all_pass = True

    for key, (target, higher_is_better) in GATE_C_GENERATION.items():
        val = gen_metrics.get(key)
        if val is None:
            print(f"  [----] {GATE_C_LABELS[key]:<38} N/A    (target: {target:.2f})")
            continue
        ok = (val >= target) if higher_is_better else (val <= target)
        if not ok:
            all_pass = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {GATE_C_LABELS[key]:<38} {val:.4f}  (target: {target:.2f})")

    print()
    print("SYSTEM METRICS — GATE C TARGETS")
    print("-" * 68)
    for key, (target, higher_is_better) in GATE_C_SYSTEM.items():
        val = sys_metrics.get(key)
        if val is None:
            print(f"  [----] {GATE_C_LABELS[key]:<38} N/A    (target: {target:.2f})")
            continue
        ok = (val >= target) if higher_is_better else (val <= target)
        if not ok:
            all_pass = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {GATE_C_LABELS[key]:<38} {val:.4f}  (target: {target:.2f})")

    print()
    print(f"GATE C GENERATION+SYSTEM: {'PASSED' if all_pass else 'FAILED'}")
    return all_pass


# ── Supabase write ────────────────────────────────────────────────────────────

def write_to_supabase(phase: str, retrieval: dict, generation: dict, system: dict) -> None:
    try:
        from supabase import create_client
        supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        supabase.table("eval_runs").insert({
            "phase":      phase,
            "retrieval":  retrieval,
            "generation": generation,
            "system":     system,
            "notes":      "full generation eval (retrieval + RAGAS + system metrics)",
        }).execute()
        print(f"\n[Supabase] Written to eval_runs for phase '{phase}'")
    except Exception as e:
        print(f"\n[Supabase] Write skipped: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generation evaluation — Phase 7")
    parser.add_argument("--phase",       required=True, help="Phase label matching retrieval eval run")
    parser.add_argument("--no-supabase", action="store_true", help="Skip Supabase write")
    args = parser.parse_args()

    results_path = RESULTS_DIR / f"{args.phase}.json"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found.")
        print("Run retrieval eval first:  python eval/run_retrieval_eval.py --phase " + args.phase)
        sys.exit(1)

    data = json.loads(results_path.read_text(encoding="utf-8"))
    results = data["results"]
    existing_retrieval = data["metrics"].get("retrieval", {})

    print(f"Phase: {args.phase}  |  {len(results)} results loaded from {results_path}")

    # Fetch chunk content from Supabase for RAGAS contexts
    print("Fetching chunk content from Supabase…")
    try:
        chunk_lookup = build_chunk_lookup()
        print(f"  Loaded {len(chunk_lookup)} chunks")
        attach_contexts(results, chunk_lookup)
    except Exception as e:
        print(f"  WARNING: Could not load chunks from Supabase: {e}")
        print("  RAGAS context-dependent metrics will be skipped.")
        for r in results:
            r["contexts"] = []

    positives = [r for r in results if r["expected_pattern_ids"]]

    # Run RAGAS
    gen_metrics: dict = {}
    hallucination_rate: float | None = None

    try:
        api_version, ragas_metrics = _try_import_ragas()
        print(f"\nRunning RAGAS evaluation (API {api_version}) on {len(positives)} positive cases…")

        if api_version == "v2":
            gen_metrics = run_ragas_v2(positives, ragas_metrics)
        else:
            gen_metrics = run_ragas_v1(positives, ragas_metrics)

        if "faithfulness" in gen_metrics:
            hallucination_rate = round(1.0 - gen_metrics["faithfulness"], 4)

        print(f"  RAGAS complete — {len(gen_metrics)} metrics computed")

    except ImportError as e:
        print(f"\nWARNING: {e}")
        print("Generation metrics (RAGAS) will be skipped.")
        print("Install with:  pip install 'ragas>=0.1.0' datasets pandas")

    # System-specific metrics (no RAGAS needed)
    sys_metrics = {
        "citation_accuracy":      citation_accuracy(results),
        "negative_handling_rate": negative_handling_rate(results),
        "hallucination_rate":     hallucination_rate,
    }

    passed = print_gate_results(gen_metrics, sys_metrics)

    # Update results file with generation + system metrics
    data["metrics"]["generation"] = gen_metrics
    data["metrics"]["system"]     = sys_metrics
    data["generation_run_at"]     = datetime.now(timezone.utc).isoformat()
    results_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nResults updated → {results_path}")

    if not args.no_supabase:
        write_to_supabase(args.phase, existing_retrieval, gen_metrics, sys_metrics)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
