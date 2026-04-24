"""Thread-safe device registry backed by a JSON file."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from copy import deepcopy
from typing import Any, Dict, List, Set

import logging_config

DEVICE_FILE = os.path.join(os.path.dirname(__file__), "devices.json")
_ALLOWED_STATUSES = {"available", "busy", "offline"}
_LOCK = threading.RLock()
LOGGER = logging_config.get_logger("device_registry")


def _ensure_file_exists() -> None:
    """Create the backing JSON file if it does not exist."""
    if not os.path.exists(DEVICE_FILE):
        with open(DEVICE_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, indent=2)
        LOGGER.info("Created device registry file: %s", DEVICE_FILE)


def _load_devices() -> List[Dict[str, Any]]:
    """Load and return all devices from disk."""
    _ensure_file_exists()
    try:
        with open(DEVICE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        LOGGER.error("Failed to read registry file: %s", DEVICE_FILE)
        return []


def _save_devices(devices: List[Dict[str, Any]]) -> None:
    """Persist device list to disk."""
    with open(DEVICE_FILE, "w", encoding="utf-8") as file:
        json.dump(devices, file, indent=2)
    LOGGER.debug("Persisted %d devices to registry", len(devices))


def _validate_device(device_object: Dict[str, Any]) -> None:
    required_fields = {"device_id", "ip", "port", "status"}
    missing_fields = required_fields - set(device_object)
    if missing_fields:
        raise ValueError(f"Missing required fields: {sorted(missing_fields)}")

    if device_object["status"] not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid status '{device_object['status']}'. "
            f"Allowed: {sorted(_ALLOWED_STATUSES)}"
        )


def _parse_adb_devices_output(output: str) -> Set[str]:
    """Extract unique, online adb device ids from `adb devices` output."""
    active: Set[str] = set()
    for line in output.splitlines()[1:]:
        cleaned = line.strip()
        if not cleaned:
            continue
        parts = cleaned.split()
        if len(parts) < 2:
            continue
        device_id, state = parts[0].strip(), parts[1].strip()
        if state == "device":
            active.add(device_id)
    return active


def get_active_devices() -> Set[str]:
    """Return active adb device ids from `adb devices` (unique, online only)."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=False,
            timeout=15,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        LOGGER.error("Failed to execute adb devices: %s", error)
        return set()

    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        LOGGER.error("adb devices failed: %s", stderr)
        return set()

    devices = _parse_adb_devices_output(stdout)
    LOGGER.info("Detected active adb devices: %s", sorted(devices))
    return devices


def cleanup_registry(active_devices: Set[str] | None = None) -> List[Dict[str, Any]]:
    """Remove duplicate/stale entries and return cleaned registry."""
    active = active_devices if active_devices is not None else get_active_devices()
    with _LOCK:
        devices = _load_devices()
        deduped: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for device in devices:
            device_id = str(device.get("device_id", "")).strip()
            if not device_id or device_id in seen:
                continue
            if device_id not in active:
                continue
            normalized = deepcopy(device)
            normalized["device_id"] = device_id
            normalized["status"] = "available"
            deduped.append(normalized)
            seen.add(device_id)
        _save_devices(deduped)
        LOGGER.info("Registry cleanup complete. Retained %d active device(s)", len(deduped))
        return deepcopy(deduped)


def get_all_devices() -> List[Dict[str, Any]]:
    """Return all devices from registry."""
    with _LOCK:
        return deepcopy(_load_devices())


def add_device(device_object: Dict[str, Any]) -> Dict[str, Any]:
    """Add a new device to the registry."""
    _validate_device(device_object)

    with _LOCK:
        devices = _load_devices()
        device_id = device_object["device_id"]

        if any(device.get("device_id") == device_id for device in devices):
            raise ValueError(f"Device '{device_id}' already exists")

        devices.append(deepcopy(device_object))
        _save_devices(devices)
        LOGGER.info("Added device to registry: %s", device_id)

    return deepcopy(device_object)


def remove_device(device_id: str) -> bool:
    """Remove a device by id. Returns True if removed, else False."""
    with _LOCK:
        devices = _load_devices()
        filtered_devices = [d for d in devices if d.get("device_id") != device_id]

        if len(filtered_devices) == len(devices):
            LOGGER.error("Attempted to remove unknown device: %s", device_id)
            return False

        _save_devices(filtered_devices)
        LOGGER.info("Removed device from registry: %s", device_id)
        return True


def update_device_status(device_id: str, status: str) -> bool:
    """Update status for a device id. Returns True if updated, else False."""
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Allowed: {sorted(_ALLOWED_STATUSES)}")

    with _LOCK:
        devices = _load_devices()
        updated = False

        for device in devices:
            if device.get("device_id") == device_id:
                device["status"] = status
                updated = True
                break

        if updated:
            _save_devices(devices)
            LOGGER.info("Updated device status: %s -> %s", device_id, status)
        else:
            LOGGER.error("Device not found for status update: %s", device_id)

        return updated
