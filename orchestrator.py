#!/usr/bin/env python3
"""End-to-end orchestration for Android performance benchmarking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import adb_reconnect
import adb_wifi_setup
import device_registry
import install_apk_parallel
import logging_config
import performance_collector
import report_generator

LOGGER = logging_config.get_logger("orchestrator")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Android performance benchmark pipeline.")
    parser.add_argument("--apk", required=True, help="Path to APK file")
    parser.add_argument("--package", required=True, help="App package name")
    parser.add_argument("--activity", required=True, help="Launch activity name")
    parser.add_argument("--iterations", type=int, default=10, help="Iterations per start type")
    parser.add_argument("--max-threads", type=int, default=4, help="Parallel install workers")
    parser.add_argument("--timeout", type=int, default=90, help="Install/launch adb timeout")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    return parser.parse_args()


def stage_connect_devices() -> None:
    LOGGER.info("Step 1/6: Connect and refresh devices")
    try:
        usb_devices = adb_wifi_setup.get_connected_devices()
    except Exception as error:
        LOGGER.warning("USB detection failed: %s", error)
        usb_devices = []

    known_ids = {str(d.get("device_id", "")).strip() for d in device_registry.get_all_devices()}

    for device_id in usb_devices:
        if device_id in known_ids:
            continue
        if not adb_wifi_setup.enable_tcpip(device_id, adb_wifi_setup.ADB_PORT):
            continue
        ip_address = adb_wifi_setup.get_device_ip(device_id)
        if not ip_address:
            continue
        if not adb_wifi_setup.connect_wifi(device_id, ip_address, adb_wifi_setup.ADB_PORT):
            continue
        device_registry.add_device(
            {
                "device_id": device_id,
                "ip": ip_address,
                "port": adb_wifi_setup.ADB_PORT,
                "status": "available",
                "device_name": adb_wifi_setup.get_device_name(device_id),
            }
        )

    for device in device_registry.get_all_devices():
        target = adb_reconnect.resolve_target(device)
        device_id = str(device.get("device_id") or target or "unknown")
        if target and adb_reconnect.reconnect_device(target, adb_reconnect.MAX_RETRIES):
            device_registry.update_device_status(device_id, "available")
        else:
            device_registry.update_device_status(device_id, "offline")


def stage_install_apk(apk_path: str, package_name: str, activity_name: str, max_threads: int, timeout: int) -> List[install_apk_parallel.DeviceExecutionStatus]:
    LOGGER.info("Step 2/6: Install APK")
    devices = install_apk_parallel.get_available_devices()
    if not devices:
        return []

    return install_apk_parallel.run_parallel(
        devices=devices,
        max_parallel_threads=max(1, max_threads),
        apk_path=apk_path,
        package_name=package_name,
        activity_name=activity_name,
        timeout_seconds=max(1, timeout),
    )


def stage_run_benchmarks(
    statuses: List[install_apk_parallel.DeviceExecutionStatus],
    package_name: str,
    activity_name: str,
    iterations: int,
) -> Dict[str, Any]:
    LOGGER.info("Step 3/6: Run runtime + startup benchmarks")
    results: Dict[str, Any] = {}

    for status in statuses:
        device_id = status.device_id
        if status.status != "success":
            results[device_id] = {
                "runtime_metrics": {"cpu": "N/A", "memory": "N/A", "fps": "N/A"},
                "startup_metrics": {
                    "cold": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                    "warm": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                    "hot": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                },
                "runtime_details": {"launch_time": {}, "raw": {}},
                "error": status.error or "install/launch failed",
            }
            continue

        try:
            results[device_id] = performance_collector.run_full_benchmark(
                status.target,
                package_name,
                activity_name,
                iterations=iterations,
            )
        except Exception as error:
            results[device_id] = {
                "runtime_metrics": {"cpu": "N/A", "memory": "N/A", "fps": "N/A"},
                "startup_metrics": {
                    "cold": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                    "warm": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                    "hot": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
                },
                "runtime_details": {"launch_time": {}, "raw": {}},
                "error": str(error),
            }

    return results


def stage_collect_and_save(results: Dict[str, Any]) -> Path:
    LOGGER.info("Step 4/6: Aggregate and save results")
    performance_collector.FINAL_RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    performance_collector.OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return performance_collector.FINAL_RESULTS_FILE.resolve()


def stage_generate_report(results: Dict[str, Any]) -> Path:
    LOGGER.info("Step 5/6: Generate HTML report")
    report_generator.generate_report_from_results(results, report_generator.OUTPUT_FILE)
    return report_generator.OUTPUT_FILE.resolve()


def print_summary(total_devices: int, passed_devices: int, failed_devices: int, result_file: Path, report_file: Path) -> None:
    print("\nFinal Summary")
    print(f"- devices tested: {total_devices}")
    print(f"- success count: {passed_devices}")
    print(f"- failed count: {failed_devices}")
    print(f"- results file: {result_file}")
    print(f"- report file: {report_file}")


def main() -> None:
    args = parse_args()
    logging_config.setup_logging(args.debug)
    performance_collector.set_debug(args.debug)

    stage_connect_devices()
    statuses = stage_install_apk(args.apk, args.package, args.activity, args.max_threads, args.timeout)
    results = stage_run_benchmarks(statuses, args.package, args.activity, args.iterations)
    result_file = stage_collect_and_save(results)
    report_file = stage_generate_report(results)

    total = len(statuses)
    passed = sum(1 for s in statuses if s.status == "success")
    failed = total - passed

    LOGGER.info("Step 6/6: Completed")
    print_summary(total, passed, failed, result_file, report_file)


if __name__ == "__main__":
    main()
