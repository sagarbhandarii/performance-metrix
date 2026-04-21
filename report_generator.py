#!/usr/bin/env python3
"""Generate responsive HTML reports for unified runtime + startup benchmarks."""

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


def _startup_block(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("startup_metrics", data)


def _runtime_block(data: Dict[str, Any]) -> Dict[str, Any]:
    if "runtime_metrics" in data:
        return data["runtime_metrics"]
    legacy = data.get("metrics", {})
    return {
        "cpu": legacy.get("cpu_percent", "N/A"),
        "memory": legacy.get("memory_mb", "N/A"),
        "fps": legacy.get("fps", "N/A"),
    }


def _device_startup_avg(device_data: Dict[str, Any]) -> float | None:
    startup = _startup_block(device_data)
    values = []
    for phase in ("cold", "warm", "hot"):
        avg = _to_number(startup.get(phase, {}).get("avg"))
        if avg is not None:
            values.append(avg)
    return round(mean(values), 2) if values else None


def _compute_summary(results: Dict[str, Any]) -> Dict[str, Any]:
    device_scores: List[Tuple[str, float]] = []
    all_values: List[float] = []

    for device_id, data in results.items():
        device_avg = _device_startup_avg(data)
        if device_avg is not None:
            device_scores.append((device_id, device_avg))

        startup = _startup_block(data)
        for phase in ("cold", "warm", "hot"):
            all_values.extend(
                [float(v) for v in startup.get(phase, {}).get("values", []) if isinstance(v, (int, float))]
            )

    fastest = min(device_scores, key=lambda x: x[1])[0] if device_scores else "N/A"
    slowest = max(device_scores, key=lambda x: x[1])[0] if device_scores else "N/A"
    overall_avg = round(mean(all_values), 2) if all_values else "N/A"
    return {"fastest": fastest, "slowest": slowest, "overall_avg": overall_avg}


def _metric_cell_css(metric: str, value: Any) -> str:
    number = _to_number(value)
    if number is None:
        return ""
    if metric == "cpu":
        return "metric-bad" if number >= 75 else ""
    if metric == "memory":
        return "metric-warn" if number >= 600 else ""
    return ""


def _startup_cell_css(value: Any) -> str:
    number = _to_number(value)
    if number is None:
        return ""
    return "metric-good" if number <= 1500 else ""


def _build_runtime_table_rows(results: Dict[str, Any]) -> str:
    rows = []
    for device_id, data in results.items():
        runtime = _runtime_block(data)
        cpu = runtime.get("cpu", "N/A")
        memory = runtime.get("memory", "N/A")
        fps = runtime.get("fps", "N/A")
        rows.append(
            "".join(
                [
                    "<tr>",
                    f"<td>{device_id}</td>",
                    f"<td class='{_metric_cell_css('cpu', cpu)}'>{cpu}</td>",
                    f"<td class='{_metric_cell_css('memory', memory)}'>{memory}</td>",
                    f"<td>{fps}</td>",
                    "</tr>",
                ]
            )
        )
    return "\n".join(rows) if rows else "<tr><td colspan='4'>No results</td></tr>"


def _build_startup_table_rows(results: Dict[str, Any]) -> str:
    rows = []
    for device_id, data in results.items():
        startup = _startup_block(data)
        cold = startup.get("cold", {}).get("avg", "N/A")
        warm = startup.get("warm", {}).get("avg", "N/A")
        hot = startup.get("hot", {}).get("avg", "N/A")
        rows.append(
            "".join(
                [
                    "<tr>",
                    f"<td>{device_id}</td>",
                    f"<td class='{_startup_cell_css(cold)}'>{cold}</td>",
                    f"<td class='{_startup_cell_css(warm)}'>{warm}</td>",
                    f"<td class='{_startup_cell_css(hot)}'>{hot}</td>",
                    "</tr>",
                ]
            )
        )
    return "\n".join(rows) if rows else "<tr><td colspan='4'>No results</td></tr>"


def _build_device_cards(results: Dict[str, Any]) -> str:
    cards = []
    for index, (device_id, _) in enumerate(results.items()):
        cards.append(
            f"""
            <section class=\"card\">
              <h3>{device_id}</h3>
              <div class=\"charts\">
                <canvas id=\"startup_bar_{index}\"></canvas>
                <canvas id=\"startup_line_{index}\"></canvas>
                <canvas id=\"runtime_bar_{index}\"></canvas>
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
      const startup = data.startup_metrics ?? data;
      const runtime = data.runtime_metrics ?? {{
        cpu: data.metrics?.cpu_percent ?? null,
        memory: data.metrics?.memory_mb ?? null,
        fps: data.metrics?.fps ?? null
      }};

      const coldAvg = typeof startup.cold?.avg === 'number' ? startup.cold.avg : null;
      const warmAvg = typeof startup.warm?.avg === 'number' ? startup.warm.avg : null;
      const hotAvg = typeof startup.hot?.avg === 'number' ? startup.hot.avg : null;

      new Chart(document.getElementById(`startup_bar_${{idx}}`), {{
        type: 'bar',
        data: {{
          labels: ['Cold', 'Warm', 'Hot'],
          datasets: [{{
            label: `${{deviceId}} Startup Average (ms)`,
            data: [coldAvg, warmAvg, hotAvg],
            backgroundColor: ['#ef4444', '#f59e0b', '#22c55e']
          }}]
        }},
        options: {{ responsive: true, maintainAspectRatio: false }}
      }});

      const coldValues = Array.isArray(startup.cold?.values) ? startup.cold.values : [];
      const warmValues = Array.isArray(startup.warm?.values) ? startup.warm.values : [];
      const hotValues = Array.isArray(startup.hot?.values) ? startup.hot.values : [];
      const iterations = Math.max(coldValues.length, warmValues.length, hotValues.length, 10);
      const labels = Array.from({{length: iterations}}, (_, i) => i + 1);

      new Chart(document.getElementById(`startup_line_${{idx}}`), {{
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

      const cpu = typeof runtime.cpu === 'number' ? runtime.cpu : null;
      const memory = typeof runtime.memory === 'number' ? runtime.memory : null;
      const fps = typeof runtime.fps === 'number' ? runtime.fps : null;

      new Chart(document.getElementById(`runtime_bar_${{idx}}`), {{
        type: 'bar',
        data: {{
          labels: ['CPU %', 'Memory MB', 'FPS'],
          datasets: [{{
            label: `${{deviceId}} Runtime Metrics`,
            data: [cpu, memory, fps],
            backgroundColor: ['#ef4444', '#f59e0b', '#3b82f6']
          }}]
        }},
        options: {{ responsive: true, maintainAspectRatio: false }}
      }});
    }});
    """


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    summary = _compute_summary(results)
    runtime_rows = _build_runtime_table_rows(results)
    startup_rows = _build_startup_table_rows(results)
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
    canvas {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; min-height: 260px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; text-align: left; padding: 8px; }}
    .metric-bad {{ color: #dc2626; font-weight: 700; }}
    .metric-warn {{ color: #ea580c; font-weight: 700; }}
    .metric-good {{ color: #16a34a; font-weight: 700; }}
    @media (max-width: 920px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class=\"container\">
    <section class=\"card\">
      <h1>Unified Android Performance Report</h1>
      <div class=\"summary\">
        <div class=\"card\"><strong>Fastest Startup Device</strong><br/>{summary['fastest']}</div>
        <div class=\"card\"><strong>Slowest Startup Device</strong><br/>{summary['slowest']}</div>
        <div class=\"card\"><strong>Overall Startup Average (ms)</strong><br/>{summary['overall_avg']}</div>
      </div>
    </section>

    <section class=\"card\">
      <h2>Runtime Performance</h2>
      <table>
        <thead><tr><th>Device</th><th>CPU %</th><th>Memory (MB)</th><th>FPS</th></tr></thead>
        <tbody>{runtime_rows}</tbody>
      </table>
    </section>

    <section class=\"card\">
      <h2>Startup Performance</h2>
      <table>
        <thead><tr><th>Device</th><th>Cold Avg</th><th>Warm Avg</th><th>Hot Avg</th></tr></thead>
        <tbody>{startup_rows}</tbody>
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
