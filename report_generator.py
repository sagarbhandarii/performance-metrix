#!/usr/bin/env python3
"""Generate an HTML performance report from performance_results.json."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple

import logging_config

INPUT_FILE = Path("performance_results.json")
OUTPUT_FILE = Path("report.html")
LOGGER = logging_config.get_logger("report_generator")


def _to_float(value: Any, default: float = 0.0) -> float:
    """Best-effort conversion to float.

    Supports numbers and strings such as "72", "72%", "512 MB", "1.5s".
    """
    if value is None:
        return default

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        cleaned = value.strip()
        for token in ["%", "mb", "mib", "gb", "s", "sec", "seconds", "ms"]:
            cleaned = cleaned.replace(token, "", 1) if cleaned.lower().endswith(token) else cleaned
        try:
            return float(cleaned)
        except ValueError:
            return default

    return default


def _pick(item: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def _normalize_records(data: Any) -> List[Dict[str, Any]]:
    """Normalize possible JSON shapes into a list of device records."""
    if isinstance(data, list):
        LOGGER.debug("Normalizing list-shaped data (%d records)", len(data))
        return [entry for entry in data if isinstance(entry, dict)]

    if isinstance(data, dict):
        if isinstance(data.get("devices"), list):
            LOGGER.debug("Normalizing `devices` section")
            return [entry for entry in data["devices"] if isinstance(entry, dict)]

        if isinstance(data.get("results"), list):
            LOGGER.debug("Normalizing `results` section")
            return [entry for entry in data["results"] if isinstance(entry, dict)]

        # Shape: {"device1": {...}, "device2": {...}}
        if all(isinstance(v, dict) for v in data.values()):
            normalized: List[Dict[str, Any]] = []
            for device_id, metrics in data.items():
                row = dict(metrics)
                row.setdefault("device_id", device_id)
                normalized.append(row)
            return normalized

    LOGGER.error("Unsupported report input shape")
    return []


def _cpu_class(cpu: float) -> str:
    if cpu < 60:
        return "good"
    if cpu < 85:
        return "warning"
    return "critical"


def _mem_class(mem: float) -> str:
    if mem < 60:
        return "good"
    if mem < 85:
        return "warning"
    return "critical"


def _launch_class(launch: float) -> str:
    if launch < 2.5:
        return "good"
    if launch < 4.0:
        return "warning"
    return "critical"


def _fps_class(fps: float) -> str:
    if fps >= 55:
        return "good"
    if fps >= 40:
        return "warning"
    return "critical"


def _as_percent_or_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    numeric = _to_float(value)
    return f"{numeric:.1f}%"


def _as_memory_or_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    numeric = _to_float(value)
    return f"{numeric:.1f}%"


def _as_launch_or_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    numeric = _to_float(value)
    return f"{numeric:.2f}s"


def _as_fps_or_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    numeric = _to_float(value)
    return f"{numeric:.1f}"


def _extract_metrics(record: Dict[str, Any]) -> Tuple[str, float, float, float, float, str, str, str, str]:
    device_id = str(_pick(record, "device_id", "id", "device", "serial", default="Unknown"))

    cpu_raw = _pick(record, "cpu_usage", "cpu", "cpu_percent", default=0)
    mem_raw = _pick(record, "memory_usage", "memory", "mem", "memory_percent", default=0)
    launch_raw = _pick(record, "launch_time", "startup_time", "app_launch_time", default=0)
    fps_raw = _pick(record, "fps", "frame_rate", default=0)

    cpu = _to_float(cpu_raw)
    mem = _to_float(mem_raw)
    launch = _to_float(launch_raw)
    fps = _to_float(fps_raw)

    return (
        device_id,
        cpu,
        mem,
        launch,
        fps,
        _as_percent_or_raw(cpu_raw),
        _as_memory_or_raw(mem_raw),
        _as_launch_or_raw(launch_raw),
        _as_fps_or_raw(fps_raw),
    )


def _mean_or_default(values: Iterable[float], default: float = 0.0) -> float:
    materialized = list(values)
    return mean(materialized) if materialized else default


def _build_html(rows: Iterable[Dict[str, Any]]) -> str:
    parsed_rows = [_extract_metrics(r) for r in rows]

    total_devices = len(parsed_rows)
    avg_cpu = _mean_or_default((r[1] for r in parsed_rows), default=0.0)
    avg_mem = _mean_or_default((r[2] for r in parsed_rows), default=0.0)

    table_rows = []
    for device_id, cpu, mem, launch, fps, cpu_label, mem_label, launch_label, fps_label in parsed_rows:
        table_rows.append(
            f"""
            <tr>
              <td>{device_id}</td>
              <td class=\"{_cpu_class(cpu)}\">{cpu_label}</td>
              <td class=\"{_mem_class(mem)}\">{mem_label}</td>
              <td class=\"{_launch_class(launch)}\">{launch_label}</td>
              <td class=\"{_fps_class(fps)}\">{fps_label}</td>
            </tr>
            """.strip()
        )

    rows_html = "\n".join(table_rows) if table_rows else '<tr><td colspan="5">No device results found.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Performance Report</title>
  <style>
    :root {{
      --good-bg: #e8f7ec;
      --good-txt: #1e7e34;
      --warn-bg: #fff8e1;
      --warn-txt: #8a6d3b;
      --crit-bg: #fdeaea;
      --crit-txt: #a94442;
      --card-bg: #ffffff;
      --page-bg: #f3f5f8;
      --border: #d9dee7;
      --text: #1e293b;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      padding: 24px;
      background: var(--page-bg);
      color: var(--text);
      font-family: Inter, Segoe UI, Roboto, Arial, sans-serif;
      line-height: 1.4;
    }}

    .container {{
      max-width: 980px;
      margin: 0 auto;
      display: grid;
      gap: 18px;
    }}

    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
      padding: 18px;
    }}

    h1, h2 {{ margin-top: 0; }}

    .summary-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    }}

    .summary-item {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      background: #fbfcfe;
    }}

    .summary-label {{
      font-size: 0.85rem;
      color: #475569;
      margin-bottom: 6px;
    }}

    .summary-value {{
      font-size: 1.35rem;
      font-weight: 700;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 10px;
      border: 1px solid var(--border);
    }}

    th, td {{
      padding: 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: middle;
    }}

    th {{
      background: #eef2f7;
      font-weight: 600;
      color: #334155;
    }}

    tr:last-child td {{ border-bottom: none; }}

    .good {{ background: var(--good-bg); color: var(--good-txt); font-weight: 600; }}
    .warning {{ background: var(--warn-bg); color: var(--warn-txt); font-weight: 600; }}
    .critical {{ background: var(--crit-bg); color: var(--crit-txt); font-weight: 600; }}

    .legend {{
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 0.9rem;
    }}

    .badge {{
      border-radius: 999px;
      padding: 4px 10px;
      border: 1px solid transparent;
    }}

    .good.badge {{ border-color: #9ad6a5; }}
    .warning.badge {{ border-color: #f0dca3; }}
    .critical.badge {{ border-color: #f2b9b8; }}
  </style>
</head>
<body>
  <div class="container">
    <section class="card">
      <h1>Performance Report</h1>
      <div class="summary-grid">
        <div class="summary-item">
          <div class="summary-label">Total Devices</div>
          <div class="summary-value">{total_devices}</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Average CPU Usage</div>
          <div class="summary-value">{avg_cpu:.1f}%</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">Average Memory Usage</div>
          <div class="summary-value">{avg_mem:.1f}%</div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>Device Metrics</h2>
      <table>
        <thead>
          <tr>
            <th>Device ID</th>
            <th>CPU Usage</th>
            <th>Memory Usage</th>
            <th>Launch Time</th>
            <th>FPS</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      <div class="legend">
        <span class="badge good">Green = Good</span>
        <span class="badge warning">Yellow = Warning</span>
        <span class="badge critical">Red = Critical</span>
      </div>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    logging_config.setup_logging()
    if not INPUT_FILE.exists():
        LOGGER.error("Input file not found: %s", INPUT_FILE)
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    with INPUT_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    rows = _normalize_records(data)
    html = _build_html(rows)

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    LOGGER.info("Report generated: %s", OUTPUT_FILE)
    print(f"Report generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
