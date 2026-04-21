#!/usr/bin/env python3
"""Generate responsive HTML reports for launch benchmarks."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Tuple

import logging_config

INPUT_FILE = Path("final_results.json")
OUTPUT_FILE = Path("report.html")
LOGGER = logging_config.get_logger("report_generator")


def _to_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _device_avg(device_data: Dict[str, Any]) -> float | None:
    values = []
    for phase in ("cold", "warm", "hot"):
        avg = _to_number(device_data.get(phase, {}).get("avg"))
        if avg is not None:
            values.append(avg)
    return round(mean(values), 2) if values else None


def _compute_summary(results: Dict[str, Any]) -> Dict[str, Any]:
    device_scores: List[Tuple[str, float]] = []
    all_values: List[float] = []

    for device_id, data in results.items():
        device_avg = _device_avg(data)
        if device_avg is not None:
            device_scores.append((device_id, device_avg))
        for phase in ("cold", "warm", "hot"):
            all_values.extend([float(v) for v in data.get(phase, {}).get("values", []) if isinstance(v, (int, float))])

    fastest = min(device_scores, key=lambda x: x[1])[0] if device_scores else "N/A"
    slowest = max(device_scores, key=lambda x: x[1])[0] if device_scores else "N/A"
    overall_avg = round(mean(all_values), 2) if all_values else "N/A"
    return {"fastest": fastest, "slowest": slowest, "overall_avg": overall_avg}


def _build_table_rows(results: Dict[str, Any]) -> str:
    rows = []
    for device_id, data in results.items():
        cold = data.get("cold", {}).get("avg", "N/A")
        warm = data.get("warm", {}).get("avg", "N/A")
        hot = data.get("hot", {}).get("avg", "N/A")
        rows.append(
            f"<tr><td>{device_id}</td><td>{cold}</td><td>{warm}</td><td>{hot}</td></tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='4'>No results</td></tr>"


def _build_device_cards(results: Dict[str, Any]) -> str:
    cards = []
    for index, (device_id, data) in enumerate(results.items()):
        cards.append(
            f"""
            <section class=\"card\">
              <h3>{device_id}</h3>
              <div class=\"charts\">
                <canvas id=\"bar_{index}\"></canvas>
                <canvas id=\"line_{index}\"></canvas>
              </div>
            </section>
            """.strip()
        )
    return "\n".join(cards)


def _build_chart_script(results: Dict[str, Any]) -> str:
    data_json = json.dumps(results)
    return f"""
    const reportData = {data_json};
    Object.entries(reportData).forEach(([deviceId, data], idx) => {{
      const coldAvg = typeof data.cold?.avg === 'number' ? data.cold.avg : null;
      const warmAvg = typeof data.warm?.avg === 'number' ? data.warm.avg : null;
      const hotAvg = typeof data.hot?.avg === 'number' ? data.hot.avg : null;

      new Chart(document.getElementById(`bar_${{idx}}`), {{
        type: 'bar',
        data: {{
          labels: ['Cold', 'Warm', 'Hot'],
          datasets: [{{
            label: `${{deviceId}} Average Launch (ms)`,
            data: [coldAvg, warmAvg, hotAvg],
            backgroundColor: ['#ef4444', '#f59e0b', '#22c55e']
          }}]
        }},
        options: {{ responsive: true, maintainAspectRatio: false }}
      }});

      const coldValues = Array.isArray(data.cold?.values) ? data.cold.values : [];
      const warmValues = Array.isArray(data.warm?.values) ? data.warm.values : [];
      const hotValues = Array.isArray(data.hot?.values) ? data.hot.values : [];
      const iterations = Math.max(coldValues.length, warmValues.length, hotValues.length, 10);
      const labels = Array.from({{length: iterations}}, (_, i) => i + 1);

      new Chart(document.getElementById(`line_${{idx}}`), {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{ label: 'Cold', data: coldValues, borderColor: '#ef4444', fill: false }},
            {{ label: 'Warm', data: warmValues, borderColor: '#f59e0b', fill: false }},
            {{ label: 'Hot', data: hotValues, borderColor: '#22c55e', fill: false }}
          ]
        }},
        options: {{ responsive: true, maintainAspectRatio: false }}
      }});
    }});
    """


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    summary = _compute_summary(results)
    table_rows = _build_table_rows(results)
    device_cards = _build_device_cards(results)
    charts_script = _build_chart_script(results)

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Android Performance Report</title>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f5f7fb; color: #111827; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .card {{ background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 8px rgba(0,0,0,.08); }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    canvas {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; min-height: 280px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; text-align: left; padding: 8px; }}
    @media (max-width: 920px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class=\"container\">
    <section class=\"card\">
      <h1>Android Launch Benchmark Report</h1>
      <div class=\"summary\">
        <div class=\"card\"><strong>Fastest Device</strong><br/>{summary['fastest']}</div>
        <div class=\"card\"><strong>Slowest Device</strong><br/>{summary['slowest']}</div>
        <div class=\"card\"><strong>Overall Average (ms)</strong><br/>{summary['overall_avg']}</div>
      </div>
    </section>

    <section class=\"card\">
      <h2>Device Summary Table</h2>
      <table>
        <thead><tr><th>Device</th><th>Cold Avg</th><th>Warm Avg</th><th>Hot Avg</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </section>

    {device_cards}
  </div>
  <script>{charts_script}</script>
</body>
</html>"""

    output_file.write_text(html, encoding="utf-8")
    LOGGER.info("Report generated: %s", output_file)
    return output_file


def main() -> None:
    logging_config.setup_logging(False)
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input JSON: {INPUT_FILE}")
    results = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    generate_report_from_results(results, OUTPUT_FILE)


if __name__ == "__main__":
    main()
