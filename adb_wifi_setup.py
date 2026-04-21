#!/usr/bin/env python3
"""Enable ADB over Wi-Fi for all USB-connected Android devices and save metadata."""

import json
import re
import subprocess
from typing import Dict, List, Optional

ADB_PORT = 5555
OUTPUT_FILE = "devices.json"


def run_command(command: List[str], device_id: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a command and return the completed process object."""
    prefix = f"[{device_id}] " if device_id else ""
    print(f"{prefix}Running: {' '.join(command)}")
    return subprocess.run(command, capture_output=True, text=True)


def get_connected_devices() -> List[str]:
    """Return a list of USB-connected and online device ids from adb devices output."""
    result = run_command(["adb", "devices"])
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list adb devices: {result.stderr.strip()}")

    devices: List[str] = []
    for line in result.stdout.splitlines()[1:]:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        device_id, state = parts[0], parts[1]
        if state == "device":
            devices.append(device_id)
        elif state == "offline":
            print(f"[{device_id}] Skipping: device is offline")
        else:
            print(f"[{device_id}] Skipping: unsupported state '{state}'")

    return devices


def enable_tcpip(device_id: str, port: int) -> bool:
    """Enable ADB over TCP/IP for a specific device."""
    result = run_command(["adb", "-s", device_id, "tcpip", str(port)], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error enabling tcpip: {result.stderr.strip()}")
        return False

    print(f"[{device_id}] tcpip enabled: {result.stdout.strip()}")
    return True


def get_device_ip(device_id: str) -> Optional[str]:
    """Get Wi-Fi IPv4 address from Android device shell."""
    commands = [
        ["adb", "-s", device_id, "shell", "ip", "route"],
        ["adb", "-s", device_id, "shell", "ip", "addr", "show", "wlan0"],
        ["adb", "-s", device_id, "shell", "ifconfig", "wlan0"],
    ]

    ip_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    for cmd in commands:
        result = run_command(cmd, device_id)
        if result.returncode != 0:
            continue

        candidates = ip_pattern.findall(result.stdout)
        for candidate in candidates:
            if candidate.startswith("127."):
                continue
            octets = [int(part) for part in candidate.split(".")]
            if all(0 <= octet <= 255 for octet in octets):
                print(f"[{device_id}] Found IP address: {candidate}")
                return candidate

    print(f"[{device_id}] Error: no IP address found")
    return None


def connect_wifi(device_id: str, ip_address: str, port: int) -> bool:
    """Connect to device via adb over Wi-Fi."""
    target = f"{ip_address}:{port}"
    result = run_command(["adb", "connect", target], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error connecting to {target}: {result.stderr.strip()}")
        return False

    output = result.stdout.strip().lower()
    if "connected" in output or "already connected" in output:
        print(f"[{device_id}] Wi-Fi connect successful: {result.stdout.strip()}")
        return True

    print(f"[{device_id}] Unexpected connect output: {result.stdout.strip()}")
    return False


def get_device_name(device_id: str) -> str:
    """Fetch device model name."""
    result = run_command(["adb", "-s", device_id, "shell", "getprop", "ro.product.model"], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error getting device name: {result.stderr.strip()}")
        return "unknown"

    name = result.stdout.strip() or "unknown"
    print(f"[{device_id}] Device name: {name}")
    return name


def main() -> None:
    print("Starting ADB USB -> Wi-Fi setup")

    try:
        devices = get_connected_devices()
    except RuntimeError as error:
        print(f"Fatal error: {error}")
        return

    if not devices:
        print("No USB-connected online Android devices found")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, indent=2)
        print(f"Saved empty output to {OUTPUT_FILE}")
        return

    output: List[Dict[str, object]] = []

    for device_id in devices:
        print(f"\nProcessing device: {device_id}")

        if not enable_tcpip(device_id, ADB_PORT):
            continue

        ip_address = get_device_ip(device_id)
        if not ip_address:
            continue

        if not connect_wifi(device_id, ip_address, ADB_PORT):
            continue

        device_name = get_device_name(device_id)
        output.append(
            {
                "device_id": device_id,
                "ip_address": ip_address,
                "port": ADB_PORT,
                "device_name": device_name,
            }
        )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)

    print(f"\nCompleted. Saved {len(output)} device record(s) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
