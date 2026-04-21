#!/usr/bin/env python3
"""Generate HTML reports with locally hosted Chart.js charts."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict

import logging_config

INPUT_FILE = Path("final_results.json")
OUTPUT_FILE = Path("report.html")
STATIC_DIR = Path("static")
CHART_JS_FILE = STATIC_DIR / "chart.min.js"
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
LOGGER = logging_config.get_logger("report_generator")

MINI_CHART_JS = """(function(){
function pick(v,d){return (typeof v==='number'&&isFinite(v))?v:d;}
function maxIn(ds){var m=0;ds.forEach(function(d){(d.data||[]).forEach(function(v){if(typeof v==='number'&&v>m)m=v;});});return m||1;}
function drawAxes(ctx,w,h,p){ctx.strokeStyle='#333';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(p,h-p);ctx.lineTo(w-p,h-p);ctx.lineTo(w-p,p);ctx.stroke();}
function drawBar(ctx,cfg){var ds=(cfg.data&&cfg.data.datasets)||[];var labels=(cfg.data&&cfg.data.labels)||[];var set=ds[0]||{data:[]};var vals=set.data||[];var colors=set.backgroundColor||[];var w=ctx.canvas.width,h=ctx.canvas.height,p=30;ctx.clearRect(0,0,w,h);drawAxes(ctx,w,h,p);var m=Math.max(maxIn(ds),1);var n=Math.max(vals.length,1);var space=(w-2*p)/n;var bw=space*0.6;ctx.font='12px Arial';ctx.textAlign='center';for(var i=0;i<n;i++){var v=pick(vals[i],0);var bh=((h-2*p)*(v/m));var x=p+i*space+(space-bw)/2;var y=h-p-bh;ctx.fillStyle=colors[i]||'#3b82f6';ctx.fillRect(x,y,bw,bh);ctx.fillStyle='#111';ctx.fillText(labels[i]||String(i+1),x+bw/2,h-p+14);} }
function drawLine(ctx,cfg){var ds=(cfg.data&&cfg.data.datasets)||[];var labels=(cfg.data&&cfg.data.labels)||[];var w=ctx.canvas.width,h=ctx.canvas.height,p=30;ctx.clearRect(0,0,w,h);drawAxes(ctx,w,h,p);var m=Math.max(maxIn(ds),1);var n=Math.max(labels.length,1);var step=(w-2*p)/Math.max(n-1,1);ctx.font='12px Arial';ctx.textAlign='center';for(var l=0;l<labels.length;l++){ctx.fillStyle='#111';ctx.fillText(labels[l],p+l*step,h-p+14);}ds.forEach(function(d){var data=d.data||[];ctx.strokeStyle=d.borderColor||'#ef4444';ctx.lineWidth=2;ctx.beginPath();for(var i=0;i<n;i++){var v=pick(data[i],0);var x=p+i*step;var y=h-p-((h-2*p)*(v/m));if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();});}
function Chart(target,config){if(!(this instanceof Chart))return new Chart(target,config);var canvas=target&&target.getContext?target:(typeof target==='string'?document.getElementById(target):null);if(!canvas)throw new Error('Canvas not found');var ctx=canvas.getContext('2d');if(!ctx)throw new Error('2D context unavailable');if(config&&config.type==='line')drawLine(ctx,config);else drawBar(ctx,config);this.canvas=canvas;this.config=config;}
window.Chart=Chart;
})();"""


def _startup_block(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("startup_metrics", data)


def _ensure_local_chart_js() -> None:
    """Ensure Chart.js is available locally as ./static/chart.min.js."""
    if CHART_JS_FILE.exists() and CHART_JS_FILE.stat().st_size > 0:
        return

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    chart_data = b""
    try:
        LOGGER.info("Downloading Chart.js to %s", CHART_JS_FILE)
        with urllib.request.urlopen(CHART_JS_URL, timeout=30) as response:
            chart_data = response.read()
    except Exception as exc:  # network-restricted env fallback
        LOGGER.warning("Chart.js download failed (%s). Using local fallback renderer.", exc)

    if chart_data:
        CHART_JS_FILE.write_bytes(chart_data)
        return

    CHART_JS_FILE.write_text(MINI_CHART_JS, encoding="utf-8")


def _device_sections(results: Dict[str, Any]) -> str:
    sections = []
    for idx, (device_id, _) in enumerate(results.items()):
        bar_id = "barChart" if idx == 0 else f"barChart_{idx}"
        line_id = "lineChart" if idx == 0 else f"lineChart_{idx}"
        debug_id = "debug" if idx == 0 else f"debug_{idx}"
        sections.append(
            f"""
  <section class=\"device\"> 
    <h2>Device: {device_id}</h2>
    <canvas id=\"{bar_id}\" width=\"400\" height=\"200\"></canvas>
    <canvas id=\"{line_id}\" width=\"400\" height=\"200\"></canvas>
    <pre id=\"{debug_id}\"></pre>
  </section>
""".rstrip()
        )

    if not sections:
        return """
  <section class=\"device\">
    <h2>No Data Available</h2>
    <pre id=\"debug\">No Data Available</pre>
  </section>
""".rstrip()

    return "\n".join(sections)


def _chart_script(results: Dict[str, Any]) -> str:
    data_json = json.dumps(results)
    return f"""
document.addEventListener("DOMContentLoaded", function () {{
  console.log("Chart script loaded");

  const allResults = {data_json};

  if (!allResults || Object.keys(allResults).length === 0) {{
    const body = document.body;
    const empty = document.createElement('h2');
    empty.innerText = 'No Data Available';
    body.appendChild(empty);
    return;
  }}

  Object.entries(allResults).forEach(([deviceId, data], idx) => {{
    console.log("Data:", data);

    const barId = idx === 0 ? 'barChart' : `barChart_${{idx}}`;
    const lineId = idx === 0 ? 'lineChart' : `lineChart_${{idx}}`;
    const debugId = idx === 0 ? 'debug' : `debug_${{idx}}`;

    const debugEl = document.getElementById(debugId);
    if (debugEl) {{
      debugEl.innerText = JSON.stringify(data, null, 2);
    }}

    const startup = data.startup_metrics || data;
    const coldAvg = startup?.cold?.avg ?? 0;
    const warmAvg = startup?.warm?.avg ?? 0;
    const hotAvg = startup?.hot?.avg ?? 0;

    const ctx = document.getElementById(barId);
    if (!ctx) {{
      console.error("Canvas not found");
      return;
    }}

    if (typeof Chart === 'undefined') {{
      console.error('Chart.js failed to load from local static/chart.min.js');
      return;
    }}

    new Chart(ctx, {{
      type: 'bar',
      data: {{
        labels: ['Cold', 'Warm', 'Hot'],
        datasets: [{{
          label: 'Launch Time (ms)',
          data: [coldAvg, warmAvg, hotAvg],
          backgroundColor: ['#ef4444', '#f59e0b', '#22c55e']
        }}]
      }},
      options: {{ responsive: false }}
    }});

    const coldValues = Array.isArray(startup?.cold?.values) ? startup.cold.values : [];
    const warmValues = Array.isArray(startup?.warm?.values) ? startup.warm.values : [];
    const hotValues = Array.isArray(startup?.hot?.values) ? startup.hot.values : [];
    const maxLen = Math.max(coldValues.length, warmValues.length, hotValues.length, 1);
    const labels = Array.from({{ length: maxLen }}, (_, i) => i + 1);

    const lineCtx = document.getElementById(lineId);
    if (!lineCtx) {{
      console.error("Canvas not found");
      return;
    }}

    new Chart(lineCtx, {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: 'Cold', data: coldValues, borderColor: '#ef4444', fill: false }},
          {{ label: 'Warm', data: warmValues, borderColor: '#f59e0b', fill: false }},
          {{ label: 'Hot', data: hotValues, borderColor: '#22c55e', fill: false }}
        ]
      }},
      options: {{ responsive: false }}
    }});
  }});
}});
""".strip()


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    _ensure_local_chart_js()
    sections = _device_sections(results)
    chart_script = _chart_script(results)

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Android Performance Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    canvas {{ border: 1px solid #ddd; margin-bottom: 16px; display: block; }}
    pre {{ background: #f7f7f7; border: 1px solid #ddd; padding: 10px; overflow: auto; }}
  </style>
</head>
<body>
{sections}
<script src=\"static/chart.min.js\"></script>
<script>
{chart_script}
</script>
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
