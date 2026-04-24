#!/usr/bin/env python3
"""Enable ADB over Wi-Fi for all USB-connected Android devices and save metadata."""

import json
import re
import subprocess
from typing import Dict, List, Optional

import logging_config

ADB_PORT = 5555
OUTPUT_FILE = "devices.json"
LOGGER = logging_config.get_logger("adb_wifi_setup")


def run_command(command: List[str], device_id: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a command and return the completed process object."""
    prefix = f"[{device_id}] " if device_id else ""
    print(f"{prefix}Running: {' '.join(command)}")
    LOGGER.debug("%sRunning command: %s", prefix, " ".join(command))
    return subprocess.run(command, capture_output=True, text=True)


def get_connected_devices() -> List[str]:
    """Return a list of USB-connected and online device ids from adb devices output."""
    result = run_command(["adb", "devices", "-l"])
    if result.returncode != 0:
        LOGGER.error("Failed to list adb devices: %s", result.stderr.strip())
        raise RuntimeError(f"Failed to list adb devices: {result.stderr.strip()}")

    devices: List[str] = []
    for line in result.stdout.splitlines()[1:]:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        device_id, state = parts[0], parts[1]
        # Only include physical USB devices. This skips mDNS/TLS entries like:
        # adb-<serial>-<token>._adb-tls-connect._tcp and network targets like <ip>:5555.
        # Some adb versions do not print a `usb:` marker in `adb devices -l` output,
        # so we also treat plain serials with a transport id as USB candidates.
        has_usb_marker = " usb:" in f" {line} "
        has_transport_id = "transport_id:" in line
        is_wireless_serial = ".adb-tls-connect._tcp" in device_id or ":" in device_id
        is_emulator = device_id.startswith("emulator-")
        is_usb_transport = (has_usb_marker or has_transport_id) and not is_wireless_serial and not is_emulator

        if state == "device" and is_usb_transport:
            devices.append(device_id)
        elif state == "device":
            print(f"[{device_id}] Skipping: not a USB transport")
            LOGGER.info("[%s] Skipping non-USB transport entry", device_id)
        elif state == "offline":
            print(f"[{device_id}] Skipping: device is offline")
            LOGGER.error("[%s] Skipping offline device", device_id)
        else:
            print(f"[{device_id}] Skipping: unsupported state '{state}'")
            LOGGER.error("[%s] Unsupported state '%s'", device_id, state)

    return devices


def enable_tcpip(device_id: str, port: int) -> bool:
    """Enable ADB over TCP/IP for a specific device."""
    result = run_command(["adb", "-s", device_id, "tcpip", str(port)], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error enabling tcpip: {result.stderr.strip()}")
        LOGGER.error("[%s] Error enabling tcpip: %s", device_id, result.stderr.strip())
        return False

    print(f"[{device_id}] tcpip enabled: {result.stdout.strip()}")
    LOGGER.info("[%s] tcpip enabled", device_id)
    return True


def get_device_ip(device_id: str) -> Optional[str]:
    """Get Wi-Fi IPv4 address from Android device shell."""
    commands = [
        ["adb", "-s", device_id, "shell", "getprop", "dhcp.wlan0.ipaddress"],
        ["adb", "-s", device_id, "shell", "ip", "-f", "inet", "addr", "show"],
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
            if candidate.startswith(("127.", "0.")):
                continue
            octets = [int(part) for part in candidate.split(".")]
            if all(0 <= octet <= 255 for octet in octets):
                print(f"[{device_id}] Found IP address: {candidate}")
                LOGGER.info("[%s] Found IP address: %s", device_id, candidate)
                return candidate

    print(f"[{device_id}] Error: no IP address found (is Wi-Fi connected on the device?)")
    LOGGER.error("[%s] No IP address found. Ensure Wi-Fi is enabled and connected.", device_id)
    return None


def connect_wifi(device_id: str, ip_address: str, port: int) -> bool:
    """Connect to device via adb over Wi-Fi."""
    target = f"{ip_address}:{port}"
    result = run_command(["adb", "connect", target], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error connecting to {target}: {result.stderr.strip()}")
        LOGGER.error("[%s] Error connecting to %s: %s", device_id, target, result.stderr.strip())
        return False

    output = result.stdout.strip().lower()
    if "connected" in output or "already connected" in output:
        print(f"[{device_id}] Wi-Fi connect successful: {result.stdout.strip()}")
        LOGGER.info("[%s] Wi-Fi connect successful: %s", device_id, target)
        return True

    print(f"[{device_id}] Unexpected connect output: {result.stdout.strip()}")
    LOGGER.error("[%s] Unexpected connect output: %s", device_id, result.stdout.strip())
    return False


def get_device_name(device_id: str) -> str:
    """Fetch device model name."""
    result = run_command(["adb", "-s", device_id, "shell", "getprop", "ro.product.model"], device_id)
    if result.returncode != 0:
        print(f"[{device_id}] Error getting device name: {result.stderr.strip()}")
        LOGGER.error("[%s] Error getting device name: %s", device_id, result.stderr.strip())
        return "unknown"

    name = result.stdout.strip() or "unknown"
    print(f"[{device_id}] Device name: {name}")
    LOGGER.info("[%s] Device name: %s", device_id, name)
    return name


def main() -> None:
    logging_config.setup_logging()
    print("Starting ADB USB -> Wi-Fi setup")
    LOGGER.info("Starting ADB USB -> Wi-Fi setup")

    try:
        devices = get_connected_devices()
    except RuntimeError as error:
        print(f"Fatal error: {error}")
        LOGGER.error("Fatal error: %s", error)
        return

    if not devices:
        print("No USB-connected online Android devices found")
        LOGGER.info("No USB-connected online Android devices found")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, indent=2)
        print(f"Saved empty output to {OUTPUT_FILE}")
        LOGGER.info("Saved empty output to %s", OUTPUT_FILE)
        return

    output: List[Dict[str, object]] = []

    for device_id in devices:
        print(f"\nProcessing device: {device_id}")
        LOGGER.info("Processing device: %s", device_id)

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
    LOGGER.info("Completed. Saved %d device record(s) to %s", len(output), OUTPUT_FILE)


if __name__ == "__main__":
    main()
