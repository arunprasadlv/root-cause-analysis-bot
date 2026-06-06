"""
Retrieval evaluation — Phase 7.

Calls the local /api/analyze endpoint for each test case and computes retrieval
metrics from the returned sources. Requires the server running on localhost:8000.

Metrics computed:
  HR@5, MRR@5, Context Precision@5, Context Recall,
  Error Code Routing Accuracy, Escalation Routing Accuracy,
  Negative Handling Rate

Results saved to eval/results/{phase}.json and optionally written to Supabase.

Usage:
    python eval/run_retrieval_eval.py --phase phase3_hybrid [--k 5] [--no-supabase]
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

API_URL = "http://localhost:8000/api/analyze"
CSV_PATH = "eval/golden_test_set.csv"
RESULTS_DIR = Path("eval/results")

NEGATIVE_PHRASES = ["no relevant runbook", "not covered", "cannot find"]

# Gate C required retrieval metrics — failures here block the gate
GATE_C_RETRIEVAL = {
    "hr_at_5":             (0.85, True),
    "mrr_at_5":            (0.75, True),
    "negative_handling_rate": (1.00, True),
}

# Tracked metrics — computed and reported but not gate-blocking
TRACKED_RETRIEVAL = {
    "context_precision_at_5":      (0.70, True),
    "context_recall":              (0.80, True),
    "error_code_routing_accuracy": (0.90, True),
    "escalation_routing_accuracy": (0.90, True),
}

GATE_C_LABELS = {
    "hr_at_5":                     "Hit Rate @ 5",
    "mrr_at_5":                    "MRR @ 5",
    "context_precision_at_5":      "Context Precision @ 5",
    "context_recall":              "Context Recall",
    "error_code_routing_accuracy": "Error Code Routing Accuracy",
    "escalation_routing_accuracy": "Escalation Routing Accuracy",
    "negative_handling_rate":      "Negative Handling Rate",
}


def load_test_set(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            {
                "id":                      row["id"],
                "query":                   row["query"],
                "query_type":              row["query_type"],
                "expected_pattern_ids":    [p for p in row["expected_pattern_ids"].split("|") if p],
                "expected_section_types":  [s for s in row["expected_section_types"].split("|") if s],
                "expected_section_titles": [t for t in row["expected_section_titles"].split("|") if t],
                "ground_truth_answer":     row["ground_truth_answer"] or None,
            }
            for row in reader
        ]


def call_api(query: str) -> dict:
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def check_server() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:8000/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def compute_metrics(results: list[dict], k: int = 5) -> dict:
    positives = [r for r in results if r["expected_pattern_ids"]]
    negatives = [r for r in results if not r["expected_pattern_ids"]]

    # HR@k
    hits = sum(
        1 for r in positives
        if any(s["pattern_id"] in r["expected_pattern_ids"] for s in r["sources"][:k])
    )
    hr = hits / len(positives) if positives else 0.0

    # MRR@k
    rrs = []
    for r in positives:
        for rank, s in enumerate(r["sources"][:k], 1):
            if s["pattern_id"] in r["expected_pattern_ids"]:
                rrs.append(1 / rank)
                break
        else:
            rrs.append(0.0)
    mrr = sum(rrs) / len(rrs) if rrs else 0.0

    # Context Precision@k — fraction of top-k sources with a relevant pattern_id
    cp_scores = []
    for r in positives:
        top = r["sources"][:k]
        if not top:
            cp_scores.append(0.0)
            continue
        relevant = sum(1 for s in top if s["pattern_id"] in r["expected_pattern_ids"])
        cp_scores.append(relevant / len(top))
    cp = sum(cp_scores) / len(cp_scores) if cp_scores else 0.0

    # Context Recall — fraction of expected pattern_ids found anywhere in top-k
    cr_scores = []
    for r in positives:
        if not r["expected_pattern_ids"]:
            continue
        found = sum(
            1 for pid in r["expected_pattern_ids"]
            if any(s["pattern_id"] == pid for s in r["sources"][:k])
        )
        cr_scores.append(found / len(r["expected_pattern_ids"]))
    cr = sum(cr_scores) / len(cr_scores) if cr_scores else 0.0

    # Error Code Routing — "Error Signatures" section in top-3 for error_code queries
    ec_cases = [r for r in positives if r["query_type"] == "error_code"]
    ec_hits = sum(
        1 for r in ec_cases
        if any("error signature" in s["section_title"].lower() for s in r["sources"][:3])
    )
    ec_acc = ec_hits / len(ec_cases) if ec_cases else 0.0

    # Escalation Routing — escalation section in top-3 for escalation queries
    esc_cases = [r for r in positives if r["query_type"] == "escalation"]
    esc_hits = sum(
        1 for r in esc_cases
        if any("escalation" in s["section_title"].lower() for s in r["sources"][:3])
    )
    esc_acc = esc_hits / len(esc_cases) if esc_cases else 0.0

    # Negative Handling Rate
    nhr = (
        sum(1 for r in negatives if r["negative_handled"]) / len(negatives)
        if negatives else 0.0
    )

    return {
        "hr_at_5":                     round(hr, 4),
        "mrr_at_5":                    round(mrr, 4),
        "context_precision_at_5":      round(cp, 4),
        "context_recall":              round(cr, 4),
        "error_code_routing_accuracy": round(ec_acc, 4),
        "escalation_routing_accuracy": round(esc_acc, 4),
        "negative_handling_rate":      round(nhr, 4),
        "n_total":     len(results),
        "n_positives": len(positives),
        "n_negatives": len(negatives),
        "n_error_code":  len(ec_cases),
        "n_escalation":  len(esc_cases),
    }


def print_results(metrics: dict) -> bool:
    print("\n" + "=" * 68)
    print("RETRIEVAL METRICS — GATE C (gate-blocking)")
    print("=" * 68)
    all_pass = True
    for key, (target, higher_is_better) in GATE_C_RETRIEVAL.items():
        val = metrics[key]
        ok = (val >= target) if higher_is_better else (val <= target)
        if not ok:
            all_pass = False
        label = GATE_C_LABELS[key]
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<38} {val:.4f}  (target: {target:.2f})")

    print()
    print("RETRIEVAL METRICS — tracked (not gate-blocking)")
    print("-" * 68)
    for key, (target, higher_is_better) in TRACKED_RETRIEVAL.items():
        val = metrics.get(key)
        if val is None:
            continue
        ok = (val >= target) if higher_is_better else (val <= target)
        label = GATE_C_LABELS[key]
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<38} {val:.4f}  (target: {target:.2f})")

    print()
    print(f"GATE C RETRIEVAL: {'PASSED' if all_pass else 'FAILED'}")
    return all_pass


def write_to_supabase(phase: str, retrieval: dict) -> None:
    try:
        from supabase import create_client
        supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        supabase.table("eval_runs").insert({
            "phase":     phase,
            "retrieval": retrieval,
            "notes":     "retrieval eval",
        }).execute()
        print(f"\n[Supabase] Written to eval_runs for phase '{phase}'")
    except Exception as e:
        print(f"\n[Supabase] Write skipped: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval evaluation — Phase 7")
    parser.add_argument("--phase",        required=True, help="Phase label e.g. phase3_hybrid")
    parser.add_argument("--k",            type=int, default=5)
    parser.add_argument("--no-supabase",  action="store_true", help="Skip Supabase write")
    args = parser.parse_args()

    if not check_server():
        print("ERROR: API server not reachable at http://localhost:8000")
        print("Start the server with:  python main.py")
        sys.exit(1)

    cases = load_test_set(CSV_PATH)
    print(f"Phase: {args.phase}  |  k={args.k}  |  {len(cases)} test cases")
    print()

    results = []
    failures = []

    for i, case in enumerate(cases, 1):
        try:
            resp = call_api(case["query"])
        except Exception as e:
            print(f"  [{i:02}/{len(cases)}] ERROR  {case['id']}: {e}")
            failures.append({"id": case["id"], "error": str(e)})
            time.sleep(0.3)
            continue

        is_neg  = not case["expected_pattern_ids"]
        sources = resp["sources"]
        answer  = resp["answer"]

        neg_handled = is_neg and any(p in answer.lower() for p in NEGATIVE_PHRASES)

        if is_neg:
            status = "PASS" if neg_handled else "FAIL"
        else:
            hit = any(s["pattern_id"] in case["expected_pattern_ids"] for s in sources[:args.k])
            status = "PASS" if hit else "FAIL"

        print(f"  [{i:02}/{len(cases)}] {status}  {case['id']:<32} {case['query'][:48]}")

        results.append({
            "id":                      case["id"],
            "query":                   case["query"],
            "query_type":              case["query_type"],
            "expected_pattern_ids":    case["expected_pattern_ids"],
            "expected_section_types":  case["expected_section_types"],
            "expected_section_titles": case["expected_section_titles"],
            "ground_truth_answer":     case["ground_truth_answer"],
            "sources":                 sources,
            "answer":                  answer,
            "negative_handled":        neg_handled,
        })

        time.sleep(0.3)

    if failures:
        print(f"\n  {len(failures)} call(s) failed — results may be incomplete.")

    metrics = compute_metrics(results, k=args.k)
    passed  = print_results(metrics)

    if failures:
        for f in failures:
            print(f"  FAIL  {json.dumps(f)}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.phase}.json"
    out_path.write_text(
        json.dumps({
            "phase":   args.phase,
            "run_at":  datetime.now(timezone.utc).isoformat(),
            "k":       args.k,
            "metrics": {"retrieval": metrics},
            "results": results,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\nResults saved → {out_path}")

    if not args.no_supabase:
        write_to_supabase(args.phase, metrics)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
