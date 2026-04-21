"""Thread-safe device registry backed by a JSON file."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from typing import Any, Dict, List

DEVICE_FILE = os.path.join(os.path.dirname(__file__), "devices.json")
_ALLOWED_STATUSES = {"available", "busy", "offline"}
_LOCK = threading.RLock()


def _ensure_file_exists() -> None:
    """Create the backing JSON file if it does not exist."""
    if not os.path.exists(DEVICE_FILE):
        with open(DEVICE_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, indent=2)


def _load_devices() -> List[Dict[str, Any]]:
    """Load and return all devices from disk."""
    _ensure_file_exists()
    try:
        with open(DEVICE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_devices(devices: List[Dict[str, Any]]) -> None:
    """Persist device list to disk."""
    with open(DEVICE_FILE, "w", encoding="utf-8") as file:
        json.dump(devices, file, indent=2)


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

    return deepcopy(device_object)


def remove_device(device_id: str) -> bool:
    """Remove a device by id. Returns True if removed, else False."""
    with _LOCK:
        devices = _load_devices()
        filtered_devices = [d for d in devices if d.get("device_id") != device_id]

        if len(filtered_devices) == len(devices):
            return False

        _save_devices(filtered_devices)
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

        return updated
