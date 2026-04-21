#!/usr/bin/env python3
"""End-to-end orchestration for Android performance test workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import adb_reconnect
import adb_wifi_setup
import device_registry
import install_apk_parallel
import logging_config
import performance_collector
import report_generator

LOGGER = logging_config.get_logger("orchestrator")


def setup_logging(verbose: bool) -> None:
    """Configure logging for orchestration stages."""
    logging_config.setup_logging(verbose)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full Android performance pipeline.")
    parser.add_argument("--apk", required=True, help="Path to APK file")
    parser.add_argument("--package", required=True, help="Android package name")
    parser.add_argument("--activity", required=True, help="Android activity name")
    parser.add_argument("--max-threads", type=int, default=4, help="Parallel install workers")
    parser.add_argument("--timeout", type=int, default=90, help="ADB command timeout per device")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def stage_register_new_devices() -> int:
    """Step 1: register newly connected USB devices into the registry."""
    LOGGER.info("Step 1/6: Register newly connected devices")
    try:
        connected_usb = adb_wifi_setup.get_connected_devices()
    except Exception as error:  # graceful stage failure
        LOGGER.exception("Device discovery failed: %s", error)
        return 0

    existing_ids = set()
    existing_targets = set()
    for device in device_registry.get_all_devices():
        existing_id = str(device.get("device_id") or "").strip()
        if existing_id:
            existing_ids.add(existing_id)
        existing_target = adb_reconnect.resolve_target(device)
        if existing_target:
            existing_targets.add(existing_target)
    registered_count = 0

    for device_id in connected_usb:
        if device_id in existing_ids:
            LOGGER.debug("Device already registered: %s", device_id)
            continue

        LOGGER.info("Registering new device: %s", device_id)
        if not adb_wifi_setup.enable_tcpip(device_id, adb_wifi_setup.ADB_PORT):
            LOGGER.warning("Skipping %s; could not enable tcpip", device_id)
            continue

        ip_address = adb_wifi_setup.get_device_ip(device_id)
        if not ip_address:
            LOGGER.warning("Skipping %s; no WiFi IP found", device_id)
            continue

        if not adb_wifi_setup.connect_wifi(device_id, ip_address, adb_wifi_setup.ADB_PORT):
            LOGGER.warning("Skipping %s; adb wifi connect failed", device_id)
            continue

        target = f"{ip_address}:{adb_wifi_setup.ADB_PORT}"
        if target in existing_targets:
            LOGGER.info("Skipping %s; target already registered as %s", device_id, target)
            continue

        device_name = adb_wifi_setup.get_device_name(device_id)
        try:
            device_registry.add_device(
                {
                    "device_id": device_id,
                    "ip": ip_address,
                    "port": adb_wifi_setup.ADB_PORT,
                    "status": "available",
                    "device_name": device_name,
                }
            )
            registered_count += 1
            existing_ids.add(device_id)
            existing_targets.add(target)
        except ValueError as error:
            LOGGER.warning("Could not add %s to registry: %s", device_id, error)

    LOGGER.info("New devices registered: %s", registered_count)
    return registered_count


def stage_reconnect_devices() -> None:
    """Step 2: reconnect all devices and refresh status."""
    LOGGER.info("Step 2/6: Reconnect all devices over WiFi")
    devices = device_registry.get_all_devices()
    if not devices:
        LOGGER.warning("No devices in registry to reconnect")
        return

    for device in devices:
        target = adb_reconnect.resolve_target(device)
        device_id = str(device.get("device_id") or target or "unknown")
        if not target:
            LOGGER.warning("%s missing IP/port; setting offline", device_id)
            try:
                device_registry.update_device_status(device_id, "offline")
            except ValueError:
                LOGGER.debug("Failed to mark %s offline", device_id)
            continue

        connected = adb_reconnect.reconnect_device(target, adb_reconnect.MAX_RETRIES)
        new_status = "available" if connected else "offline"
        try:
            device_registry.update_device_status(device_id, new_status)
            LOGGER.info("%s -> %s", device_id, new_status)
        except ValueError as error:
            LOGGER.warning("Could not update status for %s: %s", device_id, error)


def stage_filter_available_devices() -> List[Dict[str, object]]:
    """Step 3: get only available devices."""
    LOGGER.info("Step 3/6: Filter available devices")
    available_devices = install_apk_parallel.get_available_devices()
    LOGGER.info("Available devices: %d", len(available_devices))
    return available_devices


def stage_install_and_launch(
    available_devices: List[Dict[str, object]],
    apk_path: str,
    package_name: str,
    activity_name: str,
    max_threads: int,
    timeout_seconds: int,
) -> List[install_apk_parallel.DeviceExecutionStatus]:
    """Step 4: install and launch app in parallel."""
    LOGGER.info("Step 4/6: Install APK and launch app in parallel")
    if not available_devices:
        LOGGER.warning("No available devices for install stage")
        return []

    install_apk_parallel.setup_logging(verbose=False)
    return install_apk_parallel.run_parallel(
        devices=available_devices,
        max_parallel_threads=max_threads,
        apk_path=apk_path,
        package_name=package_name,
        activity_name=activity_name,
        timeout_seconds=timeout_seconds,
    )


def stage_collect_metrics(
    statuses: List[install_apk_parallel.DeviceExecutionStatus],
    package_name: str,
    activity_name: str,
) -> Dict[str, Dict[str, object]]:
    """Step 5: collect performance metrics per successfully launched device."""
    LOGGER.info("Step 5/6: Collect performance metrics")
    results: Dict[str, Dict[str, object]] = {}

    for status in statuses:
        device_key = status.device_id
        if status.status != "success":
            results[device_key] = {
                "cpu": None,
                "memory": None,
                "launch_time": None,
                "fps": None,
                "error": status.error or f"install/launch {status.status}",
            }
            continue

        try:
            metrics = performance_collector.collect_for_device(
                status.target,
                package_name,
                activity_name,
            )
            results[device_key] = metrics
        except RuntimeError as error:
            LOGGER.warning("Metrics collection failed for %s: %s", device_key, error)
            results[device_key] = {
                "cpu": None,
                "memory": None,
                "launch_time": None,
                "fps": None,
                "error": str(error),
            }

    performance_collector.OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    LOGGER.info("Metrics saved: %s", performance_collector.OUTPUT_FILE)
    return results


def stage_generate_report() -> Path:
    """Step 6: generate HTML report from collected metrics."""
    LOGGER.info("Step 6/6: Generate HTML report")
    report_generator.main()
    return report_generator.OUTPUT_FILE.resolve()


def print_summary(total_devices: int, success_count: int, failed_count: int, report_location: Path) -> None:
    """Print final run summary in requested format."""
    print("\nFinal Summary")
    print(f"- devices tested: {total_devices}")
    print(f"- success count: {success_count}")
    print(f"- failed count: {failed_count}")
    print(f"- report location: {report_location}")


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    stage_register_new_devices()
    stage_reconnect_devices()
    available_devices = stage_filter_available_devices()

    statuses = stage_install_and_launch(
        available_devices=available_devices,
        apk_path=args.apk,
        package_name=args.package,
        activity_name=args.activity,
        max_threads=max(1, args.max_threads),
        timeout_seconds=max(1, args.timeout),
    )

    stage_collect_metrics(statuses, args.package, args.activity)

    report_location = Path("report.html").resolve()
    try:
        report_location = stage_generate_report()
    except Exception as error:  # graceful report-stage failure
        LOGGER.exception("Report generation failed: %s", error)

    total_devices = len(statuses)
    success_count = sum(1 for item in statuses if item.status == "success")
    failed_count = total_devices - success_count

    print_summary(total_devices, success_count, failed_count, report_location)


if __name__ == "__main__":
    main()
