#!/usr/bin/env python3
"""
eval/generate_report.py — Generate a self-contained HTML eval report.

Usage:
    python eval/generate_report.py --phase phase6_production
    python eval/generate_report.py --phase phase6_production --out eval/reports/
"""

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

RESULTS_DIR = Path("eval/results")
DEFAULT_OUT_DIR = Path("eval/reports")

GATE_C = {
    "hr_at_5":                     (0.85, True,  "Retrieval",  "Hit Rate @ 5"),
    "mrr_at_5":                    (0.75, True,  "Retrieval",  "MRR @ 5"),
    "context_precision_at_5":      (0.70, True,  "Retrieval",  "Context Precision @ 5"),
    "context_recall":              (0.80, True,  "Retrieval",  "Context Recall"),
    "error_code_routing_accuracy": (0.90, True,  "Retrieval",  "Error Code Routing"),
    "escalation_routing_accuracy": (0.90, True,  "Retrieval",  "Escalation Routing"),
    "faithfulness":                (0.90, True,  "Generation", "Faithfulness"),
    "answer_relevancy":            (0.85, True,  "Generation", "Answer Relevancy"),
    "context_precision":           (0.70, True,  "Generation", "Context Precision"),
    "context_recall_ragas":        (0.80, True,  "Generation", "Context Recall"),
    "answer_correctness":          (0.75, True,  "Generation", "Answer Correctness"),
    "citation_accuracy":           (0.90, True,  "System",     "Citation Accuracy"),
    "negative_handling_rate":      (1.00, True,  "System",     "Negative Handling"),
    "hallucination_rate":          (0.05, False, "System",     "Hallucination Rate"),
}

QUERY_TYPE_LABELS = {
    "error_code":  "Error Code",
    "symptom":     "Symptom",
    "triage":      "Triage",
    "procedure":   "Procedure",
    "negative":    "Negative",
    "escalation":  "Escalation",
    "edge_case":   "Edge Case",
}


def flatten_metrics(data: dict) -> dict:
    m = data.get("metrics", {})
    r = m.get("retrieval", {})
    g = m.get("generation", {})
    s = m.get("system", {})
    return {
        "hr_at_5":                     r.get("hr_at_5"),
        "mrr_at_5":                    r.get("mrr_at_5"),
        "context_precision_at_5":      r.get("context_precision_at_5"),
        "context_recall":              r.get("context_recall"),
        "error_code_routing_accuracy": r.get("error_code_routing_accuracy"),
        "escalation_routing_accuracy": r.get("escalation_routing_accuracy"),
        "faithfulness":                g.get("faithfulness"),
        "answer_relevancy":            g.get("answer_relevancy"),
        "context_precision":           g.get("context_precision"),
        "context_recall_ragas":        g.get("context_recall"),
        "answer_correctness":          g.get("answer_correctness"),
        "citation_accuracy":           s.get("citation_accuracy"),
        "negative_handling_rate":      s.get("negative_handling_rate"),
        "hallucination_rate":          s.get("hallucination_rate"),
    }


def retrieval_hit(result: dict) -> bool:
    expected = set(result.get("expected_pattern_ids", []))
    retrieved = {s["pattern_id"] for s in result.get("sources", [])}
    return bool(expected & retrieved)


