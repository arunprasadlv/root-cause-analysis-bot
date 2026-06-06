"""
Gate A evaluation — Phase 2 baseline.

Measures HR@5 and Negative Handling Rate by calling the local API.
Faithfulness (RAGAS) is a separate run in Phase 7.

Usage:
    python eval/test_gate_a.py
"""

import csv
import json
import sys
import time
import urllib.request
import urllib.error

API_URL = "http://localhost:8000/api/analyze"
CSV_PATH = "eval/golden_test_set.csv"

GATE_A = {
    "hr_at_5": 0.70,
    "negative_handling_rate": 1.00,
}

NEGATIVE_PHRASES = ["no relevant runbook", "not covered", "cannot find"]


def load_test_set(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cases = []
        for row in reader:
            cases.append({
                "id":                     row["id"],
                "query":                  row["query"],
                "query_type":             row["query_type"],
                "expected_pattern_ids":   [p for p in row["expected_pattern_ids"].split("|") if p],
            })
    return cases


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


def hit_at_k(sources: list[dict], expected_ids: list[str], k: int = 5) -> bool:
    retrieved_ids = [s["pattern_id"] for s in sources[:k]]
    return any(pid in retrieved_ids for pid in expected_ids)


def is_negative_handled(answer: str) -> bool:
    lower = answer.lower()
    return any(phrase in lower for phrase in NEGATIVE_PHRASES)


def main():
    cases = load_test_set(CSV_PATH)
    print(f"Loaded {len(cases)} test cases\n")

    positives = [c for c in cases if c["expected_pattern_ids"]]
    negatives = [c for c in cases if not c["expected_pattern_ids"]]

    hits = 0
    neg_handled = 0
    failures = []

    for i, case in enumerate(cases, 1):
        try:
            response = call_api(case["query"])
        except Exception as e:
            print(f"  [{i}/{len(cases)}] ERROR {case['id']}: {e}")
            failures.append({"id": case["id"], "error": str(e)})
            continue

        is_negative = not case["expected_pattern_ids"]
        answer = response["answer"]
        sources = response["sources"]

        if is_negative:
            handled = is_negative_handled(answer)
            if handled:
                neg_handled += 1
            status = "PASS" if handled else "FAIL"
            if not handled:
                failures.append({
                    "id": case["id"],
                    "type": "negative_not_handled",
                    "answer": answer[:120],
                })
        else:
            hit = hit_at_k(sources, case["expected_pattern_ids"])
            if hit:
                hits += 1
            status = "PASS" if hit else "FAIL"
            if not hit:
                retrieved = [s["pattern_id"] for s in sources]
                failures.append({
                    "id": case["id"],
                    "type": "miss",
                    "expected": case["expected_pattern_ids"],
                    "retrieved": retrieved,
                })

        print(f"  [{i:02}/{len(cases)}] {status}  {case['id']:<35} {case['query'][:60]}")

        # Small delay to avoid rate limiting
        time.sleep(0.3)

    # ── Results ──────────────────────────────────────────────────────────────
    hr_at_5 = hits / len(positives) if positives else 0.0
    neg_rate = neg_handled / len(negatives) if negatives else 0.0

    print("\n" + "=" * 60)
    print("GATE A RESULTS")
    print("=" * 60)

    def gate(label, value, target, higher_is_better=True):
        passed = value >= target if higher_is_better else value <= target
        marker = "PASS" if passed else "FAIL"
        print(f"  [{marker}] {label:<35} {value:.3f}  (target: {target:.2f})")

    gate("Hit Rate @ 5", hr_at_5, GATE_A["hr_at_5"])
    gate("Negative Handling Rate", neg_rate, GATE_A["negative_handling_rate"])

    if failures:
        print(f"\nFailures ({len(failures)}):")
        for f in failures:
            print(f"  {json.dumps(f)}")

    overall = hr_at_5 >= GATE_A["hr_at_5"] and neg_rate >= GATE_A["negative_handling_rate"]
    print("\n" + ("GATE A: PASSED" if overall else "GATE A: FAILED"))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
