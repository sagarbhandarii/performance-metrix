#!/usr/bin/env python3
"""Collect Android performance metrics from all available devices."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import device_registry
import logging_config

OUTPUT_FILE = Path(__file__).with_name("performance_results.json")
LOGGER = logging_config.get_logger("performance_collector")


def run_adb(target: str, shell_command: str) -> str:
    """Run an adb shell command for a target and return stdout."""
    LOGGER.debug("[%s] adb shell %s", target, shell_command)
    result = subprocess.run(
        ["adb", "-s", target, "shell", "sh", "-c", shell_command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        message = details or f"adb exited with code {result.returncode} for shell command: {shell_command}"
        LOGGER.error("[%s] adb command failed: %s", target, message)
        raise RuntimeError(message)
    return result.stdout


def build_target(device: Dict[str, object]) -> str:
    """Resolve a device record into adb -s target."""
    device_id = str(device.get("device_id", "")).strip()
    if ":" in device_id:
        return device_id

    ip_address = device.get("ip") or device.get("ip_address")
    port = device.get("port")
    if ip_address and port:
        return f"{ip_address}:{port}"

    return device_id


def get_devices() -> Iterable[Dict[str, object]]:
    """Return devices marked as available in the local registry."""
    return [device for device in device_registry.get_all_devices() if device.get("status") == "available"]


def parse_cpu_usage(top_output: str, package_name: str) -> Optional[float]:
    """Parse CPU percent from top output line containing package name."""
    package_pattern = re.escape(package_name)
    cpu_candidates = []
    regexes = [
        rf"\b(\d+(?:\.\d+)?)\s*%(?=[^\n]*\b{package_pattern}\b)",
        rf"\b{package_pattern}\b[^\n]*?\b(\d+(?:\.\d+)?)\s*%",
        rf"\b(\d+(?:\.\d+)?)\b(?=[^\n]*\b{package_pattern}\b)",
    ]

    for regex in regexes:
        for match in re.finditer(regex, top_output, flags=re.IGNORECASE):
            value = float(match.group(1))
            if 0.0 <= value <= 400.0:
                cpu_candidates.append(value)

    if cpu_candidates:
        return round(max(cpu_candidates), 2)
    return None


def _to_kb(value: float, unit: str) -> int:
    """Normalize memory values to KB."""
    unit_normalized = unit.strip().lower()
    if unit_normalized in {"kb", "k"}:
        return int(value)
    if unit_normalized in {"mb", "m"}:
        return int(value * 1024)
    if unit_normalized in {"gb", "g"}:
        return int(value * 1024 * 1024)
    return int(value)


def parse_memory_kb(meminfo_output: str) -> Optional[int]:
    """Parse total PSS memory from dumpsys meminfo output and normalize to KB."""
    regexes = [
        r"TOTAL\s+PSS:\s*([0-9]+(?:\.[0-9]+)?)\s*([KMG]?B?)",
        r"\bTOTAL\b[^\n]*?\b([0-9]+(?:\.[0-9]+)?)\s*([KMG]?B?)\b",
        r"\bTOTAL\s+([0-9]+(?:\.[0-9]+)?)\s*([KMG]?B?)\b",
    ]
    for regex in regexes:
        match = re.search(regex, meminfo_output, flags=re.IGNORECASE)
        if not match:
            continue
        number = float(match.group(1))
        unit = match.group(2) or "KB"
        return _to_kb(number, unit)
    return None


def parse_launch_time_ms(start_output: str) -> Dict[str, Optional[int]]:
    """Parse launch timing values in milliseconds from am start -W output."""
    parsed: Dict[str, Optional[int]] = {"this_time": None, "total_time": None, "wait_time": None}
    pattern_map = {
        "this_time": r"ThisTime:\s*(\d+)",
        "total_time": r"TotalTime:\s*(\d+)",
        "wait_time": r"WaitTime:\s*(\d+)",
    }
    for key, pattern in pattern_map.items():
        match = re.search(pattern, start_output, flags=re.IGNORECASE)
        if match:
            parsed[key] = int(match.group(1))
    return parsed


def parse_fps(gfxinfo_output: str) -> Dict[str, Optional[float]]:
    """Parse FPS and related frame metrics from dumpsys gfxinfo output."""
    parsed: Dict[str, Optional[float]] = {
        "fps": None,
        "total_frames": None,
        "janky_frames": None,
        "janky_percent": None,
    }

    total_match = re.search(r"Total frames rendered:\s*(\d+)", gfxinfo_output, flags=re.IGNORECASE)
    if total_match:
        parsed["total_frames"] = float(total_match.group(1))

    janky_match = re.search(
        r"Janky frames:\s*(\d+)\s*\((\d+(?:\.\d+)?)%\)",
        gfxinfo_output,
        flags=re.IGNORECASE,
    )
    if janky_match:
        parsed["janky_frames"] = float(janky_match.group(1))
        parsed["janky_percent"] = float(janky_match.group(2))

    frame_times_ms = []
    for value in re.findall(r"\b([0-9]+(?:\.[0-9]+)?)\s*ms\b", gfxinfo_output, flags=re.IGNORECASE):
        timing = float(value)
        if 0.0 < timing < 1000.0:
            frame_times_ms.append(timing)

    if frame_times_ms:
        average_ms = sum(frame_times_ms) / len(frame_times_ms)
        if average_ms > 0:
            parsed["fps"] = round(1000.0 / average_ms, 2)
            return parsed

    if parsed["janky_percent"] is not None:
        parsed["fps"] = round(max(0.0, 60.0 * (1.0 - (parsed["janky_percent"] / 100.0))), 2)
    return parsed


def collect_for_device(target: str, package_name: str, activity_name: str) -> Dict[str, Any]:
    """Collect all requested metrics for one adb target."""
    LOGGER.info("[%s] Collecting performance metrics", target)
    top_output = run_adb(target, f"top -n 1 | grep {shlex.quote(package_name)} || true")
    meminfo_output = run_adb(target, f"dumpsys meminfo {package_name}")
    start_output = run_adb(target, f"am start -W {package_name}/{activity_name}")
    gfxinfo_output = run_adb(target, f"dumpsys gfxinfo {package_name}")
    LOGGER.debug("[%s] Raw CPU output:\n%s", target, top_output.strip())
    LOGGER.debug("[%s] Raw memory output:\n%s", target, meminfo_output.strip())
    LOGGER.debug("[%s] Raw launch output:\n%s", target, start_output.strip())
    LOGGER.debug("[%s] Raw FPS output:\n%s", target, gfxinfo_output.strip())

    cpu = parse_cpu_usage(top_output, package_name)
    memory_kb = parse_memory_kb(meminfo_output)
    launch_times = parse_launch_time_ms(start_output)
    fps_data = parse_fps(gfxinfo_output)

    LOGGER.debug("[%s] Parsed CPU: %s", target, cpu)
    LOGGER.debug("[%s] Parsed memory (KB): %s", target, memory_kb)
    LOGGER.debug("[%s] Parsed launch times: %s", target, launch_times)
    LOGGER.debug("[%s] Parsed FPS data: %s", target, fps_data)

    return {
        "cpu": {"value": cpu, "raw": top_output, "parsed": {"cpu_percent": cpu}},
        "memory": {"value_kb": memory_kb, "raw": meminfo_output, "parsed": {"total_pss_kb": memory_kb}},
        "launch_time": {
            "value_ms": launch_times.get("total_time"),
            "raw": start_output,
            "parsed": launch_times,
        },
        "fps": {"value": fps_data.get("fps"), "raw": gfxinfo_output, "parsed": fps_data},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect CPU, memory, launch time, and FPS for each device.")
    parser.add_argument("--package", required=True, help="App package name")
    parser.add_argument("--activity", required=True, help="App activity name")
    return parser.parse_args()


def main() -> None:
    logging_config.setup_logging()
    args = parse_args()
    results: Dict[str, Dict[str, Any]] = {}

    for device in get_devices():
        target = build_target(device)
        if not target:
            LOGGER.error("Skipping device with missing target: %s", device)
            continue

        device_id = str(device.get("device_id") or target)
        try:
            results[device_id] = collect_for_device(target, args.package, args.activity)
        except RuntimeError as error:
            LOGGER.error("[%s] Metrics collection failed: %s", device_id, error)
            results[device_id] = {
                "cpu": {"value": None, "raw": "", "parsed": {}},
                "memory": {"value_kb": None, "raw": "", "parsed": {}},
                "launch_time": {"value_ms": None, "raw": "", "parsed": {}},
                "fps": {"value": None, "raw": "", "parsed": {}},
                "error": str(error),
            }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2)
    LOGGER.info("Saved performance results: %s", OUTPUT_FILE)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
