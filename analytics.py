"""Analytics over the audit log (Stretch: analytics dashboard).

Pure read-side aggregation — no new storage. Powers GET /analytics (JSON) and the
GET /dashboard HTML view.
"""

import audit


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def compute_metrics(db_path: str = audit.DB_PATH) -> dict:
    """Aggregate detection patterns and appeal behavior from the audit log."""
    entries = audit.get_log(limit=100000, db_path=db_path)
    classified = [e for e in entries if e.get("event") == "classified"]
    appeals = [e for e in entries if e.get("event") == "appeal"]

    n = len(classified)
    bands = {"likely_ai": 0, "uncertain": 0, "likely_human": 0}
    disagreements = []
    for e in classified:
        band = e.get("attribution")
        if band in bands:
            bands[band] += 1
        scores = [e.get("llm_score"), e.get("style_score"), e.get("phrase_score")]
        scores = [s for s in scores if s is not None]
        if len(scores) >= 2:
            disagreements.append(max(scores) - min(scores))

    band_distribution = {
        b: {"count": c, "pct": round(100 * c / n, 1) if n else 0.0}
        for b, c in bands.items()
    }

    return {
        "total_classifications": n,
        "total_appeals": len(appeals),
        "appeal_rate_pct": round(100 * len(appeals) / n, 1) if n else 0.0,
        "band_distribution": band_distribution,
        "avg_signal_disagreement": _mean(disagreements),
        "mean_signal_scores": {
            "llm": _mean([e.get("llm_score") for e in classified]),
            "style": _mean([e.get("style_score") for e in classified]),
            "phrase": _mean([e.get("phrase_score") for e in classified]),
        },
    }


def _bar(pct: float, width: int = 24) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "·" * (width - filled)


def render_dashboard_html(metrics: dict) -> str:
    """Minimal self-contained HTML view of the metrics (no external assets)."""
    bd = metrics["band_distribution"]
    rows = "".join(
        f"<tr><td>{band}</td><td class='num'>{d['count']}</td>"
        f"<td class='num'>{d['pct']}%</td>"
        f"<td class='bar'>{_bar(d['pct'])}</td></tr>"
        for band, d in bd.items()
    )
    ms = metrics["mean_signal_scores"]
    dis = metrics["avg_signal_disagreement"]
    dis_txt = "n/a" if dis is None else f"{dis:.3f}"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Provenance Guard — Analytics</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 640px;
         margin: 40px auto; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .card {{ border: 1px solid #e2e2e2; border-radius: 10px; padding: 18px 22px;
          margin: 16px 0; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 6px 8px; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar {{ font-family: ui-monospace, monospace; color: #4f46e5; letter-spacing: -1px; }}
  .big {{ font-size: 1.8rem; font-weight: 700; }}
  .muted {{ color: #6b7280; font-size: .85rem; }}
</style></head><body>
<h1>🛡️ Provenance Guard — Analytics</h1>
<div class="card">
  <div class="muted">Total classifications</div>
  <div class="big">{metrics['total_classifications']}</div>
</div>
<div class="card">
  <div class="muted">Detection patterns (band distribution)</div>
  <table>{rows}</table>
</div>
<div class="card">
  <div class="muted">Appeal rate</div>
  <div class="big">{metrics['appeal_rate_pct']}%</div>
  <div class="muted">{metrics['total_appeals']} appeal(s)</div>
</div>
<div class="card">
  <div class="muted">Average signal disagreement (extra metric)</div>
  <div class="big">{dis_txt}</div>
  <div class="muted">mean spread across the 3 signals — higher = more genuine uncertainty</div>
</div>
<div class="card">
  <div class="muted">Mean signal scores</div>
  <table>
    <tr><td>LLM (semantic)</td><td class="num">{ms['llm']}</td></tr>
    <tr><td>Stylometry (structural)</td><td class="num">{ms['style']}</td></tr>
    <tr><td>Phrase lexicon (lexical)</td><td class="num">{ms['phrase']}</td></tr>
  </table>
</div>
</body></html>"""
