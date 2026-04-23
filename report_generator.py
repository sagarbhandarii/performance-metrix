#!/usr/bin/env python3
"""Generate a unified single-page HTML performance report with Chart.js charts."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

import logging_config

INPUT_FILE = Path("final_results.json")
OUTPUT_FILE = Path("report.html")
STATIC_DIR = Path("static")
CHART_JS_FILE = STATIC_DIR / "chart.min.js"
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
LOGGER = logging_config.get_logger("report_generator")

# Lightweight fallback when CDN download is unavailable.
MINI_CHART_JS = """(function(){
function pick(v,d){return (typeof v==='number'&&isFinite(v))?v:d;}
function maxIn(ds){var m=0;ds.forEach(function(d){(d.data||[]).forEach(function(v){if(typeof v==='number'&&v>m)m=v;});});return m||1;}
function drawAxes(ctx,w,h,p){ctx.strokeStyle='#333';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(p,h-p);ctx.lineTo(w-p,h-p);ctx.lineTo(w-p,p);ctx.stroke();}
function drawBar(ctx,cfg){var ds=(cfg.data&&cfg.data.datasets)||[];var labels=(cfg.data&&cfg.data.labels)||[];var set=ds[0]||{data:[]};var vals=set.data||[];var colors=set.backgroundColor||[];var w=ctx.canvas.width,h=ctx.canvas.height,p=30;ctx.clearRect(0,0,w,h);drawAxes(ctx,w,h,p);var m=Math.max(maxIn(ds),1);var n=Math.max(vals.length,1);var space=(w-2*p)/n;var bw=space*0.6;ctx.font='12px Arial';ctx.textAlign='center';for(var i=0;i<n;i++){var v=pick(vals[i],0);var bh=((h-2*p)*(v/m));var x=p+i*space+(space-bw)/2;var y=h-p-bh;ctx.fillStyle=colors[i]||'#3b82f6';ctx.fillRect(x,y,bw,bh);ctx.fillStyle='#111';ctx.fillText(labels[i]||String(i+1),x+bw/2,h-p+14);} }
function drawLine(ctx,cfg){var ds=(cfg.data&&cfg.data.datasets)||[];var labels=(cfg.data&&cfg.data.labels)||[];var w=ctx.canvas.width,h=ctx.canvas.height,p=30;ctx.clearRect(0,0,w,h);drawAxes(ctx,w,h,p);var m=Math.max(maxIn(ds),1);var n=Math.max(labels.length,1);var step=(w-2*p)/Math.max(n-1,1);ctx.font='12px Arial';ctx.textAlign='center';for(var l=0;l<labels.length;l++){ctx.fillStyle='#111';ctx.fillText(labels[l],p+l*step,h-p+14);}ds.forEach(function(d){var data=d.data||[];ctx.strokeStyle=d.borderColor||'#ef4444';ctx.lineWidth=2;ctx.beginPath();for(var i=0;i<n;i++){var v=pick(data[i],0);var x=p+i*step;var y=h-p-((h-2*p)*(v/m));if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();});}
function Chart(target,config){if(!(this instanceof Chart))return new Chart(target,config);var canvas=target&&target.getContext?target:(typeof target==='string'?document.getElementById(target):null);if(!canvas)throw new Error('Canvas not found');var ctx=canvas.getContext('2d');if(!ctx)throw new Error('2D context unavailable');if(config&&config.type==='line')drawLine(ctx,config);else drawBar(ctx,config);this.canvas=canvas;this.config=config;}
window.Chart=Chart;
})();"""


def _ensure_local_chart_js() -> None:
    """Ensure Chart.js exists at ./static/chart.min.js (download, then fallback)."""
    if CHART_JS_FILE.exists() and CHART_JS_FILE.stat().st_size > 0:
        return

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    chart_data = b""

    try:
        LOGGER.info("Downloading Chart.js to %s", CHART_JS_FILE)
        with urllib.request.urlopen(CHART_JS_URL, timeout=30) as response:
            chart_data = response.read()
    except Exception as exc:  # pragma: no cover - depends on network access
        LOGGER.warning("Chart.js download failed (%s). Using fallback renderer.", exc)

    if chart_data:
        CHART_JS_FILE.write_bytes(chart_data)
    else:
        CHART_JS_FILE.write_text(MINI_CHART_JS, encoding="utf-8")


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _as_list(values: Any) -> List[float]:
    if not isinstance(values, list):
        return []
    out: List[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _metric_value(source: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in source:
            try:
                return float(source[key])
            except (TypeError, ValueError):
                return None
    return None


def _normalize_results(results: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(results, dict):
        return {}
    # If data is directly startup_metrics/runtime_metrics without device map, wrap it.
    if "startup_metrics" in results or "runtime_metrics" in results:
        return {"Device": results}
    return {str(device): data for device, data in results.items() if isinstance(data, dict)}


def _collect_rows(devices: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    runtime_rows: List[Dict[str, Any]] = []
    startup_rows: List[Dict[str, Any]] = []

    for device, device_data in devices.items():
        runtime = device_data.get("runtime_metrics", {}) if isinstance(device_data, dict) else {}
        startup = device_data.get("startup_metrics", {}) if isinstance(device_data, dict) else {}

        runtime_rows.append(
            {
                "device": device,
                "cpu": _metric_value(runtime, "cpu", "cpu_percent", "cpu_usage", "avg_cpu"),
                "memory": _metric_value(runtime, "memory", "memory_mb", "avg_memory", "memory_usage"),
                "fps": _metric_value(runtime, "fps", "avg_fps", "frame_rate"),
                "gc_count": _metric_value(runtime, "gc_count"),
            }
        )

        startup_rows.append(
            {
                "device": device,
                "cold_avg": _metric_value(startup.get("cold", {}), "avg"),
                "warm_avg": _metric_value(startup.get("warm", {}), "avg"),
                "hot_avg": _metric_value(startup.get("hot", {}), "avg"),
                "cold_values": _as_list(startup.get("cold", {}).get("values")),
                "warm_values": _as_list(startup.get("warm", {}).get("values")),
                "hot_values": _as_list(startup.get("hot", {}).get("values")),
            }
        )

    return runtime_rows, startup_rows


def _avg(values: List[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _build_summary(startup_rows: List[Dict[str, Any]]) -> Dict[str, str]:
    averages: List[Tuple[str, float]] = []
    per_device_scores: List[float] = []

    for row in startup_rows:
        parts = [v for v in [row["cold_avg"], row["warm_avg"], row["hot_avg"]] if isinstance(v, float)]
        if parts:
            device_avg = sum(parts) / len(parts)
            averages.append((row["device"], device_avg))
            per_device_scores.append(device_avg)

    if not averages:
        return {
            "fastest": "N/A",
            "slowest": "N/A",
            "overall": "N/A",
        }

    fastest_device, fastest_value = min(averages, key=lambda x: x[1])
    slowest_device, slowest_value = max(averages, key=lambda x: x[1])
    overall = _avg(per_device_scores)

    return {
        "fastest": f"{fastest_device} ({fastest_value:.2f} ms)",
        "slowest": f"{slowest_device} ({slowest_value:.2f} ms)",
        "overall": f"{overall:.2f} ms" if overall is not None else "N/A",
    }


def _runtime_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"5\">No runtime data available</td></tr>"

    def _perf_class_cpu(cpu: float | None) -> str:
        if not isinstance(cpu, float):
            return ""
        if cpu > 70:
            return "metric-bad"
        if cpu < 40:
            return "metric-good"
        return "metric-medium"

    def _perf_class_fps(fps: float | None) -> str:
        if not isinstance(fps, float):
            return ""
        if fps >= 55:
            return "metric-good"
        if fps >= 35:
            return "metric-medium"
        return "metric-bad"

    rows_html: List[str] = []
    for row in rows:
        rows_html.append(
            "<tr>"
            f"<td>{row['device']}</td>"
            f"<td class=\"{_perf_class_cpu(row['cpu'])}\">{_fmt_num(row['cpu'])}</td>"
            f"<td>{_fmt_num(row['memory'])}</td>"
            f"<td class=\"{_perf_class_fps(row['fps'])}\">{_fmt_num(row['fps'])}</td>"
            f"<td>{_fmt_num(row['gc_count'], 0)}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def _startup_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"4\">No startup data available</td></tr>"

    def _perf_class_startup(startup_ms: float | None) -> str:
        if not isinstance(startup_ms, float):
            return ""
        if startup_ms <= 2000:
            return "metric-good"
        if startup_ms <= 3500:
            return "metric-medium"
        return "metric-bad"

    rows_html: List[str] = []
    for row in rows:
        rows_html.append(
            "<tr>"
            f"<td>{row['device']}</td>"
            f"<td class=\"{_perf_class_startup(row['cold_avg'])}\">{_fmt_num(row['cold_avg'])}</td>"
            f"<td class=\"{_perf_class_startup(row['warm_avg'])}\">{_fmt_num(row['warm_avg'])}</td>"
            f"<td class=\"{_perf_class_startup(row['hot_avg'])}\">{_fmt_num(row['hot_avg'])}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def _chart_payload(startup_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cold_avg = _avg([row["cold_avg"] for row in startup_rows if isinstance(row["cold_avg"], float)]) or 0.0
    warm_avg = _avg([row["warm_avg"] for row in startup_rows if isinstance(row["warm_avg"], float)]) or 0.0
    hot_avg = _avg([row["hot_avg"] for row in startup_rows if isinstance(row["hot_avg"], float)]) or 0.0

    cold_values: List[float] = []
    warm_values: List[float] = []
    hot_values: List[float] = []
    for row in startup_rows:
        cold_values.extend(row["cold_values"])
        warm_values.extend(row["warm_values"])
        hot_values.extend(row["hot_values"])

    def _pad(values: List[float], target: int = 10) -> List[float]:
        trimmed = values[:target]
        if len(trimmed) < target:
            trimmed.extend([0.0] * (target - len(trimmed)))
        return trimmed

    return {
        "startup_metrics": {
            "cold": {"avg": round(cold_avg, 2), "values": _pad(cold_values)},
            "warm": {"avg": round(warm_avg, 2), "values": _pad(warm_values)},
            "hot": {"avg": round(hot_avg, 2), "values": _pad(hot_values)},
        }
    }


def _html_report(summary: Dict[str, str], runtime_rows: str, startup_rows: str, chart_data_json: str) -> str:
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Unified Android Performance Report</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      background: #f5f7fa;
      margin: 0;
      padding: 20px;
      color: #1f2937;
    }}

    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}

    .card {{
      background: white;
      padding: 20px;
      margin-bottom: 20px;
      border-radius: 10px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}

    h1 {{
      text-align: center;
      margin-bottom: 30px;
    }}

    h2 {{
      margin-top: 0;
      margin-bottom: 16px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
    }}

    th {{
      background: #4CAF50;
      color: white;
      padding: 10px;
      text-align: left;
    }}

    td {{
      padding: 10px;
      border-bottom: 1px solid #ddd;
    }}

    .summary {{
      display: flex;
      gap: 20px;
    }}

    .summary-card {{
      flex: 1;
      background: #ffffff;
      padding: 15px;
      border-radius: 10px;
      text-align: center;
      font-weight: bold;
      box-shadow: 0 2px 6px rgba(0,0,0,0.1);
      border-top: 4px solid #4CAF50;
    }}

    .summary-card .label {{
      display: block;
      font-size: 14px;
      color: #6b7280;
      margin-bottom: 8px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }}

    .summary-card .value {{
      display: block;
      font-size: 18px;
      color: #111827;
    }}

    .metric-good {{
      color: #15803d;
      font-weight: 700;
    }}

    .metric-medium {{
      color: #c2410c;
      font-weight: 700;
    }}

    .metric-bad {{
      color: #b91c1c;
      font-weight: 700;
    }}

    .chart-wrap {{
      display: grid;
      gap: 20px;
    }}

    .chart-panel {{
      background: #fafafa;
      border: 1px solid #eceff3;
      border-radius: 10px;
      padding: 12px;
    }}

    .chart-panel h3 {{
      margin: 0 0 12px;
      font-size: 16px;
    }}

    canvas {{
      width: 100%;
      height: 340px;
    }}

    @media (max-width: 768px) {{
      .summary {{
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
<div class=\"container\">
  <h1>Unified Android Performance Report</h1>

<div class=\"card\">
  <h2>Summary</h2>
  <div class=\"summary\">
    <div class=\"summary-card\">
      <span class=\"label\">Fastest Device</span>
      <span class=\"value\">{summary['fastest']}</span>
    </div>
    <div class=\"summary-card\">
      <span class=\"label\">Slowest Device</span>
      <span class=\"value\">{summary['slowest']}</span>
    </div>
    <div class=\"summary-card\">
      <span class=\"label\">Avg Time</span>
      <span class=\"value\">{summary['overall']}</span>
    </div>
  </div>
</div>

<div class=\"card\">
  <h2>Runtime Performance</h2>
  <table>
    <tr>
      <th>Device</th>
      <th>CPU %</th>
      <th>Memory (MB)</th>
      <th>FPS</th>
      <th>GC Count</th>
    </tr>
    {runtime_rows}
  </table>
</div>

<div class=\"card\">
  <h2>Startup Performance</h2>
  <table>
    <tr>
      <th>Device</th>
      <th>Cold Avg</th>
      <th>Warm Avg</th>
      <th>Hot Avg</th>
    </tr>
    {startup_rows}
  </table>
</div>

<div class=\"card\">
  <h2>Performance Charts</h2>
  <div class=\"chart-wrap\">
    <div class=\"chart-panel\">
      <h3>Average Launch Time by Mode</h3>
      <canvas id=\"barChart\"></canvas>
    </div>
    <div class=\"chart-panel\">
      <h3>Launch Time Trend (Top 10 Samples)</h3>
      <canvas id=\"lineChart\"></canvas>
    </div>
  </div>
</div>
</div>

<script src=\"static/chart.min.js\"></script>
<script>
document.addEventListener(\"DOMContentLoaded\", function () {{
  const data = {chart_data_json};

  const ctx = document.getElementById(\"barChart\");
  const ctx2 = document.getElementById(\"lineChart\");

  if (!ctx || !ctx2 || typeof Chart === 'undefined') {{
    return;
  }}

  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: ['Cold','Warm','Hot'],
      datasets: [{{
        label: 'Avg Launch Time',
        data: [
          data.startup_metrics.cold.avg,
          data.startup_metrics.warm.avg,
          data.startup_metrics.hot.avg
        ],
        backgroundColor: ['#ef4444', '#f59e0b', '#22c55e']
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      layout: {{ padding: 12 }},
      plugins: {{
        legend: {{ display: true, position: 'top' }}
      }}
    }}
  }});

  new Chart(ctx2, {{
    type: 'line',
    data: {{
      labels: [1,2,3,4,5,6,7,8,9,10],
      datasets: [
        {{ label: 'Cold', data: data.startup_metrics.cold.values, borderColor: '#ef4444', fill: false }},
        {{ label: 'Warm', data: data.startup_metrics.warm.values, borderColor: '#f59e0b', fill: false }},
        {{ label: 'Hot', data: data.startup_metrics.hot.values, borderColor: '#22c55e', fill: false }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      layout: {{ padding: 12 }},
      plugins: {{
        legend: {{ display: true, position: 'top' }}
      }}
    }}
  }});
}});
</script>

</body>
</html>
"""


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    _ensure_local_chart_js()

    devices = _normalize_results(results)
    runtime_data, startup_data = _collect_rows(devices)

    summary = _build_summary(startup_data)
    runtime_rows = _runtime_rows_html(runtime_data)
    startup_rows = _startup_rows_html(startup_data)
    chart_data_json = json.dumps(_chart_payload(startup_data))

    html = _html_report(summary, runtime_rows, startup_rows, chart_data_json)
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
