#!/usr/bin/env python3
"""Generate a unified single-page HTML performance report with Chart.js charts."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
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


def _ensure_local_chart_js(target_chart_file: Path = CHART_JS_FILE) -> None:
    """Ensure Chart.js exists at the target path (download, then fallback)."""
    if target_chart_file.exists() and target_chart_file.stat().st_size > 0:
        return

    target_chart_file.parent.mkdir(parents=True, exist_ok=True)
    chart_data = b""

    try:
        LOGGER.info("Downloading Chart.js to %s", target_chart_file)
        with urllib.request.urlopen(CHART_JS_URL, timeout=30) as response:
            chart_data = response.read()
    except Exception as exc:  # pragma: no cover - depends on network access
        LOGGER.warning("Chart.js download failed (%s). Using fallback renderer.", exc)

    if chart_data:
        target_chart_file.write_bytes(chart_data)
    else:
        target_chart_file.write_text(MINI_CHART_JS, encoding="utf-8")


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _as_nullable_list(values: Any) -> List[float | None]:
    if not isinstance(values, list):
        return []
    out: List[float | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        try:
            number = float(value)
            out.append(number if number > 0 else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _metric_value(source: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in source:
            try:
                return float(source[key])
            except (TypeError, ValueError):
                return None
    return None


def _device_label(device_id: str, device_details: Dict[str, Any]) -> str:
    manufacturer = str(device_details.get("manufacturer", "")).strip()
    model = str(device_details.get("model", "")).strip()

    def _valid(part: str) -> bool:
        return bool(part) and part.upper() != "N/A"

    manufacturer_valid = _valid(manufacturer)
    model_valid = _valid(model)
    if manufacturer_valid and model_valid:
        return f"{manufacturer} {model}"
    if model_valid:
        return model
    if manufacturer_valid:
        return manufacturer
    return device_id


def _startup_bucket(startup: Dict[str, Any], mode: str) -> Dict[str, Any]:
    aliases = {
        "cold": ("cold", "cold_start", "coldStart"),
        "warm": ("warm", "warm_start", "warmStart"),
        "hot": ("hot", "hot_start", "hotStart"),
    }
    for key in aliases.get(mode, (mode,)):
        value = startup.get(key)
        if isinstance(value, dict):
            return value
    return {}


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
        cold = _startup_bucket(startup, "cold")
        warm = _startup_bucket(startup, "warm")
        hot = _startup_bucket(startup, "hot")
        details = device_data.get("device_details", {}) if isinstance(device_data, dict) else {}
        error = str(device_data.get("error", "")).strip() if isinstance(device_data, dict) else ""
        label = _device_label(device, details)

        runtime_rows.append(
            {
                "device": device,
                "device_label": label,
                "cpu": _metric_value(runtime, "cpu", "cpu_percent", "cpu_usage", "avg_cpu"),
                "memory": _metric_value(runtime, "memory", "memory_mb", "avg_memory", "memory_usage"),
                "fps": _metric_value(runtime, "fps", "avg_fps", "frame_rate"),
                "gc_count": _metric_value(runtime, "gc_count"),
                "cpu_status": str(runtime.get("status", {}).get("cpu", "missing")) if isinstance(runtime.get("status"), dict) else "missing",
                "memory_status": str(runtime.get("status", {}).get("memory", "missing")) if isinstance(runtime.get("status"), dict) else "missing",
                "fps_status": str(runtime.get("status", {}).get("fps", "missing")) if isinstance(runtime.get("status"), dict) else "missing",
                "error": error,
            }
        )

        startup_rows.append(
            {
                "device": device,
                "device_label": label,
                "cold_avg": _metric_value(cold, "avg", "average"),
                "warm_avg": _metric_value(warm, "avg", "average"),
                "hot_avg": _metric_value(hot, "avg", "average"),
                "cold_values": _as_nullable_list(cold.get("values") if "values" in cold else cold.get("samples")),
                "warm_values": _as_nullable_list(warm.get("values") if "values" in warm else warm.get("samples")),
                "hot_values": _as_nullable_list(hot.get("values") if "values" in hot else hot.get("samples")),
            }
        )

        device_rows.append(
            {
                "device": device,
                "device_label": label,
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
        cold = row.get("cold_avg")
        if isinstance(cold, float):
            averages.append((row["device_label"], cold))
            per_device_scores.append(cold)

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


def _build_kpis(
    summary: Dict[str, str],
    runtime_rows: List[Dict[str, Any]],
    device_rows: List[Dict[str, Any]],
) -> Dict[str, str]:
    avg_cpu = _avg([row["cpu"] for row in runtime_rows if isinstance(row.get("cpu"), float)])
    avg_memory = _avg([row["memory"] for row in runtime_rows if isinstance(row.get("memory"), float)])
    avg_fps = _avg([row["fps"] for row in runtime_rows if isinstance(row.get("fps"), float)])
    test_count = len(device_rows)

    return {
        "fastest": summary.get("fastest", "N/A"),
        "slowest": summary.get("slowest", "N/A"),
        "overall": summary.get("overall", "N/A"),
        "avg_cpu": f"{avg_cpu:.1f}%" if avg_cpu is not None else "No Data",
        "avg_memory": f"{avg_memory:.1f} MB" if avg_memory is not None else "No Data",
        "avg_fps": f"{avg_fps:.1f}" if avg_fps is not None else "No Data",
        "total_devices": str(test_count),
    }


def _estimate_test_duration(startup_rows: List[Dict[str, Any]]) -> str:
    startup_samples = 0
    for row in startup_rows:
        startup_samples += len(row.get("cold_values", []))
        startup_samples += len(row.get("warm_values", []))
        startup_samples += len(row.get("hot_values", []))
    if startup_samples <= 0:
        return "Unknown (insufficient timing data)"
    seconds = startup_samples * 2
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"~{minutes}m {remaining_seconds}s (estimated)"



def _build_insights(runtime_rows: List[Dict[str, Any]], startup_rows: List[Dict[str, Any]]) -> List[str]:
    insights: List[str] = []

    failed = [row for row in runtime_rows if row.get("error")]
    if failed:
        insights.append(f"{len(failed)} device(s) reported runtime/startup errors.")

    critical_fps = [row for row in runtime_rows if isinstance(row.get("fps"), float) and row["fps"] < 20.0]
    if critical_fps:
        names = ", ".join(row["device_label"] for row in critical_fps[:4])
        insights.append(f"Critical FPS (<20) detected on: {names}.")

    low_fps = [
        row for row in runtime_rows if isinstance(row.get("fps"), float) and 20.0 <= row["fps"] < 30.0
    ]
    if low_fps:
        names = ", ".join(row["device_label"] for row in low_fps[:4])
        insights.append(f"Low FPS detected (<30) on: {names}.")

    high_gc = [row for row in runtime_rows if isinstance(row.get("gc_count"), float) and row["gc_count"] >= 30]
    if high_gc:
        names = ", ".join(row["device_label"] for row in high_gc[:4])
        insights.append(f"High GC activity detected on: {names}.")

    for row in startup_rows:
        cold, warm = row.get("cold_avg"), row.get("warm_avg")
        hot = row.get("hot_avg")
        if isinstance(cold, float) and cold > 3000.0:
            insights.append(f"{row['device_label']} cold start is critical (>3000 ms).")
        elif isinstance(cold, float) and cold > 1500.0:
            insights.append(f"{row['device_label']} cold start is elevated (>1500 ms).")
        if isinstance(cold, float) and isinstance(warm, float) and warm > 0 and (cold / warm) > 5.0:
            insights.append(f"{row['device_label']} cold/warm ratio is >5x; investigate initialization path.")
        if isinstance(hot, float) and isinstance(warm, float) and hot > warm:
            insights.append(f"{row['device_label']} hot start is slower than warm start (anomaly).")

    high_cpu = [row for row in runtime_rows if isinstance(row.get("cpu"), float) and row["cpu"] > 60.0]
    if high_cpu:
        names = ", ".join(row["device_label"] for row in high_cpu[:4])
        insights.append(f"High CPU usage (>60%) detected on: {names}.")

    all_runtime_na = [
        row for row in runtime_rows if not isinstance(row.get("cpu"), float) and not isinstance(row.get("memory"), float) and not isinstance(row.get("fps"), float)
    ]
    if all_runtime_na:
        names = ", ".join(row["device_label"] for row in all_runtime_na[:4])
        insights.append(
            f"Runtime CPU/Memory/FPS are all N/A on: {names}. App was likely not in foreground during sampling; keep it active and rerun."
        )

    return insights if insights else ["No major anomalies detected from available metrics."]

def _runtime_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"6\">No runtime data available</td></tr>"

    def _cpu_class(cpu: float | None) -> str:
        if not isinstance(cpu, float):
            return ""
        if cpu > 60:
            return "metric-critical"
        if cpu >= 40:
            return "metric-warning"
        return "metric-good"

    def _fps_class(fps: float | None) -> str:
        if not isinstance(fps, float):
            return ""
        if fps < 20:
            return "metric-critical"
        if fps < 30:
            return "metric-warning"
        return "metric-good"

    rows_html: List[str] = []
    def _display_metric(value: float | None, status: str) -> str:
        if isinstance(value, float):
            return _fmt_num(value)
        if status == "loading":
            return "Loading..."
        if status == "retrying":
            return "Retrying..."
        return "N/A"

    for row in rows:
        cpu_value = _display_metric(row["cpu"], row.get("cpu_status", "missing"))
        memory_value = _display_metric(row["memory"], row.get("memory_status", "missing"))
        fps_value = _display_metric(row["fps"], row.get("fps_status", "missing"))
        cpu_cell = cpu_value if cpu_value != "N/A" else '<span class="badge badge-neutral">No Data</span>'
        memory_cell = memory_value if memory_value != "N/A" else '<span class="badge badge-neutral">No Data</span>'
        fps_cell = fps_value if fps_value != "N/A" else '<span class="badge badge-neutral">No Data</span>'
        gc_cell = _fmt_num(row["gc_count"], 0) if isinstance(row["gc_count"], float) else '<span class="badge badge-neutral">No Data</span>'
        status_cell = f'<span class="badge badge-critical">{escape(row["error"])}</span>' if row["error"] else '<span class="badge badge-good">Healthy</span>'
        rows_html.append(
            "<tr>"
            f"<td title=\"Test device (manufacturer + model)\">{escape(row['device_label'])}</td>"
            f"<td class=\"{_cpu_class(row['cpu'])}\" title=\"CPU usage % of the app process during the runtime sampling window. >40% = warning, >60% = critical.\">{cpu_cell}</td>"
            f"<td title=\"Average PSS memory used by the app in MB. High values risk LMK kills on low-RAM devices.\">{memory_cell}</td>"
            f"<td class=\"{_fps_class(row['fps'])}\" title=\"Rendered frames per second during the gfxinfo sampling window. <30 fps = warning (amber), <20 fps = critical (red).\">{fps_cell}</td>"
            f"<td title=\"Number of garbage collection events observed in logcat during the benchmark. Frequent GC can cause frame drops.\">{gc_cell}</td>"
            f"<td title=\"Overall health: Healthy = all metrics within limits. Warning = at least one metric in amber range. Degraded = at least one metric in red range. No Data = runtime collection failed.\">{status_cell}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def _startup_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan=\"7\">No startup data available</td></tr>"

    def _perf_class_startup(startup_ms: float | None) -> str:
        if not isinstance(startup_ms, float):
            return ""
        if startup_ms <= 2000:
            return "metric-good"
        if startup_ms <= 3500:
            return "metric-warning"
        return "metric-critical"

    rows_html: List[str] = []
    for row in rows:
        cold_values = row.get("cold_values", [])
        warm_values = row.get("warm_values", [])
        hot_values = row.get("hot_values", [])

        def _spread_title(values: List[float | None]) -> str:
            valid_values = [v for v in values if isinstance(v, float)]
            if valid_values:
                return f"min={int(min(valid_values))} ms, max={int(max(valid_values))} ms, samples={len(valid_values)}/{len(values)}"
            return f"min=N/A ms, max=N/A ms, samples=0/{len(values)}"

        def _samples_cell(values: List[float | None]) -> str:
            total = len(values)
            valid = sum(1 for value in values if isinstance(value, float))
            if total <= 0:
                badge_class = "badge-neutral"
            elif valid == total:
                badge_class = "badge-good"
            elif valid >= total * 0.7:
                badge_class = "badge-warn"
            else:
                badge_class = "badge-critical"
            return f'<span class="badge {badge_class}">{valid}/{total}</span>'

        rows_html.append(
            "<tr>"
            f"<td title=\"Test device (manufacturer + model)\">{escape(row['device_label'])}</td>"
            f"<td class=\"{_perf_class_startup(row['cold_avg'])}\" title=\"{escape(_spread_title(cold_values))}\">{_fmt_num(row['cold_avg'])}</td>"
            f"<td title=\"Valid cold samples over total configured iterations.\">{_samples_cell(cold_values)}</td>"
            f"<td class=\"{_perf_class_startup(row['warm_avg'])}\" title=\"{escape(_spread_title(warm_values))}\">{_fmt_num(row['warm_avg'])}</td>"
            f"<td title=\"Valid warm samples over total configured iterations.\">{_samples_cell(warm_values)}</td>"
            f"<td class=\"{_perf_class_startup(row['hot_avg'])}\" title=\"{escape(_spread_title(hot_values))}\">{_fmt_num(row['hot_avg'])}</td>"
            f"<td title=\"Valid hot samples over total configured iterations.\">{_samples_cell(hot_values)}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def _device_rows_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty-state">No device details available</div>'

    lines: List[str] = []
    for row in rows:
        os_label = row["android_version"]
        if row["sdk_int"] != "N/A":
            os_label = f"{os_label} (SDK {row['sdk_int']})"
        status_class = "status-down" if row["error"] else "status-up"
        status_text = "Issue detected" if row["error"] else "Healthy"
        memory_text = f"{_fmt_num(row['total_memory_mb'])} MB RAM" if isinstance(row["total_memory_mb"], float) else "No Data MB RAM"
        error_html = f'<p class="device-error">{escape(row["error"])}</p>' if row["error"] else ""
        lines.append(
            '<article class="device-card">'
            '<div class="device-card-header">'
            f"<div><h3>{escape(row['device_label'])}</h3><p>{escape(row['manufacturer'])} · {escape(row['model'])}</p></div>"
            f"<span class=\"status-pill {status_class}\"><span class=\"status-dot\"></span>{status_text}</span>"
            "</div>"
            '<div class="device-meta">'
            f"<span class=\"badge badge-neutral\">OS {escape(os_label)}</span>"
            f"<span class=\"badge badge-neutral\">{escape(row['cpu'])}</span>"
            f"<span class=\"badge badge-neutral\">{memory_text}</span>"
            "</div>"
            f"<p class=\"mono\">{escape(row['build_fingerprint'])}</p>"
            f"{error_html}"
            "</article>"
        )
    return "\n".join(lines)


def _chart_payload(
    startup_rows: List[Dict[str, Any]],
    runtime_rows: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    runtime_rows = runtime_rows or []
    cold_avg = _avg([row["cold_avg"] for row in startup_rows if isinstance(row["cold_avg"], float)])
    warm_avg = _avg([row["warm_avg"] for row in startup_rows if isinstance(row["warm_avg"], float)])
    hot_avg = _avg([row["hot_avg"] for row in startup_rows if isinstance(row["hot_avg"], float)])

    def _pad_to(values: List[float | None], target: int) -> List[float | None]:
        out = [round(value, 2) if isinstance(value, float) else None for value in values[:target]]
        while len(out) < target:
            out.append(None)
        return out

    cold_series = [row.get("cold_values", []) for row in startup_rows]
    warm_series = [row.get("warm_values", []) for row in startup_rows]
    hot_series = [row.get("hot_values", []) for row in startup_rows]
    all_series = cold_series + warm_series + hot_series
    max_points = max((len(series) for series in all_series), default=0)

    cold_series = [_pad_to(series, max_points) for series in cold_series]
    warm_series = [_pad_to(series, max_points) for series in warm_series]
    hot_series = [_pad_to(series, max_points) for series in hot_series]

    device_labels = [row["device_label"] for row in startup_rows]
    device_avg_values = [
        round(row["cold_avg"], 2) if isinstance(row.get("cold_avg"), float) else None
        for row in startup_rows
    ]

    runtime_labels = [row["device_label"] for row in runtime_rows]
    cpu_values = [round(row["cpu"], 2) if isinstance(row["cpu"], float) else None for row in runtime_rows]
    memory_values = [round(row["memory"], 2) if isinstance(row["memory"], float) else None for row in runtime_rows]

    return {
        "startup_metrics": {
            "cold": {"avg": round(cold_avg, 2) if cold_avg is not None else None},
            "warm": {"avg": round(warm_avg, 2) if warm_avg is not None else None},
            "hot": {"avg": round(hot_avg, 2) if hot_avg is not None else None},
            "labels": list(range(1, max_points + 1)),
            "devices": device_labels,
            "per_device_series": {"cold": cold_series, "warm": warm_series, "hot": hot_series},
        },
        "device_metrics": {"labels": device_labels, "startup_avg_ms": device_avg_values},
        "runtime_metrics": {"labels": runtime_labels, "cpu_percent": cpu_values, "memory_mb": memory_values},
    }


def _html_report(
    kpis: Dict[str, str],
    insights_html: str,
    runtime_rows: str,
    startup_rows: str,
    device_rows: str,
    chart_data_json: str,
    generated_at: str,
    test_duration: str,
    environment: str,
) -> str:
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Unified Android Performance Report</title>
  <style>
    :root {{
      --bg: #0b1220;
      --surface: #111a2e;
      --surface-soft: #162239;
      --text: #e5edf9;
      --muted: #93a2be;
      --line: #253454;
      --good: #22c55e;
      --warn: #f59e0b;
      --critical: #ef4444;
      --accent: #60a5fa;
    }}
    body {{
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: radial-gradient(circle at top, #16233f 0%, #0b1220 60%);
      margin: 0;
      padding: 24px;
      color: var(--text);
    }}
    .container {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 20px;
      margin-bottom: 18px;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.25);
      transition: transform .2s ease, box-shadow .2s ease;
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 16px 38px rgba(0, 0, 0, 0.32);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 30px;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 20px;
    }}
    .subtitle {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    .header-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }}
    .meta {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid transparent;
    }}
    .badge-neutral {{ background: rgba(147, 162, 190, .14); color: #d7e1f5; border-color: rgba(147, 162, 190, .24); }}
    .badge-good {{ background: rgba(34,197,94,.18); color: #aff7c8; border-color: rgba(34,197,94,.3); }}
    .badge-warn {{ background: rgba(245,158,11,.18); color: #ffe0a5; border-color: rgba(245,158,11,.3); }}
    .badge-critical {{ background: rgba(239,68,68,.18); color: #ffc0c0; border-color: rgba(239,68,68,.3); }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 14px;
    }}
    .kpi-card {{
      background: var(--surface-soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .kpi-card .label {{ font-size: 12px; color: var(--muted); margin-bottom: 8px; display: block; }}
    .kpi-card .value {{ font-size: 24px; font-weight: 800; letter-spacing: .2px; }}
    .kpi-card small {{ color: var(--muted); font-size: 12px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 12px;
      border: 1px solid var(--line);
    }}
    th {{
      background: rgba(96,165,250,.15);
      color: #cfe3ff;
      padding: 11px;
      font-size: 13px;
      text-align: left;
    }}
    td {{
      padding: 11px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    .metric-good {{ color: var(--good); font-weight: 700; }}
    .metric-warning {{ color: var(--warn); font-weight: 700; }}
    .metric-critical {{ color: var(--critical); font-weight: 700; }}
    .chart-wrap {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }}
    .chart-panel {{
      background: var(--surface-soft);
      border: 1px solid var(--line);
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
      color: var(--muted);
      font-size: 14px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 12px;
      background: rgba(11,18,32,.4);
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
      color: #ffb3b3;
      border-color: rgba(239,68,68,.5);
      background: rgba(239,68,68,.12);
      font-weight: 600;
    }}
    .insight-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
    }}
    .insight-card {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: var(--surface-soft);
    }}
    .insight-card.warning {{ border-left: 4px solid var(--warn); }}
    .insight-card.critical {{ border-left: 4px solid var(--critical); }}
    .insight-card.healthy {{ border-left: 4px solid var(--good); }}
    .device-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
    }}
    .device-card {{
      background: var(--surface-soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      margin: 0;
    }}
    .device-card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    .device-card h3 {{ margin: 0; font-size: 15px; }}
    .device-card p {{ margin: 2px 0 0; color: var(--muted); font-size: 13px; }}
    .device-meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 10px; }}
    .status-pill {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; }}
    .status-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
    .status-up .status-dot {{ background: var(--good); box-shadow: 0 0 0 2px rgba(34,197,94,.2); }}
    .status-down .status-dot {{ background: var(--critical); box-shadow: 0 0 0 2px rgba(239,68,68,.2); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 11px; word-break: break-all; color: #9db1d1; }}
    .device-error {{ color: #ffb3b3 !important; margin-top: 8px !important; }}
    canvas {{ width: 100%; height: 340px; }}
    .table-wrap {{ overflow-x: auto; }}
    @media (max-width: 768px) {{
      body {{ padding: 12px; }}
      .kpi-card .value {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
<div class=\"container\">
  <div class=\"header-bar\">
    <div>
      <h1>Android Performance Analytics</h1>
      <p class=\"subtitle\">Production-style runtime and startup benchmarking dashboard.</p>
    </div>
    <div class=\"meta\">
      <span class=\"badge badge-neutral\">Generated: {escape(generated_at)}</span>
      <span class=\"badge badge-neutral\">Duration: {escape(test_duration)}</span>
      <span class=\"badge badge-good\">Env: {escape(environment)}</span>
    </div>
  </div>

<div class=\"card\">
  <h2>KPI Summary</h2>
  <div class=\"kpi-grid\">
    <div class=\"kpi-card\"><span class=\"label\">🚀 Fastest Device</span><span class=\"value\">{kpis['fastest']}</span><small>Lowest average startup time</small></div>
    <div class=\"kpi-card\"><span class=\"label\">🐢 Slowest Device</span><span class=\"value\">{kpis['slowest']}</span><small>Highest average startup time</small></div>
    <div class=\"kpi-card\"><span class=\"label\">📊 Avg Startup</span><span class=\"value\">{kpis['overall']}</span><small>Overall launch latency</small></div>
    <div class=\"kpi-card\"><span class=\"label\">Avg CPU</span><span class=\"value\">{kpis['avg_cpu']}</span><small>Mean runtime CPU utilization</small></div>
    <div class=\"kpi-card\"><span class=\"label\">Avg Memory</span><span class=\"value\">{kpis['avg_memory']}</span><small>Mean memory footprint</small></div>
    <div class=\"kpi-card\"><span class=\"label\">Avg FPS</span><span class=\"value\">{kpis['avg_fps']}</span><small>Rendering smoothness index</small></div>
    <div class=\"kpi-card\"><span class=\"label\">Total Devices Tested</span><span class=\"value\">{kpis['total_devices']}</span><small>Devices included in report</small></div>
  </div>
</div>

<div class=\"card\">
  <h2>Insights</h2>
  <div class=\"insight-grid\">
    {insights_html}
  </div>
</div>

<div class=\"card\">
  <h2>Device Details</h2>
  <p class=\"subtitle\">Hardware and OS context for each tested target.</p>
  <div class=\"device-grid\">{device_rows}</div>
</div>

<div class=\"card\">
  <h2>Runtime Performance</h2>
  <p class=\"subtitle\">Tooltips explain each metric. FPS below 30 is marked critical.</p>
  <div class=\"table-wrap\"><table>
    <tr>
      <th title=\"Test device (manufacturer + model)\">Device</th>
      <th title=\"CPU usage % of the app process during the runtime sampling window. >40% = warning, >60% = critical.\">CPU (%)</th>
      <th title=\"Average PSS memory used by the app in MB. High values risk LMK kills on low-RAM devices.\">Memory (MB)</th>
      <th title=\"Rendered frames per second during the gfxinfo sampling window. <30 fps = warning (amber), <20 fps = critical (red).\">FPS</th>
      <th title=\"Number of garbage collection events observed in logcat during the benchmark. Frequent GC can cause frame drops.\">GC Count</th>
      <th title=\"Overall health: Healthy = all metrics within limits. Warning = at least one metric in amber range. Degraded = at least one metric in red range. No Data = runtime collection failed.\">Status</th>
    </tr>
    {runtime_rows}
  </table></div>
</div>

<div class=\"card\">
  <h2>Startup Performance</h2>
  <div class=\"table-wrap\"><table>
    <tr>
      <th title=\"Test device (manufacturer + model)\">Device</th>
      <th title=\"Average launch time after force-stopping the app. Measures full process creation + Activity onCreate. >1500 ms = warning, >3000 ms = critical.\">Cold Avg (ms)</th>
      <th title=\"Valid cold samples / total configured iterations.\">Samples</th>
      <th title=\"Average launch time when the process is already alive but the Activity was backgrounded via HOME. Measures Activity re-creation only.\">Warm Avg (ms)</th>
      <th title=\"Valid warm samples / total configured iterations.\">Samples</th>
      <th title=\"Average launch time when the Activity is brought back from brief background. Should be the fastest of the three. N/A means all samples were 0 ms (activity was already resumed).\">Hot Avg (ms)</th>
      <th title=\"Valid hot samples / total configured iterations.\">Samples</th>
    </tr>
    {startup_rows}
  </table></div>
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
      <h3>Warm Start Trend by Device</h3>
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
    <div class=\"chart-panel\" id=\"resourcePanel\">
      <h3>CPU vs Memory by Device</h3>
      <div class=\"chart-container\">
        <canvas id=\"resourceChart\"></canvas>
        <div class=\"chart-message\" id=\"resourceChartMessage\">No data available.</div>
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
  const ctx4 = document.getElementById(\"resourceChart\");
  const barPanel = document.getElementById(\"barPanel\");
  const linePanel = document.getElementById(\"linePanel\");
  const devicePanel = document.getElementById(\"devicePanel\");
  const resourcePanel = document.getElementById(\"resourcePanel\");

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

  if (!ctx || !ctx2 || !ctx3 || !ctx4) {{
    console.error(`${{reportPrefix}} Missing canvas elements for chart rendering.`);
    setPanelMessage(barPanel, 'Chart container is unavailable.', true);
    setPanelMessage(linePanel, 'Chart container is unavailable.', true);
    setPanelMessage(devicePanel, 'Chart container is unavailable.', true);
    setPanelMessage(resourcePanel, 'Chart container is unavailable.', true);
    return;
  }}

  if (typeof Chart === 'undefined') {{
    console.error(`${{reportPrefix}} Chart.js failed to load from static/chart.min.js.`);
    setPanelMessage(barPanel, 'Unable to load chart library.', true);
    setPanelMessage(linePanel, 'Unable to load chart library.', true);
    setPanelMessage(devicePanel, 'Unable to load chart library.', true);
    setPanelMessage(resourcePanel, 'Unable to load chart library.', true);
    return;
  }}

  const startupMetrics = data && typeof data === 'object' ? data.startup_metrics || {{}} : {{}};
  const coldAvg = Number(startupMetrics?.cold?.avg);
  const warmAvg = Number(startupMetrics?.warm?.avg);
  const hotAvg = Number(startupMetrics?.hot?.avg);
  const barValues = [coldAvg, warmAvg, hotAvg].map((value) => (Number.isFinite(value) ? value : null));
  const startupLabels = Array.isArray(startupMetrics?.labels) ? startupMetrics.labels : [];
  const startupDevices = Array.isArray(startupMetrics?.devices) ? startupMetrics.devices : [];
  const warmPerDeviceSeries = Array.isArray(startupMetrics?.per_device_series?.warm)
    ? startupMetrics.per_device_series.warm.map((series) => toNumberArray(series))
    : [];

  const labels = Array.isArray(data.device_metrics?.labels) ? data.device_metrics.labels : [];
  const values = toNumberArray(data.device_metrics?.startup_avg_ms);
  const runtimeLabels = Array.isArray(data.runtime_metrics?.labels) ? data.runtime_metrics.labels : [];
  const runtimeCpu = toNumberArray(data.runtime_metrics?.cpu_percent);
  const runtimeMemory = toNumberArray(data.runtime_metrics?.memory_mb);

  try {{
    if (hasPositiveValue(barValues)) {{
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: ['Cold','Warm','Hot'],
          datasets: [{{
            label: 'Avg Launch Time',
            data: barValues,
            backgroundColor: ['rgba(239,68,68,.65)', 'rgba(245,158,11,.65)', 'rgba(34,197,94,.65)'],
            borderRadius: 8
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          layout: {{ padding: 12 }},
          plugins: {{
            legend: {{ display: true, position: 'top' }}
          }},
          scales: {{
            y: {{ title: {{ display: true, text: 'Milliseconds (ms)' }} }}
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
    const palette = ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#14b8a6', '#f97316', '#e11d48'];
    const warmDatasets = warmPerDeviceSeries
      .map((series, index) => {{
        const color = palette[index % palette.length];
        const hasData = series.some((value) => typeof value === 'number' && Number.isFinite(value) && value > 0);
        if (!hasData) return null;
        const hasGap = series.some((value) => value === null);
        return {{
          label: startupDevices[index] || `Device ${{index + 1}}`,
          data: series,
          borderColor: color,
          backgroundColor: color,
          borderDash: hasGap ? [6, 4] : [],
          tension: 0.35,
          fill: false,
          spanGaps: true
        }};
      }})
      .filter(Boolean);

    if (warmDatasets.length > 0) {{
      new Chart(ctx2, {{
        type: 'line',
        data: {{
          labels: startupLabels,
          datasets: warmDatasets
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          layout: {{ padding: 12 }},
          plugins: {{
            legend: {{ display: true, position: 'top' }}
          }},
          scales: {{
            x: {{ title: {{ display: true, text: 'Run Iteration' }} }},
            y: {{ title: {{ display: true, text: 'Startup Time (ms)' }} }}
          }}
        }}
      }});
    }} else {{
      console.warn(`${{reportPrefix}} No warm startup trend samples for line chart.`);
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
            backgroundColor: 'rgba(96,165,250,.7)',
            borderRadius: 8
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: 'y',
          layout: {{ padding: 12 }},
          plugins: {{
            legend: {{ display: true, position: 'top' }}
          }},
          scales: {{
            x: {{ title: {{ display: true, text: 'Milliseconds (ms)' }} }}
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

  try {{
    if (runtimeLabels.length && hasPositiveValue(runtimeCpu) && hasPositiveValue(runtimeMemory)) {{
      new Chart(ctx4, {{
        type: 'bar',
        data: {{
          labels: runtimeLabels,
          datasets: [
            {{ label: 'CPU (%)', data: runtimeCpu, backgroundColor: 'rgba(245,158,11,.7)', yAxisID: 'y' }},
            {{ label: 'Memory (MB)', data: runtimeMemory, backgroundColor: 'rgba(34,197,94,.6)', yAxisID: 'y1' }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{ legend: {{ display: true, position: 'top' }} }},
          scales: {{
            y: {{ type: 'linear', position: 'left', title: {{ display: true, text: 'CPU (%)' }} }},
            y1: {{ type: 'linear', position: 'right', title: {{ display: true, text: 'Memory (MB)' }}, grid: {{ drawOnChartArea: false }} }}
          }}
        }}
      }});
    }} else {{
      setPanelMessage(resourcePanel, 'No runtime CPU/Memory data available.', false);
    }}
  }} catch (error) {{
    console.error(`${{reportPrefix}} Failed to render resource chart.`, error);
    setPanelMessage(resourcePanel, 'Unable to render chart.', true);
  }}
}});
</script>

</body>
</html>
"""


