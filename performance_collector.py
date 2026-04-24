#!/usr/bin/env python3
"""Production-grade Android performance collection helpers."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import adb_client
import device_registry
import logging_config

OUTPUT_FILE = Path("performance_results.json")
FINAL_RESULTS_FILE = Path("final_results.json")
DEBUG_LOG_FILE = Path("logs/debug.txt")
LOGS_DIR = Path("logs")
LOGGER = logging_config.get_logger("performance_collector")
DEBUG_MODE = False

MetricValue = Union[float, str]
RUNTIME_OBSERVATION_WINDOW_SECONDS = 60
CPU_SAMPLE_INTERVAL_SECONDS = 5
ADB_RETRY_COUNT = 1
LAST_VALID_RUNTIME_BY_DEVICE: Dict[str, Dict[str, float]] = {}


def set_debug(enabled: bool) -> None:
    global DEBUG_MODE
    DEBUG_MODE = enabled
    if DEBUG_MODE:
        DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEBUG_LOG_FILE.write_text("", encoding="utf-8")


def set_output_directory(output_dir: Path) -> None:
    """Configure output + log paths for a single orchestrated run."""
    global OUTPUT_FILE, FINAL_RESULTS_FILE, DEBUG_LOG_FILE, LOGS_DIR
    OUTPUT_FILE = output_dir / "performance_results.json"
    FINAL_RESULTS_FILE = output_dir / "final_results.json"
    LOGS_DIR = output_dir / "logs"
    DEBUG_LOG_FILE = LOGS_DIR / "debug.txt"


def set_runtime_collection(window_seconds: int, sample_interval_seconds: int) -> None:
    """Tune runtime observation duration and sample interval."""
    global RUNTIME_OBSERVATION_WINDOW_SECONDS, CPU_SAMPLE_INTERVAL_SECONDS
    RUNTIME_OBSERVATION_WINDOW_SECONDS = max(5, window_seconds)
    CPU_SAMPLE_INTERVAL_SECONDS = max(1, sample_interval_seconds)


def set_adb_retries(retries: int) -> None:
    global ADB_RETRY_COUNT
    ADB_RETRY_COUNT = max(0, retries)


def _debug_log(message: str) -> None:
    if not DEBUG_MODE:
        return
    LOGGER.debug(message)
    with DEBUG_LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{message}\n")


def _device_log_path(device_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", device_id)
    path = LOGS_DIR / f"{safe_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _device_log(device_id: str, message: str) -> None:
    with _device_log_path(device_id).open("a", encoding="utf-8") as file:
        file.write(f"{message}\n")


def run_adb_command(cmd: List[str], timeout: int = 10, device_id: str = "") -> Dict[str, Any]:
    """Run an adb command with retry policy and structured response."""
    command_text = " ".join(cmd)
    if device_id:
        _device_log(device_id, f"$ {command_text}")
    _debug_log(f"$ {command_text}")

    response = adb_client.run_adb_command(
        cmd,
        timeout=timeout,
        retries=ADB_RETRY_COUNT,
    )

    if response.output:
        _debug_log(f"stdout:\n{response.output}")
    if response.error:
        _debug_log(f"stderr:\n{response.error}")

    if device_id:
        if response.output:
            _device_log(device_id, f"stdout: {response.output}")
        if response.error:
            _device_log(device_id, f"stderr: {response.error}")

    return response.to_dict()


def _adb_shell_getprop(target: str, prop_name: str) -> str:
    response = run_adb_command(["adb", "-s", target, "shell", "getprop", prop_name], timeout=10, device_id=target)
    if not response["success"]:
        return "N/A"
    value = str(response.get("output", "")).strip()
    return value or "N/A"


def _adb_shell_memtotal_mb(target: str) -> MetricValue:
    response = run_adb_command(["adb", "-s", target, "shell", "cat", "/proc/meminfo"], timeout=10, device_id=target)
    if not response["success"]:
        return "N/A"

    match = re.search(r"MemTotal:\s*(\d+)\s*kB", str(response.get("output", "")), flags=re.IGNORECASE)
    if not match:
        return "N/A"
    total_mb = round(int(match.group(1)) / 1024, 2)
    return total_mb


def _collect_getprops(target: str) -> Dict[str, str]:
    response = run_adb_command(["adb", "-s", target, "shell", "getprop"], timeout=15, device_id=target)
    if not response["success"]:
        return {}

    props: Dict[str, str] = {}
    for line in str(response.get("output", "")).splitlines():
        match = re.match(r"^\[(.+?)\]:\s*\[(.*?)\]\s*$", line.strip())
        if match:
            props[match.group(1)] = match.group(2)
    return props


def collect_device_details(target: str) -> Dict[str, MetricValue]:
    props = _collect_getprops(target)

    def _prop(name: str, fallback: str = "N/A") -> str:
        value = props.get(name)
        if value is None:
            return fallback
        value = value.strip()
        return value or fallback

    cpu_abi = _prop("ro.product.cpu.abi")
    abi_list = _prop("ro.product.cpu.abilist")
    cpu_details = abi_list if abi_list != "N/A" else cpu_abi

    details: Dict[str, MetricValue] = {
        "model": _prop("ro.product.model"),
        "manufacturer": _prop("ro.product.manufacturer"),
        "brand": _prop("ro.product.brand"),
        "device": _prop("ro.product.device"),
        "android_version": _prop("ro.build.version.release"),
        "sdk_int": _prop("ro.build.version.sdk"),
        "build_fingerprint": _prop("ro.build.fingerprint"),
        "kernel_version": _prop("ro.kernel.version"),
        "cpu": cpu_details,
        "abi_list": abi_list,
        "total_memory_mb": _adb_shell_memtotal_mb(target),
    }
    return details


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
        _debug_log(f"CPU parse candidate(top): {line}")
        percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        if percent_match:
            value = float(percent_match.group(1))
            _debug_log(f"Parsed CPU from line (% style): {line} => {value}")
            return value

        # Newer Android top outputs may expose %CPU as a plain numeric token near status column.
        tokens = line.split()
        for idx, token in enumerate(tokens):
            if token in {"S", "R", "D", "T", "Z", "X", "I"} and idx + 1 < len(tokens):
                candidate = tokens[idx + 1]
                if re.fullmatch(r"\d+(?:\.\d+)?", candidate):
                    value = float(candidate)
                    _debug_log(f"Parsed CPU from line (token style): {line} => {value}")
                    return value
        for token in tokens:
            if re.fullmatch(r"\d+(?:\.\d+)?", token):
                value = float(token)
                if 0.0 <= value <= 400.0:
                    _debug_log(f"Parsed CPU from line (generic token): {line} => {value}")
                    return value
    return "N/A"


def parse_cpu_usage_cpuinfo(cpuinfo_output: str, package_name: str) -> MetricValue:
    escaped_pkg = re.escape(package_name)
    candidates: List[float] = []
    for line in cpuinfo_output.splitlines():
        if package_name not in line:
            continue
        _debug_log(f"CPU parse candidate(cpuinfo): {line}")
        match = re.search(rf"^\s*([0-9]+(?:\.[0-9]+)?)%\s+\d+/{escaped_pkg}(?::\S+)?\b", line)
        if not match:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", line)
        if match:
            candidates.append(float(match.group(1)))
    if candidates:
        # dumpsys cpuinfo can include multiple process lines; max is the app process load.
        value = round(max(candidates), 2)
        _debug_log(f"Parsed CPU from cpuinfo candidates: {candidates} => {value}")
        return value
    return "N/A"


def _to_mb(value: float, unit: str) -> float:
    normalized_unit = unit.upper()
    if normalized_unit == "KB":
        return value / 1024
    if normalized_unit == "GB":
        return value * 1024
    return value


def parse_memory_metrics(meminfo_output: str) -> Dict[str, MetricValue]:
    normalized = meminfo_output.replace(",", "")
    result: Dict[str, MetricValue] = {"total_pss_mb": "N/A", "total_rss_mb": "N/A", "total_mb": "N/A"}
    metric_patterns: List[Tuple[str, str]] = [
        ("total_pss_mb", r"TOTAL\s+PSS:\s*([0-9]+(?:\.[0-9]+)?)\s*(KB|MB|GB)?"),
        ("total_rss_mb", r"TOTAL\s+RSS:\s*([0-9]+(?:\.[0-9]+)?)\s*(KB|MB|GB)?"),
        ("total_mb", r"\bTOTAL\b\s+([0-9]+(?:\.[0-9]+)?)\s*(KB|MB|GB)?"),
    ]

    for key, pattern in metric_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = round(_to_mb(float(match.group(1)), (match.group(2) or "KB")), 2)
        _debug_log(f"Parsed memory metric {key} from line: {match.group(0)} => {parsed} MB")
        result[key] = parsed
    if isinstance(result["total_pss_mb"], float):
        result["total_mb"] = result["total_pss_mb"]
    elif isinstance(result["total_rss_mb"], float):
        result["total_mb"] = result["total_rss_mb"]
    return result


def parse_memory_mb(meminfo_output: str) -> MetricValue:
    metrics = parse_memory_metrics(meminfo_output)
    if isinstance(metrics.get("total_mb"), float):
        return metrics["total_mb"]
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

    percentile_match = re.search(r"50th percentile:\s*([0-9]+(?:\.[0-9]+)?)ms", gfxinfo_output, flags=re.IGNORECASE)
    if percentile_match:
        frame_time_ms = float(percentile_match.group(1))
        if frame_time_ms > 0:
            fps = round(min(240.0, 1000.0 / frame_time_ms), 2)
            _debug_log(f"Parsed FPS from 50th percentile frame time: {fps}")
            return fps

    total = re.search(r"Total frames rendered:\s*(\d+)", gfxinfo_output, flags=re.IGNORECASE)
    if total and int(total.group(1)) > 0:
        _debug_log("Frame stats found but no janky %; FPS unavailable")
    return "N/A"


def parse_surfaceflinger_fps(latency_output: str) -> MetricValue:
    rows = [line.strip() for line in latency_output.splitlines() if line.strip()]
    deltas_ns: List[int] = []
    previous_present: Optional[int] = None
    for row in rows:
        if not re.fullmatch(r"[-0-9 ]+", row):
            continue
        parts = row.split()
        if len(parts) < 3:
            continue
        present_ns = int(parts[1])
        if present_ns <= 0 or present_ns >= 9223372036854775807:
            continue
        if previous_present is not None and present_ns > previous_present:
            deltas_ns.append(present_ns - previous_present)
        previous_present = present_ns
    if not deltas_ns:
        return "N/A"
    average_delta_ns = mean(deltas_ns)
    if average_delta_ns <= 0:
        return "N/A"
    fps = round(min(240.0, 1_000_000_000.0 / average_delta_ns), 2)
    _debug_log(f"Parsed FPS from SurfaceFlinger latency: {fps} (samples={len(deltas_ns)})")
    return fps


def _runtime_metric_or_cached(value: MetricValue, cached: MetricValue, metric_name: str, target: str) -> MetricValue:
    if isinstance(value, float):
        return value
    if isinstance(cached, float):
        _debug_log(f"[{target}] Using cached {metric_name}: {cached}")
        return cached
    return "N/A"


def _collect_runtime_metrics_with_retries(
    target: str,
    package_name: str,
    activity_name: str,
    attempts: int = 3,
) -> Dict[str, Any]:
    runtime_last: Dict[str, Any] = {}
    cached_metrics: Dict[str, MetricValue] = {
        "cpu": LAST_VALID_RUNTIME_BY_DEVICE.get(target, {}).get("cpu", "N/A"),
        "memory": LAST_VALID_RUNTIME_BY_DEVICE.get(target, {}).get("memory", "N/A"),
        "fps": LAST_VALID_RUNTIME_BY_DEVICE.get(target, {}).get("fps", "N/A"),
    }

    for attempt in range(1, max(1, attempts) + 1):
        runtime_last = collect_performance_metrics(target, package_name, activity_name, launch_before_collect=False)
        for metric in ("cpu", "memory", "fps"):
            value = runtime_last.get(metric, "N/A")
            if isinstance(value, float):
                cached_metrics[metric] = value
        if all(isinstance(cached_metrics[m], float) for m in ("cpu", "memory", "fps")):
            break
        _debug_log(f"[{target}] Runtime metric attempt {attempt}/{attempts} incomplete; retrying.")
        time.sleep(1)

    runtime_last["cpu"] = _runtime_metric_or_cached(runtime_last.get("cpu", "N/A"), cached_metrics["cpu"], "cpu", target)
    runtime_last["memory"] = _runtime_metric_or_cached(runtime_last.get("memory", "N/A"), cached_metrics["memory"], "memory", target)
    runtime_last["fps"] = _runtime_metric_or_cached(runtime_last.get("fps", "N/A"), cached_metrics["fps"], "fps", target)
    LAST_VALID_RUNTIME_BY_DEVICE.setdefault(target, {})
    for metric in ("cpu", "memory", "fps"):
        value = runtime_last.get(metric, "N/A")
        if isinstance(value, float):
            LAST_VALID_RUNTIME_BY_DEVICE[target][metric] = value
    return runtime_last


def _get_package_pids(target: str, package_name: str) -> List[str]:
    result = run_adb_command(["adb", "-s", target, "shell", "pidof", package_name], timeout=10, device_id=target)
    if not result["success"] or not result["output"]:
        return []
    return [pid for pid in result["output"].split() if pid.isdigit()]


def collect_gc_count(target: str, package_name: str, max_lines: int = 4000) -> int:
    pids = set(_get_package_pids(target, package_name))
    logcat = run_adb_command(
        ["adb", "-s", target, "logcat", "-d", "-t", str(max_lines), "-v", "threadtime"],
        timeout=20,
        device_id=target,
    )
    if not logcat["success"] or not logcat["output"]:
        return 0

    gc_keywords = (
        "GC",
        "GC freed",
        "concurrent mark sweep",
        "concurrent copying",
        "young concurrent copying",
        "Background concurrent",
    )
    package_process_prefix = f"{package_name}:"
    total = 0
    for line in logcat["output"].splitlines():
        if not any(keyword in line for keyword in gc_keywords):
            continue
        pid_match = re.search(r"^\S+\s+\S+\s+(\d+)\s+\d+\s+[A-Z]\s+\S+\s*:", line)
        pid = pid_match.group(1) if pid_match else None
        if pid and pid in pids:
            total += 1
            continue

        if package_name in line or package_process_prefix in line:
            total += 1
            continue
        if not pids:
            total += 1

    _debug_log(f"[{target}] GC count for {package_name}: {total}")
    return total


def collect_cpu_average(
    target: str,
    package_name: str,
    duration_seconds: int = RUNTIME_OBSERVATION_WINDOW_SECONDS,
    interval_seconds: int = CPU_SAMPLE_INTERVAL_SECONDS,
) -> MetricValue:
    samples: List[float] = []
    deadline = time.time() + max(1, duration_seconds)
    while time.time() < deadline:
        top = run_adb_command(["adb", "-s", target, "shell", "top", "-n", "1"], timeout=15, device_id=target)
        cpu = parse_cpu_usage(top["output"], package_name) if top["success"] else "N/A"
        if cpu == "N/A":
            cpuinfo = run_adb_command(
                ["adb", "-s", target, "shell", "dumpsys", "cpuinfo", package_name],
                timeout=15,
                device_id=target,
            )
            if cpuinfo["success"]:
                cpu = parse_cpu_usage_cpuinfo(cpuinfo["output"], package_name)
        if isinstance(cpu, float):
            samples.append(cpu)
            _debug_log(f"[{target}] CPU sample: {cpu}%")
        time.sleep(max(1, interval_seconds))

    if not samples:
        return "N/A"
    avg_cpu = round(mean(samples), 2)
    _debug_log(f"[{target}] Average CPU over {duration_seconds}s: {avg_cpu}%")
    return avg_cpu


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


def collect_performance_metrics(
    target: str,
    package_name: str,
    activity_name: str,
    launch_before_collect: bool = True,
) -> Dict[str, Any]:
    component = f"{package_name}/{activity_name}"
    launch: Dict[str, Any]
    if launch_before_collect:
        launch = _start_app(target, component, timeout=20)
    else:
        launch = {"success": True, "output": "", "error": ""}

    top = run_adb_command(["adb", "-s", target, "shell", "top", "-n", "1"], timeout=15, device_id=target)
    cpuinfo = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "cpuinfo", package_name], timeout=15, device_id=target)
    mem = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "meminfo", package_name], timeout=15, device_id=target)
    gfx = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "gfxinfo", package_name], timeout=20, device_id=target)
    sf_latency = run_adb_command(["adb", "-s", target, "shell", "dumpsys", "SurfaceFlinger", "--latency"], timeout=20, device_id=target)

    _debug_log(f"Raw TOP output:\n{top['output']}")
    _debug_log(f"Raw CPUINFO output:\n{cpuinfo['output']}")
    _debug_log(f"Raw MEMINFO output:\n{mem['output']}")
    _debug_log(f"Raw START output:\n{launch['output']}")
    _debug_log(f"Raw GFXINFO output:\n{gfx['output']}")
    _debug_log(f"Raw SurfaceFlinger latency output:\n{sf_latency['output']}")

    cpu = parse_cpu_usage(top["output"], package_name) if top["success"] else "N/A"
    if cpu == "N/A" and cpuinfo["success"]:
        cpu = parse_cpu_usage_cpuinfo(cpuinfo["output"], package_name)
    memory_metrics = parse_memory_metrics(mem["output"]) if mem["success"] else {"total_mb": "N/A", "total_pss_mb": "N/A", "total_rss_mb": "N/A"}
    memory = memory_metrics.get("total_mb", "N/A")
    launch_times = parse_launch_times(launch["output"]) if launch["success"] else {"ThisTime": "N/A", "TotalTime": "N/A", "WaitTime": "N/A"}
    fps = parse_fps(gfx["output"]) if gfx["success"] else "N/A"
    if fps == "N/A" and sf_latency["success"]:
        fps = parse_surfaceflinger_fps(sf_latency["output"])

    cpu_status = "ok" if isinstance(cpu, float) else "missing"
    memory_status = "ok" if isinstance(memory, float) else "missing"
    fps_status = "ok" if isinstance(fps, float) and fps > 0 else ("abnormal_zero" if fps == 0.0 else "missing")
    if cpu == "N/A":
        _debug_log(f"[{target}] CPU parse unavailable (top success={top['success']}, cpuinfo success={cpuinfo['success']})")
    if memory == "N/A":
        _debug_log(f"[{target}] Memory parse unavailable (meminfo success={mem['success']})")
    if fps == "N/A":
        _debug_log(f"[{target}] FPS parse unavailable (gfxinfo success={gfx['success']}, sf success={sf_latency['success']})")
    _debug_log(
        f"[{target}] Processed runtime metrics => cpu={cpu}, memory={memory}, fps={fps}, "
        f"memory_metrics={memory_metrics}, statuses=(cpu={cpu_status}, memory={memory_status}, fps={fps_status})"
    )

    return {
        "cpu": cpu,
        "memory": memory,
        "fps": fps,
        "memory_metrics": memory_metrics,
        "status": {"cpu": cpu_status, "memory": memory_status, "fps": fps_status},
        "launch_time": launch_times,
        "raw": {
            "top": top,
            "cpuinfo": cpuinfo,
            "meminfo": mem,
            "start": launch,
            "gfxinfo": gfx,
            "surfaceflinger_latency": sf_latency,
        },
    }


def run_full_benchmark(target: str, package_name: str, activity_name: str, iterations: int = 10) -> Dict[str, Any]:
    component = f"{package_name}/{activity_name}"
    LOGGER.info("[%s] Benchmark step 1/4: Collect device details", target)
    device_details = collect_device_details(target)

    LOGGER.info("[%s] Benchmark step 2/4: Validate device state and collect runtime metrics", target)
    state = run_adb_command(["adb", "-s", target, "get-state"], timeout=10, device_id=target)
    if not state["success"] or "device" not in state.get("output", ""):
        _debug_log(f"[{target}] Device not in ready state before benchmark: {state}")

    run_adb_command(["adb", "-s", target, "logcat", "-c"], timeout=10, device_id=target)

    _start_app(target, component, timeout=20)
    avg_cpu = collect_cpu_average(
        target,
        package_name,
        duration_seconds=RUNTIME_OBSERVATION_WINDOW_SECONDS,
        interval_seconds=CPU_SAMPLE_INTERVAL_SECONDS,
    )
    runtime = _collect_runtime_metrics_with_retries(target, package_name, activity_name, attempts=3)
    runtime_cpu = _runtime_metric_or_cached(runtime.get("cpu", "N/A"), avg_cpu, "cpu", target)
    gc_count = collect_gc_count(target, package_name)

    LOGGER.info("[%s] Benchmark step 3/4: Run startup benchmarks (cold/warm/hot)", target)
    startup = {
        "cold": run_start_test(target, "cold", component, package_name, iterations),
        "warm": run_start_test(target, "warm", component, package_name, iterations),
        "hot": run_start_test(target, "hot", component, package_name, iterations),
    }

    LOGGER.info("[%s] Benchmark step 4/4: Build benchmark result payload", target)
    return {
        "device_details": device_details,
        "runtime_metrics": {
            "cpu": runtime_cpu,
            "memory": runtime.get("memory", "N/A"),
            "fps": runtime.get("fps", "N/A"),
            "gc_count": gc_count,
            "memory_metrics": runtime.get("memory_metrics", {}),
            "status": runtime.get("status", {}),
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
    parser.add_argument("--runtime-window", type=int, default=60, help="CPU sampling window seconds")
    parser.add_argument("--sample-interval", type=int, default=5, help="CPU sampling interval seconds")
    parser.add_argument("--adb-retries", type=int, default=1, help="Retries for adb commands")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.iterations <= 0:
        raise ValueError("--iterations must be > 0")

    logging_config.setup_logging(args.debug)
    set_debug(args.debug)
    set_runtime_collection(args.runtime_window, args.sample_interval)
    set_adb_retries(args.adb_retries)

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
