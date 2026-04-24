#!/usr/bin/env python3
"""Generate a unified single-page HTML performance report with Chart.js charts."""

from __future__ import annotations

import json
import urllib.request
from html import escape
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


def _collect_rows(devices: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    runtime_rows: List[Dict[str, Any]] = []
    startup_rows: List[Dict[str, Any]] = []
    device_rows: List[Dict[str, Any]] = []

    for device, device_data in devices.items():
        runtime = device_data.get("runtime_metrics", {}) if isinstance(device_data, dict) else {}
        startup = device_data.get("startup_metrics", {}) if isinstance(device_data, dict) else {}
        details = device_data.get("device_details", {}) if isinstance(device_data, dict) else {}
        error = str(device_data.get("error", "")).strip() if isinstance(device_data, dict) else ""

        runtime_rows.append(
            {
                "device": device,
                "cpu": _metric_value(runtime, "cpu", "cpu_percent", "cpu_usage", "avg_cpu"),
                "memory": _metric_value(runtime, "memory", "memory_mb", "avg_memory", "memory_usage"),
                "fps": _metric_value(runtime, "fps", "avg_fps", "frame_rate"),
                "gc_count": _metric_value(runtime, "gc_count"),
                "error": error,
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

        device_rows.append(
            {
                "device": device,
                "model": str(details.get("model", "N/A")),
                "manufacturer": str(details.get("manufacturer", "N/A")),
                "android_version": str(details.get("android_version", "N/A")),
                "sdk_int": str(details.get("sdk_int", "N/A")),
                "cpu": str(details.get("cpu", "N/A")),
                "total_memory_mb": _metric_value(details, "total_memory_mb"),
                "build_fingerprint": str(details.get("build_fingerprint", "N/A")),
                "error": error,
            }
        )

    return runtime_rows, startup_rows, device_rows


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




def _build_insights(runtime_rows: List[Dict[str, Any]], startup_rows: List[Dict[str, Any]]) -> List[str]:
    insights: List[str] = []

    failed = [row for row in runtime_rows if row.get("error")]
    if failed:
        insights.append(f"{len(failed)} device(s) reported runtime/startup errors.")

    low_fps = [row for row in runtime_rows if isinstance(row.get("fps"), float) and row["fps"] < 30.0]
    if low_fps:
        names = ", ".join(row["device"] for row in low_fps[:4])
        insights.append(f"Low FPS detected (<30) on: {names}.")

    high_gc = [row for row in runtime_rows if isinstance(row.get("gc_count"), float) and row["gc_count"] >= 30]
    if high_gc:
        names = ", ".join(row["device"] for row in high_gc[:4])
        insights.append(f"High GC activity detected on: {names}.")

    for row in startup_rows:
        cold, warm = row.get("cold_avg"), row.get("warm_avg")
        if isinstance(cold, float) and isinstance(warm, float) and warm > 0 and cold > warm * 1.3:
            insights.append(f"{row['device']} has cold start >30% slower than warm start.")

    return insights if insights else ["No major anomalies detected from available metrics."]

def _runtime_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"6\">No runtime data available</td></tr>"

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
            f"<td>{escape(row['device'])}</td>"
            f"<td class=\"{_perf_class_cpu(row['cpu'])}\">{_fmt_num(row['cpu'])}</td>"
            f"<td>{_fmt_num(row['memory'])}</td>"
            f"<td class=\"{_perf_class_fps(row['fps'])}\">{_fmt_num(row['fps'])}</td>"
            f"<td>{_fmt_num(row['gc_count'], 0)}</td>"
            f"<td class=\"{'metric-bad' if row['error'] else 'metric-good'}\">{escape(row['error']) if row['error'] else 'None'}</td>"
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
            f"<td>{escape(row['device'])}</td>"
            f"<td class=\"{_perf_class_startup(row['cold_avg'])}\">{_fmt_num(row['cold_avg'])}</td>"
            f"<td class=\"{_perf_class_startup(row['warm_avg'])}\">{_fmt_num(row['warm_avg'])}</td>"
            f"<td class=\"{_perf_class_startup(row['hot_avg'])}\">{_fmt_num(row['hot_avg'])}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def _device_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"9\">No device details available</td></tr>"

    lines: List[str] = []
    for row in rows:
        os_label = row["android_version"]
        if row["sdk_int"] != "N/A":
            os_label = f"{os_label} (SDK {row['sdk_int']})"
        lines.append(
            "<tr>"
            f"<td>{escape(row['device'])}</td>"
            f"<td>{escape(row['model'])}</td>"
            f"<td>{escape(row['manufacturer'])}</td>"
            f"<td>{escape(os_label)}</td>"
            f"<td>{escape(row['cpu'])}</td>"
            f"<td>{_fmt_num(row['total_memory_mb'])}</td>"
            f"<td class=\"mono\">{escape(row['build_fingerprint'])}</td>"
            f"<td class=\"{'metric-bad' if row['error'] else 'metric-good'}\">{escape(row['error']) if row['error'] else 'OK'}</td>"
            "</tr>"
        )
    return "\n".join(lines)


def _chart_payload(startup_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cold_avg = _avg([row["cold_avg"] for row in startup_rows if isinstance(row["cold_avg"], float)])
    warm_avg = _avg([row["warm_avg"] for row in startup_rows if isinstance(row["warm_avg"], float)])
    hot_avg = _avg([row["hot_avg"] for row in startup_rows if isinstance(row["hot_avg"], float)])

    cold_values: List[float] = []
    warm_values: List[float] = []
    hot_values: List[float] = []
    for row in startup_rows:
        cold_values.extend(row["cold_values"])
        warm_values.extend(row["warm_values"])
        hot_values.extend(row["hot_values"])

    max_points = max(len(cold_values), len(warm_values), len(hot_values), 0)

    def _pad_nullable(values: List[float], target: int) -> List[float | None]:
        out: List[float | None] = [round(v, 2) for v in values[:target]]
        while len(out) < target:
            out.append(None)
        return out

    device_labels = [row["device"] for row in startup_rows]
    device_avg_values = [
        round(
            _avg([value for value in [row["cold_avg"], row["warm_avg"], row["hot_avg"]] if isinstance(value, float)]) or 0.0,
            2,
        )
        for row in startup_rows
    ]

    return {
        "startup_metrics": {
            "cold": {"avg": round(cold_avg, 2) if cold_avg is not None else None, "values": _pad_nullable(cold_values, max_points)},
            "warm": {"avg": round(warm_avg, 2) if warm_avg is not None else None, "values": _pad_nullable(warm_values, max_points)},
            "hot": {"avg": round(hot_avg, 2) if hot_avg is not None else None, "values": _pad_nullable(hot_values, max_points)},
            "labels": list(range(1, max_points + 1)),
        },
        "device_metrics": {"labels": device_labels, "startup_avg_ms": device_avg_values},
    }


def _html_report(summary: Dict[str, str], insights_html: str, runtime_rows: str, startup_rows: str, device_rows: str, chart_data_json: str) -> str:
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
      padding: 22px;
      margin-bottom: 18px;
      border-radius: 12px;
      box-shadow: 0 6px 24px rgba(15,23,42,0.08);
      border: 1px solid #e5e7eb;
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
      background: #0f766e;
      color: white;
      padding: 10px;
      text-align: left;
    }}

    td {{
      padding: 10px;
      border-bottom: 1px solid #ddd;
      vertical-align: top;
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
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      word-break: break-all;
      color: #475569;
    }}
    .section-subtitle {{
      color: #64748b;
      margin: -4px 0 14px;
      font-size: 14px;
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
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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

    .chart-container {{
      position: relative;
      min-height: 340px;
    }}

    .chart-message {{
      display: none;
      align-items: center;
      justify-content: center;
      min-height: 340px;
      text-align: center;
      color: #6b7280;
      font-size: 14px;
      border: 1px dashed #d1d5db;
      border-radius: 8px;
      padding: 12px;
      background: #ffffff;
    }}

    .chart-panel.no-data canvas,
    .chart-panel.error canvas {{
      display: none;
    }}

    .chart-panel.no-data .chart-message,
    .chart-panel.error .chart-message {{
      display: flex;
    }}

    .chart-panel.error .chart-message {{
      color: #b91c1c;
      border-color: #fecaca;
      background: #fff1f2;
      font-weight: 600;
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
  <h2>Insights</h2>
  <ul>
    {insights_html}
  </ul>
</div>

<div class=\"card\">
  <h2>Device Details</h2>
  <p class=\"section-subtitle\">Hardware/OS details for each tested device (quickly identify environment differences).</p>
  <table>
    <tr>
      <th>Device ID</th>
      <th>Model</th>
      <th>Manufacturer</th>
      <th>OS Version</th>
      <th>CPU ABI</th>
      <th>Total Memory (MB)</th>
      <th>Build Fingerprint</th>
      <th>Status</th>
    </tr>
    {device_rows}
  </table>
</div>

<div class=\"card\">
  <h2>Runtime Performance</h2>
  <p class=\"section-subtitle\">Critical runtime metrics. Error column highlights failed/invalid runs.</p>
  <table>
    <tr>
      <th>Device</th>
      <th>CPU %</th>
      <th>Memory (MB)</th>
      <th>FPS</th>
      <th>GC Count</th>
      <th>Error</th>
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
    <div class=\"chart-panel\" id=\"barPanel\">
      <h3>Average Launch Time by Mode</h3>
      <div class=\"chart-container\">
        <canvas id=\"barChart\"></canvas>
        <div class=\"chart-message\" id=\"barChartMessage\">No data available.</div>
      </div>
    </div>
    <div class=\"chart-panel\" id=\"linePanel\">
      <h3>Launch Time Trend</h3>
      <div class=\"chart-container\">
        <canvas id=\"lineChart\"></canvas>
        <div class=\"chart-message\" id=\"lineChartMessage\">No data available.</div>
      </div>
    </div>
    <div class=\"chart-panel\" id=\"devicePanel\">
      <h3>Average Startup Time by Device</h3>
      <div class=\"chart-container\">
        <canvas id=\"deviceChart\"></canvas>
        <div class=\"chart-message\" id=\"deviceChartMessage\">No data available.</div>
      </div>
    </div>
  </div>
</div>
</div>

<script src=\"static/chart.min.js\"></script>
<script>
document.addEventListener(\"DOMContentLoaded\", function () {{
  const data = {chart_data_json};
  const reportPrefix = '[Performance Report]';

  const ctx = document.getElementById(\"barChart\");
  const ctx2 = document.getElementById(\"lineChart\");
  const ctx3 = document.getElementById(\"deviceChart\");
  const barPanel = document.getElementById(\"barPanel\");
  const linePanel = document.getElementById(\"linePanel\");
  const devicePanel = document.getElementById(\"devicePanel\");

  function setPanelMessage(panel, message, isError) {{
    if (!panel) return;
    panel.classList.remove('no-data', 'error');
    panel.classList.add(isError ? 'error' : 'no-data');
    const messageElement = panel.querySelector('.chart-message');
    if (messageElement) {{
      messageElement.textContent = message;
    }}
  }}

  function toNumberArray(values) {{
    if (!Array.isArray(values)) return [];
    return values.map((value) => (typeof value === 'number' && Number.isFinite(value) ? value : null));
  }}

  function hasPositiveValue(values) {{
    return values.some((value) => typeof value === 'number' && value > 0);
  }}

  if (!ctx || !ctx2 || !ctx3) {{
    console.error(`${{reportPrefix}} Missing canvas elements for chart rendering.`);
    setPanelMessage(barPanel, 'Chart container is unavailable.', true);
    setPanelMessage(linePanel, 'Chart container is unavailable.', true);
    setPanelMessage(devicePanel, 'Chart container is unavailable.', true);
    return;
  }}

  if (typeof Chart === 'undefined') {{
    console.error(`${{reportPrefix}} Chart.js failed to load from static/chart.min.js.`);
    setPanelMessage(barPanel, 'Unable to load chart library.', true);
    setPanelMessage(linePanel, 'Unable to load chart library.', true);
    setPanelMessage(devicePanel, 'Unable to load chart library.', true);
    return;
  }}

  const startupMetrics = data && typeof data === 'object' ? data.startup_metrics || {{}} : {{}};
  const coldAvg = Number(startupMetrics?.cold?.avg);
  const warmAvg = Number(startupMetrics?.warm?.avg);
  const hotAvg = Number(startupMetrics?.hot?.avg);
  const barValues = [coldAvg, warmAvg, hotAvg].map((value) => (Number.isFinite(value) ? value : null));
  const coldTrend = toNumberArray(startupMetrics?.cold?.values);
  const warmTrend = toNumberArray(startupMetrics?.warm?.values);
  const hotTrend = toNumberArray(startupMetrics?.hot?.values);

  const labels = Array.isArray(data.device_metrics?.labels) ? data.device_metrics.labels : [];
  const values = toNumberArray(data.device_metrics?.startup_avg_ms).filter((value) => typeof value === 'number');

  try {{
    if (hasPositiveValue(barValues)) {{
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: ['Cold','Warm','Hot'],
          datasets: [{{
            label: 'Avg Launch Time',
            data: barValues,
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
    }} else {{
      console.warn(`${{reportPrefix}} No startup average data for bar chart.`);
      setPanelMessage(barPanel, 'No data available.', false);
    }}
  }} catch (error) {{
    console.error(`${{reportPrefix}} Failed to render average launch chart.`, error);
    setPanelMessage(barPanel, 'Unable to render chart.', true);
  }}

  try {{
    if (hasPositiveValue(coldTrend) || hasPositiveValue(warmTrend) || hasPositiveValue(hotTrend)) {{
      new Chart(ctx2, {{
        type: 'line',
        data: {{
          labels: Array.isArray(startupMetrics?.labels) ? startupMetrics.labels : [],
          datasets: [
            {{ label: 'Cold', data: coldTrend, borderColor: '#ef4444', fill: false }},
            {{ label: 'Warm', data: warmTrend, borderColor: '#f59e0b', fill: false }},
            {{ label: 'Hot', data: hotTrend, borderColor: '#22c55e', fill: false }}
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
    }} else {{
      console.warn(`${{reportPrefix}} No startup trend samples for line chart.`);
      setPanelMessage(linePanel, 'No data available.', false);
    }}
  }} catch (error) {{
    console.error(`${{reportPrefix}} Failed to render launch trend chart.`, error);
    setPanelMessage(linePanel, 'Unable to render chart.', true);
  }}

  try {{
    if (labels.length > 0 && labels.length === values.length && hasPositiveValue(values)) {{
      new Chart(ctx3, {{
        type: 'bar',
        data: {{
          labels,
          datasets: [{{
            label: 'Avg Startup (ms)',
            data: values,
            backgroundColor: '#3b82f6'
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: 'y',
          layout: {{ padding: 12 }},
          plugins: {{
            legend: {{ display: true, position: 'top' }}
          }}
        }}
      }});
    }} else {{
      console.warn(`${{reportPrefix}} No device-level startup data for device chart.`);
      setPanelMessage(devicePanel, 'No data available.', false);
    }}
  }} catch (error) {{
    console.error(`${{reportPrefix}} Failed to render device startup chart.`, error);
    setPanelMessage(devicePanel, 'Unable to render chart.', true);
  }}
}});
</script>

</body>
</html>
"""


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    _ensure_local_chart_js()

    devices = _normalize_results(results)
    runtime_data, startup_data, device_data = _collect_rows(devices)

    summary = _build_summary(startup_data)
    insights = _build_insights(runtime_data, startup_data)
    insights_html = "\n".join(f"<li>{escape(item)}</li>" for item in insights)
    runtime_rows = _runtime_rows_html(runtime_data)
    startup_rows = _startup_rows_html(startup_data)
    device_rows = _device_rows_html(device_data)
    chart_data_json = json.dumps(_chart_payload(startup_data))

    html = _html_report(summary, insights_html, runtime_rows, startup_rows, device_rows, chart_data_json)
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
