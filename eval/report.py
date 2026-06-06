"""
Evaluation comparison report — Phase 7.

Loads two phase result JSON files and prints a side-by-side comparison table
with Gate C targets. Optionally writes a combined row to Supabase eval_runs.

Usage:
    python eval/report.py --baseline phase2_baseline --compare phase3_hybrid
    python eval/report.py --baseline phase3_hybrid  --compare phase6_production
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RESULTS_DIR = Path("eval/results")

# Gate C targets — (target_value, higher_is_better)
GATE_C = {
    # Retrieval
    "hr_at_5":                     (0.85, True,  "Retrieval"),
    "mrr_at_5":                    (0.75, True,  "Retrieval"),
    "context_precision_at_5":      (0.70, True,  "Retrieval"),
    "context_recall":              (0.80, True,  "Retrieval"),
    "error_code_routing_accuracy": (0.90, True,  "Retrieval"),
    "escalation_routing_accuracy": (0.90, True,  "Retrieval"),
    # Generation (RAGAS)
    "faithfulness":                (0.90, True,  "Generation"),
    "answer_relevancy":            (0.85, True,  "Generation"),
    "context_precision":           (0.70, True,  "Generation"),
    "context_recall_ragas":        (0.80, True,  "Generation"),
    "answer_correctness":          (0.75, True,  "Generation"),
    # System
    "citation_accuracy":           (0.90, True,  "System"),
    "negative_handling_rate":      (1.00, True,  "System"),
    "hallucination_rate":          (0.05, False, "System"),
}

LABELS = {
    "hr_at_5":                     "Hit Rate @ 5",
    "mrr_at_5":                    "MRR @ 5",
    "context_precision_at_5":      "Context Precision @ 5",
    "context_recall":              "Context Recall",
    "error_code_routing_accuracy": "Error Code Routing Acc.",
    "escalation_routing_accuracy": "Escalation Routing Acc.",
    "faithfulness":                "Faithfulness",
    "answer_relevancy":            "Answer Relevance",
    "context_precision":           "Context Precision (RAGAS)",
    "context_recall_ragas":        "Context Recall (RAGAS)",
    "answer_correctness":          "Answer Correctness",
    "citation_accuracy":           "Citation Accuracy",
    "negative_handling_rate":      "Negative Handling Rate",
    "hallucination_rate":          "Hallucination Rate",
}


def load_phase(phase: str) -> dict:
    path = RESULTS_DIR / f"{phase}.json"
    if not path.exists():
        print(f"ERROR: {path} not found.")
        print(f"Run:  python eval/run_retrieval_eval.py --phase {phase}")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def extract_all_metrics(data: dict) -> dict:
    """Flatten retrieval + generation + system metrics into one dict."""
    flat: dict = {}
    metrics = data.get("metrics", {})

    retrieval = metrics.get("retrieval", {})
    flat.update({k: retrieval.get(k) for k in [
        "hr_at_5", "mrr_at_5", "context_precision_at_5", "context_recall",
        "error_code_routing_accuracy", "escalation_routing_accuracy",
    ]})

    generation = metrics.get("generation", {})
    flat.update({
        "faithfulness":         generation.get("faithfulness"),
        "answer_relevancy":     generation.get("answer_relevancy"),
        "context_precision":    generation.get("context_precision"),
        "context_recall_ragas": generation.get("context_recall"),
        "answer_correctness":   generation.get("answer_correctness"),
    })

    system = metrics.get("system", {})
    flat.update({
        "citation_accuracy":      system.get("citation_accuracy"),
        "negative_handling_rate": system.get("negative_handling_rate"),
        "hallucination_rate":     system.get("hallucination_rate"),
    })

    return flat


def gate_marker(value, target: float, higher_is_better: bool) -> str:
    if value is None:
        return "----"
    ok = (value >= target) if higher_is_better else (value <= target)
    return "PASS" if ok else "FAIL"


def fmt(value) -> str:
    if value is None:
        return "   N/A  "
    return f" {value:.4f} "


def print_report(baseline_name: str, compare_name: str,
                 base: dict, comp: dict) -> None:
    w_label = 30
    print()
    print("=" * 75)
    print(f"  EVALUATION REPORT")
    print(f"  Baseline : {baseline_name}")
    print(f"  Compare  : {compare_name}")
    print("=" * 75)

    current_group = None
    for key, (target, higher_is_better, group) in GATE_C.items():
        if group != current_group:
            current_group = group
            print()
            print(f"  {group.upper()}")
            print(f"  {'Metric':<{w_label}}  {'Target':>8}  {'Baseline':>10}  {'Compare':>10}  {'Δ':>8}")
            print(f"  {'-'*w_label}  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*8}")

        bval = base.get(key)
        cval = comp.get(key)

        b_str  = fmt(bval)
        c_str  = fmt(cval)
        b_mark = gate_marker(bval, target, higher_is_better)
        c_mark = gate_marker(cval, target, higher_is_better)

        if bval is not None and cval is not None:
            delta = cval - bval
            d_str = f" {delta:+.4f}"
        else:
            d_str = "     N/A"

        label = LABELS.get(key, key)
        print(
            f"  {label:<{w_label}}  {target:>8.2f}"
            f"  [{b_mark}]{b_str}"
            f"  [{c_mark}]{c_str}"
            f"  {d_str}"
        )

    print()
    print("=" * 75)

    # Overall Gate C pass/fail for compare phase
    gate_c_keys = list(GATE_C.keys())
    comp_pass = all(
        gate_marker(comp.get(k), t, h) in ("PASS", "----")
        for k, (t, h, _) in GATE_C.items()
        if comp.get(k) is not None
    )
    comp_has_all = all(comp.get(k) is not None for k in gate_c_keys)

    if comp_has_all:
        print(f"  GATE C ({compare_name}): {'PASSED' if comp_pass else 'FAILED'}")
    else:
        missing = [k for k in gate_c_keys if comp.get(k) is None]
        print(f"  GATE C ({compare_name}): INCOMPLETE — missing: {', '.join(missing)}")
        print(f"  Run generation eval:  python eval/run_generation_eval.py --phase {compare_name}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluation comparison report — Phase 7")
    parser.add_argument("--baseline", required=True, help="Baseline phase JSON e.g. phase2_baseline")
    parser.add_argument("--compare",  required=True, help="Phase to compare e.g. phase3_hybrid")
    args = parser.parse_args()

    base_data = load_phase(args.baseline)
    comp_data = load_phase(args.compare)

    base_metrics = extract_all_metrics(base_data)
    comp_metrics = extract_all_metrics(comp_data)

    print_report(args.baseline, args.compare, base_metrics, comp_metrics)


if __name__ == "__main__":
    main()