def format_run_at(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y at %H:%M UTC")
    except Exception:
        return ts


def build_html(data: dict, phase: str) -> str:
    metrics = flatten_metrics(data)
    results = data.get("results", [])
    run_at = format_run_at(data.get("run_at", ""))
    k = data.get("k", 5)

    retrieval_meta = data.get("metrics", {}).get("retrieval", {})
    n_total = retrieval_meta.get("n_total", len(results))
    n_positives = retrieval_meta.get("n_positives", 0)
    n_negatives = retrieval_meta.get("n_negatives", 0)
    n_error_code = retrieval_meta.get("n_error_code", 0)
    n_escalation = retrieval_meta.get("n_escalation", 0)

    # Gate C overall
    n_pass = sum(
        1 for k_name, (target, higher, _, _) in GATE_C.items()
        if metrics.get(k_name) is not None and (
            (metrics[k_name] >= target) if higher else (metrics[k_name] <= target)
        )
    )
    n_fail = sum(
        1 for k_name, (target, higher, _, _) in GATE_C.items()
        if metrics.get(k_name) is not None and not (
            (metrics[k_name] >= target) if higher else (metrics[k_name] <= target)
        )
    )
    n_missing = sum(1 for k_name in GATE_C if metrics.get(k_name) is None)

    # Per-result data for JS
    results_js = []
    for r in results:
        results_js.append({
            "id": r.get("id", ""),
            "query": r.get("query", ""),
            "query_type": r.get("query_type", ""),
            "expected_pattern_ids": r.get("expected_pattern_ids", []),
            "expected_section_titles": r.get("expected_section_titles", []),
            "ground_truth_answer": r.get("ground_truth_answer", ""),
            "answer": r.get("answer", ""),
            "negative_handled": r.get("negative_handled", False),
            "hit": retrieval_hit(r),
            "sources": r.get("sources", []),
        })

    results_json = json.dumps(results_js, ensure_ascii=False)

    # Build metric groups HTML
    groups = {}
    for key, (target, higher, group, label) in GATE_C.items():
        groups.setdefault(group, []).append((key, target, higher, label))

    def metric_card(key, target, higher, label):
        value = metrics.get(key)
        if value is None:
            badge = '<span class="badge badge-missing">N/A</span>'
            pct = 0
            val_str = "—"
            card_cls = "card-missing"
        else:
            passed = (value >= target) if higher else (value <= target)
            badge = f'<span class="badge {"badge-pass" if passed else "badge-fail"}">{"PASS" if passed else "FAIL"}</span>'
            pct = min(int(value * 100), 100)
            val_str = f"{value:.1%}"
            card_cls = "card-pass" if passed else "card-fail"

        target_str = f"{target:.0%}"
        dir_label = "min" if higher else "max"
        bar_pct = pct if higher else min(int((1 - value) * 100 if value is not None else 100), 100)

        return f"""
        <div class="metric-card {card_cls}">
          <div class="metric-header">
            <span class="metric-label">{label}</span>
            {badge}
          </div>
          <div class="metric-value">{val_str}</div>
          <div class="progress-bar">
            <div class="progress-fill" style="width:{pct}%"></div>
          </div>
          <div class="metric-target">Target: {dir_label} {target_str}</div>
        </div>"""

    group_html = ""
    for group_name, items in groups.items():
        cards = "".join(metric_card(k, t, h, lbl) for k, t, h, lbl in items)
        group_html += f"""
      <div class="group-section">
        <h3 class="group-title">{group_name}</h3>
        <div class="metric-grid">{cards}
        </div>
      </div>"""

    gate_status_cls = "gate-pass" if n_fail == 0 and n_missing == 0 else "gate-fail"
    gate_text = "GATE C PASSED" if n_fail == 0 and n_missing == 0 else f"GATE C: {n_fail} failing, {n_missing} missing"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Eval Report — {phase}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f1f5f9;
    color: #0f172a;
    font-size: 14px;
    line-height: 1.5;
  }}

  /* ── Header ── */
  .page-header {{
    background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
    color: white;
    padding: 28px 36px;
  }}
  .page-header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }}
  .page-header .sub {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
  .header-meta {{ display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }}
  .meta-pill {{
    background: rgba(255,255,255,0.1);
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 12px;
    color: #cbd5e1;
  }}
  .meta-pill strong {{ color: white; }}

  /* ── Gate C Banner ── */
  .gate-banner {{
    padding: 14px 36px;
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 16px;
  }}
  .gate-pass {{ background: #dcfce7; color: #15803d; border-bottom: 2px solid #86efac; }}
  .gate-fail {{ background: #fee2e2; color: #b91c1c; border-bottom: 2px solid #fca5a5; }}
  .gate-stats {{ display: flex; gap: 12px; font-weight: 400; color: inherit; }}
  .stat {{ opacity: 0.8; }}
  .stat strong {{ font-weight: 600; opacity: 1; }}

  /* ── Content ── */
  .content {{ max-width: 1200px; margin: 0 auto; padding: 28px 24px; }}

  /* ── Section headers ── */
  .section-title {{
    font-size: 16px;
    font-weight: 700;
    color: #1e293b;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }}

  /* ── Metric groups ── */
  .group-section {{ margin-bottom: 28px; }}
  .group-title {{
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #64748b;
    margin-bottom: 10px;
  }}
  .metric-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }}
  .metric-card {{
    background: white;
    border-radius: 10px;
    padding: 16px;
    border-left: 4px solid transparent;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  .card-pass {{ border-left-color: #22c55e; }}
  .card-fail {{ border-left-color: #ef4444; }}
  .card-missing {{ border-left-color: #94a3b8; }}
  .metric-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 6px; margin-bottom: 8px; }}
  .metric-label {{ font-size: 12px; color: #64748b; font-weight: 500; line-height: 1.3; }}
  .metric-value {{ font-size: 26px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
  .card-fail .metric-value {{ color: #b91c1c; }}
  .progress-bar {{
    height: 5px;
    background: #e2e8f0;
    border-radius: 3px;
    margin-bottom: 6px;
    overflow: hidden;
  }}
  .progress-fill {{
    height: 100%;
    background: #22c55e;
    border-radius: 3px;
    transition: width 0.4s ease;
  }}
  .card-fail .progress-fill {{ background: #ef4444; }}
  .card-missing .progress-fill {{ background: #94a3b8; }}
  .metric-target {{ font-size: 11px; color: #94a3b8; }}

  /* ── Badges ── */
  .badge {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 2px 7px;
    border-radius: 4px;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .badge-pass {{ background: #dcfce7; color: #15803d; }}
  .badge-fail {{ background: #fee2e2; color: #b91c1c; }}
  .badge-missing {{ background: #f1f5f9; color: #94a3b8; }}
  .badge-hit {{ background: #dbeafe; color: #1d4ed8; font-size: 11px; padding: 2px 8px; border-radius: 4px; }};
  .badge-miss {{ background: #fef3c7; color: #b45309; font-size: 11px; padding: 2px 8px; border-radius: 4px; }}
  .badge-type {{
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
    background: #f1f5f9;
    color: #475569;
    white-space: nowrap;
  }}

  /* ── Test Cases ── */
  .filter-bar {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 14px;
    align-items: center;
  }}
  .filter-btn {{
    border: 1px solid #e2e8f0;
    background: white;
    padding: 5px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    color: #475569;
    transition: all 0.15s;
  }}
  .filter-btn:hover {{ background: #f8fafc; }}
  .filter-btn.active {{
    background: #0f172a;
    color: white;
    border-color: #0f172a;
  }}
  .search-box {{
    margin-left: auto;
    padding: 5px 12px;
    border: 1px solid #e2e8f0;
    border-radius: 20px;
    font-size: 12px;
    outline: none;
    width: 220px;
    color: #0f172a;
    background: white;
  }}
  .search-box:focus {{ border-color: #94a3b8; }}

  .results-count {{ font-size: 12px; color: #64748b; margin-bottom: 10px; }}

  .cases-table {{ width: 100%; border-collapse: collapse; }}
  .cases-table thead tr {{
    background: #f8fafc;
    border-bottom: 2px solid #e2e8f0;
  }}
  .cases-table th {{
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #64748b;
    padding: 10px 14px;
  }}
  .cases-table td {{ padding: 10px 14px; vertical-align: top; }}
  .case-row {{
    border-bottom: 1px solid #f1f5f9;
    cursor: pointer;
    transition: background 0.1s;
  }}
  .case-row:hover {{ background: #f8fafc; }}
  .case-row.row-miss {{ background: #fffbeb; }}
  .case-row.row-miss:hover {{ background: #fef3c7; }}
  .query-text {{
    font-size: 13px;
    color: #1e293b;
    max-width: 380px;
    line-height: 1.4;
  }}
  .pattern-tag {{
    display: inline-block;
    font-size: 11px;
    padding: 1px 7px;
    background: #eff6ff;
    color: #2563eb;
    border-radius: 4px;
    margin: 1px;
    font-weight: 500;
  }}
  .pattern-tag.retrieved {{ background: #f0fdf4; color: #15803d; }}
  .pattern-tag.missed {{ background: #fee2e2; color: #b91c1c; }}

  /* ── Expanded detail ── */
  .detail-row {{ display: none; background: #f8fafc; }}
  .detail-row.open {{ display: table-row; }}
  .detail-panel {{
    padding: 16px 20px;
    border-left: 3px solid #cbd5e1;
    margin: 4px 14px 8px;
    border-radius: 0 6px 6px 0;
  }}
  .detail-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .detail-block h4 {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #94a3b8;
    margin-bottom: 6px;
  }}
  .detail-block p {{
    font-size: 13px;
    color: #334155;
    line-height: 1.6;
    white-space: pre-wrap;
  }}
  .sources-list {{ list-style: none; }}
  .sources-list li {{
    font-size: 12px;
    color: #475569;
    padding: 3px 0;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .sources-list .rank {{
    font-size: 10px;
    font-weight: 700;
    color: #94a3b8;
    min-width: 20px;
  }}
  .sources-list .src-pattern {{ color: #2563eb; font-weight: 600; }}
  .sources-list .src-score {{ color: #94a3b8; font-size: 11px; margin-left: auto; }}

  .expand-icon {{ float: right; color: #94a3b8; font-size: 12px; }}

  /* ── Responsive ── */
  @media (max-width: 700px) {{
    .detail-grid {{ grid-template-columns: 1fr; }}
    .metric-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .page-header {{ padding: 20px 16px; }}
    .content {{ padding: 16px; }}
    .gate-banner {{ padding: 12px 16px; }}
  }}
</style>
</head>
<body>

<div class="page-header">
  <h1>Evaluation Report</h1>
  <div class="sub">Phase: <strong>{phase}</strong></div>
  <div class="header-meta">
    <span class="meta-pill">Run: <strong>{run_at}</strong></span>
    <span class="meta-pill">Top-K: <strong>{k}</strong></span>
    <span class="meta-pill">Total cases: <strong>{n_total}</strong></span>
    <span class="meta-pill">Positives: <strong>{n_positives}</strong></span>
    <span class="meta-pill">Negatives: <strong>{n_negatives}</strong></span>
    <span class="meta-pill">Error code cases: <strong>{n_error_code}</strong></span>
    <span class="meta-pill">Escalation cases: <strong>{n_escalation}</strong></span>
  </div>
</div>

<div class="gate-banner {gate_status_cls}">
  <span>{gate_text}</span>
  <div class="gate-stats">
    <span class="stat"><strong>{n_pass}</strong> passing</span>
    <span class="stat"><strong>{n_fail}</strong> failing</span>
    {"<span class='stat'><strong>" + str(n_missing) + "</strong> not evaluated</span>" if n_missing else ""}
  </div>
</div>

<div class="content">

  <div class="section-title">Metric Scores vs Gate C Targets</div>
  {group_html}

  <div class="section-title" style="margin-top:32px">Test Case Results</div>

  <div class="filter-bar">
    <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
    <button class="filter-btn" onclick="setFilter('error_code', this)">Error Code</button>
    <button class="filter-btn" onclick="setFilter('symptom', this)">Symptom</button>
    <button class="filter-btn" onclick="setFilter('triage', this)">Triage</button>
    <button class="filter-btn" onclick="setFilter('procedure', this)">Procedure</button>
    <button class="filter-btn" onclick="setFilter('negative', this)">Negative</button>
    <button class="filter-btn" onclick="setFilter('escalation', this)">Escalation</button>
    <button class="filter-btn" onclick="setFilter('miss', this)">Misses only</button>
    <input class="search-box" id="searchBox" placeholder="Search queries..." oninput="renderTable()"/>
  </div>

  <div class="results-count" id="resultsCount"></div>

  <table class="cases-table" id="casesTable">
    <thead>
      <tr>
        <th>#</th>
        <th>Query</th>
        <th>Type</th>
        <th>Expected Pattern</th>
        <th>Retrieval</th>
        <th>Retrieved Sources</th>
      </tr>
    </thead>
    <tbody id="casesBody"></tbody>
  </table>
</div>

<script>
const RESULTS = {results_json};
const TYPE_LABELS = {json.dumps(QUERY_TYPE_LABELS)};

let activeFilter = 'all';

function setFilter(f, btn) {{
  activeFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderTable();
}}

function renderTable() {{
  const search = (document.getElementById('searchBox').value || '').toLowerCase();
  const tbody = document.getElementById('casesBody');
  tbody.innerHTML = '';

  let filtered = RESULTS.filter(r => {{
    if (activeFilter === 'miss' && r.hit) return false;
    if (activeFilter !== 'all' && activeFilter !== 'miss' && r.query_type !== activeFilter) return false;
    if (search && !r.query.toLowerCase().includes(search)) return false;
    return true;
  }});

  document.getElementById('resultsCount').textContent =
    filtered.length + ' of ' + RESULTS.length + ' cases shown';

  filtered.forEach((r, idx) => {{
    const hitBadge = r.hit
      ? '<span class="badge-hit">HIT</span>'
      : '<span class="badge-miss">MISS</span>';

    const expectedTags = (r.expected_pattern_ids || [])
      .map(p => `<span class="pattern-tag">${{p}}</span>`).join(' ');

    const sourceTags = (r.sources || []).slice(0, 3)
      .map(s => {{
        const isExpected = (r.expected_pattern_ids || []).includes(s.pattern_id);
        const cls = isExpected ? 'retrieved' : '';
        return `<span class="pattern-tag ${{cls}}">${{s.pattern_id}}</span>`;
      }}).join(' ');

    const typeLabel = TYPE_LABELS[r.query_type] || r.query_type;
    const rowCls = r.hit ? 'case-row' : 'case-row row-miss';
    const rowId = 'row-' + idx;
    const detailId = 'detail-' + idx;

    const sourcesList = (r.sources || []).map((s, i) => {{
      const isExpected = (r.expected_pattern_ids || []).includes(s.pattern_id);
      const markCls = isExpected ? 'src-pattern' : '';
      return `<li>
        <span class="rank">#${{i+1}}</span>
        <span class="${{markCls}}">${{s.pattern_id}}</span>
        <span style="color:#94a3b8">·</span>
        <span style="flex:1;font-size:11px;color:#64748b">${{s.section_title}}</span>
        <span class="src-score">${{s.score.toFixed(4)}}</span>
      </li>`;
    }}).join('');

    const mainRow = `
      <tr class="${{rowCls}}" id="${{rowId}}" onclick="toggleDetail(${{idx}})">
        <td style="color:#94a3b8;font-size:12px;white-space:nowrap">${{idx+1}}</td>
        <td><div class="query-text">${{escHtml(r.query)}}</div></td>
        <td><span class="badge-type">${{typeLabel}}</span></td>
        <td>${{expectedTags}}</td>
        <td>${{hitBadge}}</td>
        <td>${{sourceTags}}</td>
      </tr>`;

    const detailRow = `
      <tr class="detail-row" id="${{detailId}}">
        <td colspan="6">
          <div class="detail-panel">
            <div class="detail-grid">
              <div class="detail-block">
                <h4>Ground Truth Answer</h4>
                <p>${{escHtml(r.ground_truth_answer)}}</p>
              </div>
              <div class="detail-block">
                <h4>Generated Answer</h4>
                <p>${{escHtml(r.answer)}}</p>
              </div>
              <div class="detail-block">
                <h4>Retrieved Sources (top ${{(r.sources || []).length}})</h4>
                <ul class="sources-list">${{sourcesList}}</ul>
              </div>
              <div class="detail-block">
                <h4>Expected Sections</h4>
                <p style="font-size:12px">${{(r.expected_section_titles || []).join(', ') || '—'}}</p>
              </div>
            </div>
          </div>
        </td>
      </tr>`;

    tbody.insertAdjacentHTML('beforeend', mainRow + detailRow);
  }});
}}

function toggleDetail(idx) {{
  const detail = document.getElementById('detail-' + idx);
  detail.classList.toggle('open');
}}

function escHtml(str) {{
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}}

renderTable();
</script>
</body>
</html>"""
    return html


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML eval report")
    parser.add_argument("--phase", required=True, help="Phase name e.g. phase6_production")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="Output directory")
    args = parser.parse_args()

    src = RESULTS_DIR / f"{args.phase}.json"
    if not src.exists():
        print(f"ERROR: {src} not found.")
        return

    data = json.loads(src.read_text(encoding="utf-8"))
    html = build_html(data, args.phase)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{args.phase}.html"
    out_file.write_text(html, encoding="utf-8")
    print(f"Report written to: {out_file}")


if __name__ == "__main__":
    main()
