#!/usr/bin/env python3
"""End-to-end orchestration for Android performance benchmarking."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import adb_client
import adb_wifi_setup
import device_registry
import install_apk_parallel
import logging_config
import performance_collector
import report_generator

LOGGER = logging_config.get_logger("orchestrator")


def _failed_result(error: str) -> Dict[str, Any]:
    return {
        "device_details": {
            "model": "N/A",
            "manufacturer": "N/A",
            "brand": "N/A",
            "device": "N/A",
            "android_version": "N/A",
            "sdk_int": "N/A",
            "build_fingerprint": "N/A",
            "kernel_version": "N/A",
            "cpu": "N/A",
            "abi_list": "N/A",
            "total_memory_mb": "N/A",
        },
        "runtime_metrics": {"cpu": "N/A", "memory": "N/A", "fps": "N/A", "gc_count": "N/A"},
        "startup_metrics": {
            "cold": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
            "warm": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
            "hot": {"values": [], "avg": "N/A", "min": "N/A", "max": "N/A"},
        },
        "runtime_details": {"launch_time": {}, "raw": {}},
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Android performance benchmark pipeline.")
    parser.add_argument("--apk", required=True, help="Path to APK file")
    parser.add_argument("--package", required=True, help="App package name")
    parser.add_argument("--activity", required=True, help="Launch activity name")
    parser.add_argument("--iterations", type=int, default=10, help="Iterations per start type")
    parser.add_argument("--max-threads", type=int, default=4, help="Parallel install workers")
    parser.add_argument("--timeout", type=int, default=90, help="Install/launch adb timeout")
    parser.add_argument("--runtime-window", type=int, default=60, help="CPU sampling window seconds")
    parser.add_argument("--sample-interval", type=int, default=5, help="CPU sampling interval seconds")
    parser.add_argument("--adb-retries", type=int, default=1, help="Retries for adb commands")
    parser.add_argument("--quick", action="store_true", help="Use quicker benchmark defaults")
    parser.add_argument("--output-dir", default="performance_runs", help="Base output directory for run artifacts")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.iterations <= 0:
        raise ValueError("--iterations must be greater than 0")
    if args.max_threads <= 0:
        raise ValueError("--max-threads must be greater than 0")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")

    apk_path = Path(args.apk)
    if not apk_path.exists() or not apk_path.is_file():
        raise FileNotFoundError(f"APK does not exist or is not a file: {apk_path}")

    if args.quick:
        args.runtime_window = min(args.runtime_window, 20)
        args.sample_interval = min(args.sample_interval, 2)
        args.iterations = min(args.iterations, 3)

    return args


def _detect_valid_adb_devices() -> List[str]:
    active_devices = sorted(device_registry.get_active_devices())
    deduped_devices = _dedupe_physical_devices(active_devices)
    LOGGER.info("Detected active devices (%d): %s", len(active_devices), active_devices)
    if len(deduped_devices) != len(active_devices):
        LOGGER.info(
            "De-duplicated active devices to unique physical devices (%d): %s",
            len(deduped_devices),
            deduped_devices,
        )
    return deduped_devices


def _physical_serial_for_target(target: str) -> str:
    for prop in ("ro.serialno", "ro.boot.serialno"):
        response = adb_client.run_adb_command(
            ["adb", "-s", target, "shell", "getprop", prop],
            timeout=10,
            retries=0,
        )
        serial = (response.output or "").strip()
        if response.success and serial and serial.lower() not in {"unknown", "n/a"}:
            return serial
    return target


def _target_priority(target: str) -> int:
    if target.startswith("adb-"):
        return 3
    if ":" in target:
        return 0
    return 1


def _dedupe_physical_devices(active_devices: List[str]) -> List[str]:
    by_serial: Dict[str, List[str]] = {}
    for device in active_devices:
        serial = _physical_serial_for_target(device)
        by_serial.setdefault(serial, []).append(device)

    deduped: List[str] = []
    for serial, aliases in sorted(by_serial.items()):
        selected = sorted(aliases, key=lambda item: (_target_priority(item), item))[0]
        if len(aliases) > 1:
            LOGGER.warning(
                "Found duplicate adb targets for physical device serial %s: %s. Using %s",
                serial,
                sorted(aliases),
                selected,
            )
        deduped.append(selected)
    return sorted(deduped)


def _sync_registry_with_active_devices(active_devices: List[str]) -> None:
    device_registry.cleanup_registry(set(active_devices))
    existing = {str(d.get("device_id", "")).strip() for d in device_registry.get_all_devices()}
    for device_id in active_devices:
        if device_id in existing:
            device_registry.update_device_status(device_id, "available")
            continue
        device_registry.add_device(
            {
                "device_id": device_id,
                "ip": "",
                "port": adb_wifi_setup.ADB_PORT,
                "status": "available",
                "device_name": adb_wifi_setup.get_device_name(device_id),
            }
        )
    LOGGER.info("Registry synced for %d device(s)", len(active_devices))


def stage_connect_devices() -> List[str]:
    LOGGER.info("Step 1/6: Connect and refresh devices")
    active_devices = _detect_valid_adb_devices()
    if not active_devices:
        LOGGER.warning("No active adb devices detected")
        device_registry.cleanup_registry(set())
        return []

    _sync_registry_with_active_devices(active_devices)
    return active_devices


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
    max_threads: int,
) -> Dict[str, Any]:
    LOGGER.info("Step 3/6: Run runtime + startup benchmarks")
    results: Dict[str, Any] = {}

    successful: List[install_apk_parallel.DeviceExecutionStatus] = []
    for status in statuses:
        device_id = status.device_id
        if status.status != "success":
            results[device_id] = _failed_result(status.error or "install/launch failed")
            continue
        successful.append(status)

    if not successful:
        return results

    workers = max(1, min(max_threads, len(successful)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                performance_collector.run_full_benchmark,
                status.target,
                package_name,
                activity_name,
                iterations,
            ): status.device_id
            for status in successful
        }
        for future in as_completed(future_map):
            device_id = future_map[future]
            try:
                results[device_id] = future.result()
            except Exception as error:
                results[device_id] = _failed_result(str(error))

    return results


def stage_collect_and_save(results: Dict[str, Any]) -> Path:
    LOGGER.info("Step 4/6: Aggregate and save results")
    performance_collector.FINAL_RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    performance_collector.OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return performance_collector.FINAL_RESULTS_FILE.resolve()


def stage_generate_report(results: Dict[str, Any]) -> Path:
    LOGGER.info("Step 5/6: Generate HTML report")
    report_file = performance_collector.FINAL_RESULTS_FILE.parent / "report.html"
    report_generator.generate_report_from_results(results, report_file)
    return report_file.resolve()


def print_summary(total_devices: int, passed_devices: int, failed_devices: int, result_file: Path, report_file: Path) -> None:
    print("\nFinal Summary")
    print(f"- devices tested: {total_devices}")
    print(f"- success count: {passed_devices}")
    print(f"- failed count: {failed_devices}")
    print(f"- results file: {result_file}")
    print(f"- report file: {report_file}")


def main() -> None:
    args = parse_args()
    run_stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / f"run_{run_stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = run_dir / "logs"
    logging_config.configure_logs_dir(logs_dir)
    install_apk_parallel.set_logs_dir(logs_dir)
    performance_collector.set_output_directory(run_dir)

    logging_config.setup_logging(args.debug)
    performance_collector.set_debug(args.debug)
    performance_collector.set_runtime_collection(args.runtime_window, args.sample_interval)
    performance_collector.set_adb_retries(args.adb_retries)

    active_devices = stage_connect_devices()
    if len(active_devices) > 1:
        LOGGER.warning("More than one active device detected (%d). All will be processed.", len(active_devices))
    if len(active_devices) == 1:
        LOGGER.info("Single device connected; strict single-device processing enabled.")

    statuses = stage_install_apk(args.apk, args.package, args.activity, args.max_threads, args.timeout)
    if len(active_devices) == 1 and len(statuses) > 1:
        LOGGER.warning(
            "Mismatch detected: one active device but %d status entries. Enforcing strict mapping.",
            len(statuses),
        )
        statuses = [status for status in statuses if status.device_id == active_devices[0]]

    results = stage_run_benchmarks(statuses, args.package, args.activity, args.iterations, args.max_threads)
    result_file = stage_collect_and_save(results)
    report_file = stage_generate_report(results)

    total = len(statuses)
    passed = sum(1 for s in statuses if s.status == "success")
    failed = total - passed

    LOGGER.info("Step 6/6: Completed")
    print_summary(total, passed, failed, result_file, report_file)


if __name__ == "__main__":
    main()
