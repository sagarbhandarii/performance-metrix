#!/usr/bin/env python3
"""Collect Android performance metrics from all available devices."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Optional

import device_registry

OUTPUT_FILE = Path(__file__).with_name("performance_results.json")


def run_adb(target: str, shell_command: str) -> str:
    """Run an adb shell command for a target and return stdout."""
    result = subprocess.run(
        ["adb", "-s", target, "shell", shell_command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown adb error").strip()
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
    for line in top_output.splitlines():
        if package_name not in line:
            continue
        match = re.search(r"(\d+(?:\.\d+)?)%", line)
        if match:
            return float(match.group(1))
    return None


def parse_memory_kb(meminfo_output: str) -> Optional[int]:
    """Parse total PSS memory (kB) from dumpsys meminfo output."""
    for line in meminfo_output.splitlines():
        if "TOTAL PSS:" in line.upper():
            numbers = re.findall(r"\d+", line)
            if numbers:
                return int(numbers[0])

    match = re.search(r"TOTAL\s+(\d+)", meminfo_output)
    return int(match.group(1)) if match else None


def parse_launch_time_ms(start_output: str) -> Optional[int]:
    """Parse launch timing in milliseconds from am start -W output."""
    patterns = [r"TotalTime:\s*(\d+)", r"ThisTime:\s*(\d+)", r"WaitTime:\s*(\d+)"]
    for pattern in patterns:
        match = re.search(pattern, start_output)
        if match:
            return int(match.group(1))
    return None


def parse_fps(gfxinfo_output: str) -> Optional[float]:
    """Parse FPS from gfxinfo framestats or profile data."""
    frame_times_ms = [float(value) for value in re.findall(r"([0-9]+(?:\.[0-9]+)?)ms", gfxinfo_output)]
    if frame_times_ms:
        average_ms = sum(frame_times_ms) / len(frame_times_ms)
        if average_ms > 0:
            return round(1000.0 / average_ms, 2)

    janky_match = re.search(r"Janky frames:\s*(\d+)\s*\((\d+\.\d+)%\)", gfxinfo_output)
    if janky_match:
        jank_percent = float(janky_match.group(2))
        return round(max(0.0, 60.0 * (1.0 - (jank_percent / 100.0))), 2)

    return None


def collect_for_device(target: str, package_name: str, activity_name: str) -> Dict[str, Optional[float]]:
    """Collect all requested metrics for one adb target."""
    top_output = run_adb(target, f"top -n 1 | grep {package_name}")
    meminfo_output = run_adb(target, f"dumpsys meminfo {package_name}")
    start_output = run_adb(target, f"am start -W {package_name}/{activity_name}")
    gfxinfo_output = run_adb(target, f"dumpsys gfxinfo {package_name}")

    return {
        "cpu": parse_cpu_usage(top_output, package_name),
        "memory": parse_memory_kb(meminfo_output),
        "launch_time": parse_launch_time_ms(start_output),
        "fps": parse_fps(gfxinfo_output),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect CPU, memory, launch time, and FPS for each device.")
    parser.add_argument("--package", required=True, help="App package name")
    parser.add_argument("--activity", required=True, help="App activity name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results: Dict[str, Dict[str, Optional[float]]] = {}

    for device in get_devices():
        target = build_target(device)
        if not target:
            continue

        device_id = str(device.get("device_id") or target)
        try:
            results[device_id] = collect_for_device(target, args.package, args.activity)
        except RuntimeError as error:
            results[device_id] = {
                "cpu": None,
                "memory": None,
                "launch_time": None,
                "fps": None,
                "error": str(error),
            }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
