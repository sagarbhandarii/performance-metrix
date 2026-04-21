#!/usr/bin/env python3
"""Reconnect Android devices over ADB Wi-Fi and update device availability."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import logging_config

DEVICE_FILE = Path(__file__).with_name("devices.json")
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1
LOGGER = logging_config.get_logger("adb_reconnect")


def run_command(command: List[str]) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return its completed process object."""
    LOGGER.debug("Running command: %s", " ".join(command))
    return subprocess.run(command, capture_output=True, text=True)


def load_devices() -> List[Dict[str, Any]]:
    """Load device records from devices.json."""
    if not DEVICE_FILE.exists():
        LOGGER.info("Device file not found: %s", DEVICE_FILE)
        return []

    try:
        with DEVICE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        LOGGER.error("Failed to load device file: %s", DEVICE_FILE)
        return []

    if not isinstance(data, list):
        return []

    return [device for device in data if isinstance(device, dict)]


def save_devices(devices: List[Dict[str, Any]]) -> None:
    """Save device records to devices.json."""
    with DEVICE_FILE.open("w", encoding="utf-8") as file:
        json.dump(devices, file, indent=2)
    LOGGER.info("Saved %d device records", len(devices))


def get_online_targets() -> Set[str]:
    """Return ADB targets currently listed as online (state: device)."""
    result = run_command(["adb", "devices"])
    if result.returncode != 0:
        LOGGER.error("adb devices failed: %s", (result.stderr or "").strip())
        return set()

    targets: Set[str] = set()
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            targets.add(parts[0])

    return targets


def resolve_target(device: Dict[str, Any]) -> str:
    """Build adb connect target from device fields."""
    ip_address = device.get("ip") or device.get("ip_address")
    port = device.get("port")
    return f"{ip_address}:{port}" if ip_address and port else ""


def reconnect_device(target: str, retries: int) -> bool:
    """Try adb connect up to retries times, verifying online state after each attempt."""
    for attempt in range(1, retries + 1):
        connect_result = run_command(["adb", "connect", target])
        output = f"{connect_result.stdout}\n{connect_result.stderr}".lower()

        online_targets = get_online_targets()
        if target in online_targets or "already connected" in output or "connected to" in output:
            LOGGER.info("[%s] connected on attempt %d/%d", target, attempt, retries)
            return True

        LOGGER.error("[%s] attempt %d/%d failed", target, attempt, retries)
        if attempt < retries:
            time.sleep(RETRY_DELAY_SECONDS)

    return False


def main() -> None:
    logging_config.setup_logging()
    devices = load_devices()

    if not devices:
        print(f"No devices found in {DEVICE_FILE.name}")
        LOGGER.info("No devices found in %s", DEVICE_FILE.name)
        print("Summary:")
        print("- total devices: 0")
        print("- connected: 0")
        print("- failed: 0")
        return

    connected_count = 0

    for device in devices:
        target = resolve_target(device)
        if not target:
            device["status"] = "offline"
            print("[unknown] missing ip/port fields")
            LOGGER.error("Device missing ip/port fields")
            continue

        is_connected = reconnect_device(target, MAX_RETRIES)
        device["status"] = "available" if is_connected else "offline"

        if is_connected:
            connected_count += 1
            print(f"[{target}] status: available")
            LOGGER.info("[%s] status: available", target)
        else:
            print(f"[{target}] status: offline")
            LOGGER.error("[%s] status: offline", target)

    save_devices(devices)

    total = len(devices)
    failed = total - connected_count

    print("Summary:")
    print(f"- total devices: {total}")
    print(f"- connected: {connected_count}")
    print(f"- failed: {failed}")


if __name__ == "__main__":
    main()