def generate_report_from_results(results: Dict[str, Any], output_file: Path = OUTPUT_FILE) -> Path:
    chart_js_file = output_file.parent / "static" / "chart.min.js"
    _ensure_local_chart_js(chart_js_file)

    devices = _normalize_results(results)
    runtime_data, startup_data, device_data = _collect_rows(devices)

    summary = _build_summary(startup_data)
    insights = _build_insights(runtime_data, startup_data)
    def _insight_card(item: str) -> str:
        normalized = item.lower()
        if "no major anomalies" in normalized or "healthy" in normalized:
            icon, style = "✅", "healthy"
        elif "error" in normalized or "low fps" in normalized:
            icon, style = "❌", "critical"
        else:
            icon, style = "⚠️", "warning"
        return f'<article class="insight-card {style}"><strong>{icon}</strong> {escape(item)}</article>'

    insights_html = "\n".join(_insight_card(item) for item in insights)
    runtime_rows = _runtime_rows_html(runtime_data)
    startup_rows = _startup_rows_html(startup_data)
    device_rows = _device_rows_html(device_data)
    chart_data_json = json.dumps(_chart_payload(startup_data, runtime_data))
    kpis = _build_kpis(summary, runtime_data, device_data)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    test_duration = _estimate_test_duration(startup_data)
    environment = os.environ.get("REPORT_ENV", "QA")

    html = _html_report(kpis, insights_html, runtime_rows, startup_rows, device_rows, chart_data_json, generated_at, test_duration, environment)
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
