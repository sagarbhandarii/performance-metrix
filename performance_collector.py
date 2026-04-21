#!/usr/bin/env python3
"""Production-grade Android performance collection helpers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Union

import device_registry
import logging_config

OUTPUT_FILE = Path("performance_results.json")
FINAL_RESULTS_FILE = Path("final_results.json")
DEBUG_LOG_FILE = Path("logs/debug.txt")
LOGGER = logging_config.get_logger("performance_collector")
DEBUG_MODE = False

MetricValue = Union[float, str]


def set_debug(enabled: bool) -> None:
    global DEBUG_MODE
    DEBUG_MODE = enabled
    if DEBUG_MODE:
        DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEBUG_LOG_FILE.write_text("", encoding="utf-8")


def _debug_log(message: str) -> None:
    if not DEBUG_MODE:
        return
    LOGGER.debug(message)
    with DEBUG_LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{message}\n")


def _device_log_path(device_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", device_id)
    path = Path("logs") / f"{safe_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _device_log(device_id: str, message: str) -> None:
    with _device_log_path(device_id).open("a", encoding="utf-8") as file:
        file.write(f"{message}\n")


def run_adb_command(cmd: List[str], timeout: int = 10, device_id: str = "") -> Dict[str, Any]:
    """Run an adb command safely with timeout and structured response."""
    command_text = " ".join(cmd)
    if device_id:
        print(f"Running on device: {device_id}")
        _device_log(device_id, f"Running on device: {device_id}")
        _device_log(device_id, f"$ {command_text}")
    _debug_log(f"$ {command_text}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "").strip()
        err = f"timeout after {timeout}s"
        _debug_log(f"ERROR: {err}")
        return {"success": False, "output": output, "error": err}
    except subprocess.CalledProcessError as error:
        output = (error.stdout or "").strip()
        err = (error.stderr or str(error)).strip()
        _debug_log(f"ERROR: {err}")
        return {"success": False, "output": output, "error": err}

    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    _debug_log(f"stdout:\n{output}")
    if err:
        _debug_log(f"stderr:\n{err}")
    if device_id:
        if output:
            _device_log(device_id, f"stdout: {output}")
        if err:
            _device_log(device_id, f"stderr: {err}")

    success = result.returncode == 0
    if not success and not err:
        err = f"exit code {result.returncode}"
    return {"success": success, "output": output, "error": err}


def build_target(device: Dict[str, object]) -> str:
    device_id = str(device.get("device_id", "")).strip()
    if ":" in device_id:
        return device_id
    ip_address = device.get("ip") or device.get("ip_address")
    port = device.get("port")
    if ip_address and port:
        return f"{ip_address}:{port}"
    return device_id


def get_devices() -> Iterable[Dict[str, object]]:
    return [device for device in device_registry.get_all_devices() if device.get("status") == "available"]


def parse_cpu_usage(top_output: str, package_name: str) -> MetricValue:
    for line in top_output.splitlines():
        if package_name not in line:
            continue
        match = re.search(r"(\d+(?:\.\d+)?)%", line)
        if match:
            value = float(match.group(1))
            _debug_log(f"Parsed CPU from line: {line} => {value}")
            return value
    return "N/A"


def parse_memory_mb(meminfo_output: str) -> MetricValue:
    patterns = [
        r"TOTAL\s+PSS:\s*([0-9]+(?:\.[0-9]+)?)\s*(KB|MB|GB)?",
        r"\bTOTAL\b\s+([0-9]+(?:\.[0-9]+)?)\s*(KB|MB|GB)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, meminfo_output, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "KB").upper()
        if unit == "KB":
            value = value / 1024
        elif unit == "GB":
            value = value * 1024
        parsed = round(value, 2)
        _debug_log(f"Parsed memory from line: {match.group(0)} => {parsed} MB")
        return parsed
    return "N/A"


def parse_launch_times(start_output: str) -> Dict[str, Union[int, str]]:
    result: Dict[str, Union[int, str]] = {"ThisTime": "N/A", "TotalTime": "N/A", "WaitTime": "N/A"}
    for key in result:
        match = re.search(rf"{key}:\s*(\d+)", start_output, flags=re.IGNORECASE)
        if match:
            result[key] = int(match.group(1))
    _debug_log(f"Parsed launch times => {result}")
    return result


def parse_fps(gfxinfo_output: str) -> MetricValue:
    janky = re.search(r"Janky frames:\s*\d+\s*\(([0-9]+(?:\.[0-9]+)?)%\)", gfxinfo_output, flags=re.IGNORECASE)
    if janky:
        janky_pct = float(janky.group(1))
        fps = round(max(0.0, 60.0 * (1.0 - janky_pct / 100.0)), 2)
        _debug_log(f"Parsed FPS from janky frames: {fps}")
        return fps

    total = re.search(r"Total frames rendered:\s*(\d+)", gfxinfo_output, flags=re.IGNORECASE)
    if total and int(total.group(1)) > 0:
        _debug_log("Frame stats found but no janky %; FPS unavailable")
    return "N/A"


def _start_app(target: str, component: str, timeout: int = 20) -> Dict[str, Any]:
    return run_adb_command(["adb", "-s", target, "shell", "am", "start", "-W", "-n", component], timeout=timeout, device_id=target)


def run_start_test(device: str, start_type: str, component: str, package: str, iterations: int = 10) -> Dict[str, Any]:
    values: List[float] = []
    for i in range(1, iterations + 1):
        _debug_log(f"[{device}] {start_type} iteration {i}/{iterations}")

        if start_type == "cold":
            run_adb_command(["adb", "-s", device, "shell", "am", "force-stop", package], timeout=10, device_id=device)
        elif start_type == "warm":
            run_adb_command(["adb", "-s", device, "shell", "am", "start", "-n", component], timeout=15, device_id=device)
            run_adb_command(["adb", "-s", device, "shell", "input", "keyevent", "KEYCODE_HOME"], timeout=10, device_id=device)
        elif start_type == "hot":
            run_adb_command(["adb", "-s", device, "shell", "am", "start", "-n", component], timeout=15, device_id=device)
        else:
            raise ValueError(f"Unknown start type: {start_type}")

        launch = _start_app(device, component, timeout=20)
        if launch["success"]:
            parsed = parse_launch_times(launch["output"])
            total = parsed.get("TotalTime")
            if isinstance(total, int):
                values.append(float(total))

        time.sleep(2)

    if values:
        return {
            "values": values,
            "avg": round(mean(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }
    return {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"}


def collect_performance_metrics(target: str, package_name: str, activity_name: str) -> Dict[str, Any]:
    component = f"{package_name}/{activity_name}"

    top = run_adb_command(["adb", "-s", target, "shell", "top", "-n", "1"], timeout=15, device_id=target)
    mem = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "meminfo", package_name], timeout=15, device_id=target)
    launch = _start_app(target, component, timeout=20)
    gfx = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "gfxinfo", package_name], timeout=20, device_id=target)

    _debug_log(f"Raw TOP output:\n{top['output']}")
    _debug_log(f"Raw MEMINFO output:\n{mem['output']}")
    _debug_log(f"Raw START output:\n{launch['output']}")
    _debug_log(f"Raw GFXINFO output:\n{gfx['output']}")

    cpu = parse_cpu_usage(top["output"], package_name) if top["success"] else "N/A"
    memory = parse_memory_mb(mem["output"]) if mem["success"] else "N/A"
    launch_times = parse_launch_times(launch["output"]) if launch["success"] else {"ThisTime": "N/A", "TotalTime": "N/A", "WaitTime": "N/A"}
    fps = parse_fps(gfx["output"]) if gfx["success"] else "N/A"

    return {
        "cpu_percent": cpu,
        "memory_mb": memory,
        "launch_time": launch_times,
        "fps": fps,
        "raw": {
            "top": top,
            "meminfo": mem,
            "start": launch,
            "gfxinfo": gfx,
        },
    }


def run_full_benchmark(target: str, package_name: str, activity_name: str, iterations: int = 10) -> Dict[str, Any]:
    component = f"{package_name}/{activity_name}"

    # Runtime metrics should be captured immediately after launch to reflect active app behavior.
    _start_app(target, component, timeout=20)
    runtime = collect_performance_metrics(target, package_name, activity_name)

    startup = {
        "cold": run_start_test(target, "cold", component, package_name, iterations),
        "warm": run_start_test(target, "warm", component, package_name, iterations),
        "hot": run_start_test(target, "hot", component, package_name, iterations),
    }

    return {
        "runtime_metrics": {
            "cpu": runtime.get("cpu_percent", "N/A"),
            "memory": runtime.get("memory_mb", "N/A"),
            "fps": runtime.get("fps", "N/A"),
        },
        "startup_metrics": startup,
        "runtime_details": {
            "launch_time": runtime.get("launch_time", {}),
            "raw": runtime.get("raw", {}),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Android launch + runtime metrics.")
    parser.add_argument("--package", required=True)
    parser.add_argument("--activity", required=True)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging_config.setup_logging(args.debug)
    set_debug(args.debug)

    results: Dict[str, Any] = {}
    for device in get_devices():
        target = build_target(device)
        device_id = str(device.get("device_id") or target)
        if not target:
            continue
        results[device_id] = run_full_benchmark(target, args.package, args.activity, iterations=args.iterations)

    OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    FINAL_RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
