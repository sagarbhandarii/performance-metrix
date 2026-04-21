#!/usr/bin/env python3
"""Install an APK on all available devices in parallel and launch the app."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional

import device_registry
import logging_config


@dataclass
class DeviceExecutionStatus:
    """Execution status for a single device."""

    device_id: str
    target: str
    install_success: bool
    launch_success: bool
    status: str
    error: Optional[str] = None


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


def get_available_devices() -> List[Dict[str, object]]:
    """Return only devices currently marked as available."""
    devices = device_registry.get_all_devices()
    return [device for device in devices if device.get("status") == "available"]


def run_adb_command(
    command: List[str],
    timeout_seconds: int,
    logger: logging.Logger,
) -> subprocess.CompletedProcess[str]:
    """Run adb command and raise descriptive errors for common failure modes."""
    logger.debug("Running command: %s", " ".join(command))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"timeout after {timeout_seconds}s") from error

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    merged = f"{stdout}\n{stderr}".lower()

    if "device offline" in merged or "device not found" in merged:
        raise RuntimeError("device disconnected")

    if result.returncode != 0:
        failure_text = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(failure_text)

    return result


def install_and_launch(
    device: Dict[str, object],
    apk_path: str,
    package_name: str,
    activity_name: str,
    timeout_seconds: int,
) -> DeviceExecutionStatus:
    """Install APK and launch app on a single device."""
    target = build_target(device)
    device_id = str(device.get("device_id", target))

    logger = logging.getLogger(f"device.{device_id}")

    if not target:
        logger.error("Cannot resolve adb target")
        return DeviceExecutionStatus(
            device_id=device_id,
            target=target,
            install_success=False,
            launch_success=False,
            status="failed",
            error="missing adb target",
        )

    install_success = False
    try:
        logger.info("Installing APK: %s", apk_path)
        run_adb_command(["adb", "-s", target, "install", "-r", apk_path], timeout_seconds, logger)
        install_success = True

        component = f"{package_name}/{activity_name}"
        logger.info("Launching app: %s", component)
        run_adb_command(
            ["adb", "-s", target, "shell", "am", "start", "-n", component],
            timeout_seconds,
            logger,
        )

        logger.info("Completed successfully")
        return DeviceExecutionStatus(
            device_id=device_id,
            target=target,
            install_success=install_success,
            launch_success=True,
            status="success",
        )
    except RuntimeError as error:
        message = str(error)
        logger.error("Failure: %s", message)

        disconnected = "disconnected" in message
        return DeviceExecutionStatus(
            device_id=device_id,
            target=target,
            install_success=install_success,
            launch_success=False,
            status="disconnected" if disconnected else "failed",
            error=message,
        )


def setup_logging(verbose: bool) -> None:
    """Configure thread-safe console logging."""
    logging_config.setup_logging(verbose)


def run_parallel(
    devices: Iterable[Dict[str, object]],
    max_parallel_threads: int,
    apk_path: str,
    package_name: str,
    activity_name: str,
    timeout_seconds: int,
) -> List[DeviceExecutionStatus]:
    """Execute install and launch workflow on devices with bounded thread pool."""
    statuses: List[DeviceExecutionStatus] = []
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_parallel_threads) as executor:
        future_to_device = {
            executor.submit(
                install_and_launch,
                device,
                apk_path,
                package_name,
                activity_name,
                timeout_seconds,
            ): device
            for device in devices
        }

        for future in as_completed(future_to_device):
            status = future.result()
            with lock:
                statuses.append(status)

    return sorted(statuses, key=lambda item: item.device_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install APK on all available devices in parallel and launch app.",
    )
    parser.add_argument("--apk", required=True, help="Path to APK file to install")
    parser.add_argument("--package", required=True, help="Android package name")
    parser.add_argument("--activity", required=True, help="Launch activity name")
    parser.add_argument(
        "--max-threads",
        type=int,
        default=4,
        help="Maximum parallel device operations",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Timeout in seconds for each adb command",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    if args.max_threads <= 0:
        raise ValueError("--max-threads must be greater than 0")

    available_devices = get_available_devices()
    if not available_devices:
        print("[]")
        return

    statuses = run_parallel(
        devices=available_devices,
        max_parallel_threads=args.max_threads,
        apk_path=args.apk,
        package_name=args.package,
        activity_name=args.activity,
        timeout_seconds=args.timeout,
    )

    print(json.dumps([asdict(status) for status in statuses], indent=2))


if __name__ == "__main__":
    main()
