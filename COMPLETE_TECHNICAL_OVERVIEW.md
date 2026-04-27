# Complete Technical Overview: performance-metrix

## 1) Project Overview

### Purpose
`performance-metrix` is a Python-based Android device-farm benchmarking toolkit that automates:
1. Device discovery/registration (USB to Wi-Fi ADB),
2. Device reconnect and availability tracking,
3. Parallel APK install + app launch,
4. Runtime and startup performance metric collection,
5. HTML dashboard report generation.

The orchestration entrypoint is `orchestrator.py`, which runs this pipeline end-to-end and writes per-run artifacts into timestamped directories.

### Problem it solves
In multi-device Android QA/performance workflows, teams often run ad hoc ADB commands manually, producing inconsistent measurements and fragmented logs. This project standardizes the full flow with:
- deterministic, repeatable command sequencing,
- per-device and per-run logs,
- normalized output schemas (`final_results.json` / `performance_results.json`),
- consolidated HTML reporting for runtime + startup behavior.

---

## 2) Architecture

### High-level architecture style
This is a **script-oriented modular pipeline architecture** (not classic MVC/MVVM/Clean). Each module owns a discrete operational concern and the orchestrator composes them.

- **Coordination layer:** `orchestrator.py`
- **Execution/integration layer:** `adb_client.py`, `adb_wifi_setup.py`, `adb_reconnect.py`, `install_apk_parallel.py`
- **State layer:** `device_registry.py` (JSON-backed registry)
- **Domain metrics layer:** `performance_collector.py`
- **Presentation/reporting layer:** `report_generator.py`
- **Cross-cutting concern:** `logging_config.py`

### Folder structure explanation

- **Root scripts**
  - `orchestrator.py`: full pipeline coordinator.
  - `adb_wifi_setup.py`: USB → Wi-Fi registration and `devices.json` generation.
  - `adb_reconnect.py`: reconnect devices listed in registry.
  - `install_apk_parallel.py`: parallel install + launch executor.
  - `performance_collector.py`: runtime/startup metric extraction and validation.
  - `report_generator.py`: single-page HTML report builder.
  - `adb_client.py`: retryable ADB command abstraction.
  - `device_registry.py`: thread-safe registry CRUD + active device reconciliation.
  - `logging_config.py`: global logging bootstrap.
- **Static assets**
  - `static/chart.min.js`: local Chart.js bundle (downloaded/fallback).
- **Tests**
  - `tests/test_metrics_parsing.py`: parsing, payload, stability unit tests.

### Layer interaction model
1. `orchestrator.py` initializes output/log directories and configures modules.
2. Registry/device detection flows through `device_registry.py` + `adb_wifi_setup.py` metadata helpers.
3. Installation is delegated to `install_apk_parallel.py`, which uses `adb_client.py`.
4. Benchmark execution is delegated to `performance_collector.py` using many ADB probes.
5. Results are persisted by orchestrator and rendered through `report_generator.py`.

---

## 3) Core Modules / Features

### 3.1 `orchestrator.py`
**Responsibility:** end-to-end workflow, argument validation, run directory management, stage execution, final summary.

Important elements:
- `parse_args()` validates CLI and supports quick-mode overrides.
- `_detect_valid_adb_devices()` + `_dedupe_physical_devices()` reduce duplicate ADB aliases for same physical device.
- `stage_connect_devices()` syncs active devices to registry.
- `stage_install_apk()` runs parallel install/launch.
- `stage_run_benchmarks()` runs metric collection in a thread pool for successful devices.
- `stage_collect_and_save()` writes deterministic sorted JSON outputs.
- `stage_generate_report()` creates `report.html`.

### 3.2 `adb_client.py`
**Responsibility:** shared resilient subprocess wrapper for ADB commands.

Important elements:
- `AdbResponse` dataclass with structured fields.
- `run_adb_command()` supports retries + exponential backoff and fast-fail for clearly non-transient errors (e.g., invalid APK, unknown package).

### 3.3 `device_registry.py`
**Responsibility:** persistent device inventory and status tracking in `devices.json`.

Important elements:
- Thread-safe read/write with `RLock`.
- `_validate_device()` enforces required fields and status domain.
- `get_active_devices()` parses `adb devices` online targets.
- `cleanup_registry()` prunes stale/duplicate entries and normalizes active devices to `available`.

### 3.4 `adb_wifi_setup.py`
**Responsibility:** first-time USB registration into Wi-Fi ADB.

Important elements:
- `get_connected_devices()` filters for physical USB targets, skipping emulators and network/mDNS aliases.
- `enable_tcpip()`, `get_device_ip()`, `connect_wifi()`, `get_device_name()` perform registration workflow.
- Outputs device metadata JSON for later automation.

### 3.5 `adb_reconnect.py`
**Responsibility:** reconnect known devices (`ip:port`) and update availability statuses.

Important elements:
- `load_devices()` / `save_devices()` for registry IO.
- `reconnect_device()` with retries and connection verification.
- Status updates to `available` or `offline` per device.

### 3.6 `install_apk_parallel.py`
**Responsibility:** parallel deployment + launch.

Important elements:
- `DeviceExecutionStatus` dataclass capturing per-device outcomes.
- `run_parallel()` executes install/launch concurrently via thread pool.
- `install_and_launch()` handles target resolution and failure classification (`failed` / `disconnected`).

### 3.7 `performance_collector.py`
**Responsibility:** metric gathering, parsing, retries, result shaping, quality normalization.

Important elements:
- Runtime parsers: CPU (`top`, `dumpsys cpuinfo` fallback), memory (`dumpsys meminfo`), FPS (`gfxinfo` with SurfaceFlinger fallback), launch times.
- Sampling logic: `collect_cpu_average()` fixed sample count from runtime window/interval.
- Startup benchmarking: `run_start_test()` for cold/warm/hot launch modes.
- GC telemetry: `collect_gc_count()` from logcat with PID/package matching.
- Payload hygiene: `validate_benchmark_result()` clamps invalid ranges and normalizes malformed startup buckets.

### 3.8 `report_generator.py`
**Responsibility:** transform metric JSON into rich standalone HTML dashboard.

Important elements:
- `_ensure_local_chart_js()` downloads Chart.js or writes lightweight JS fallback.
- `_collect_rows()` normalizes raw results into runtime/startup/device row models.
- `_build_summary()`, `_build_kpis()`, `_build_insights()` derive higher-level analytics.
- `_chart_payload()` emits structured chart data with nullable padding (not synthetic zeros).
- `_html_report()` contains full styled UI template + defensive chart rendering logic.

### 3.9 `logging_config.py`
**Responsibility:** one-time root logger initialization with file + console handlers.

---

## 4) Data Flow

## 4.1 Device lifecycle data flow
1. **Discovery:** `device_registry.get_active_devices()` parses current `adb devices` online set.
2. **De-duplication:** orchestrator maps multiple aliases to one physical serial and selects preferred target.
3. **Registry sync:** active devices are kept; stale entries removed; statuses normalized to `available`.
4. **Install stage input:** `install_apk_parallel.get_available_devices()` consumes registry entries.

## 4.2 Benchmark data flow
Per successful device:
1. Device metadata via `getprop` + `/proc/meminfo`.
2. Runtime metrics via `top`, `cpuinfo`, `meminfo`, `gfxinfo`, `SurfaceFlinger`, `logcat`.
3. Startup metrics from repeated `am start -W` in cold/warm/hot scenarios.
4. Validation/normalization pass ensures bounded, deterministic payload values.

Output shape (top level):
```json
{
  "<device_id>": {
    "device_details": {...},
    "runtime_metrics": {...},
    "startup_metrics": {...},
    "runtime_details": {...},
    "error": "...optional"
  }
}
```

## 4.3 Persistence and reporting flow
- Orchestrator writes both `performance_results.json` and `final_results.json` inside run directory.
- Report module reads in-memory results and writes `report.html` + `static/chart.min.js` in same run tree.

## 4.4 State handling model
- **Persistent state:** `devices.json` registry + run artifacts.
- **Ephemeral state:** in-memory runtime cache (`LAST_VALID_RUNTIME_BY_DEVICE`) for metric fallback inside a run.
- **Concurrency safety:** registry guarded by re-entrant lock; status collection done with thread pools.

---

## 5) Tech Stack

- **Language:** Python 3.10+ recommended.
- **Dependencies:** Standard library only (requirements intentionally minimal).
- **Core stdlib usage:** `subprocess`, `concurrent.futures`, `threading`, `json`, `pathlib`, `argparse`, `re`, `statistics`, `logging`, `urllib`.
- **Frontend reporting:** HTML/CSS/JS with Chart.js (CDN pull and local fallback).
- **Testing:** `unittest` + `unittest.mock`.

No Gradle/Android build system exists in this repository; APK is an external input artifact.

---

## 6) Key Functionalities

1. **USB-to-Wi-Fi ADB registration** for new devices.
2. **Registry-based reconnect and health status updates** for known devices.
3. **Parallel app deployment and launch** with robust error reporting.
4. **Runtime sampling** (CPU, memory, FPS, GC) across devices.
5. **Startup latency benchmarking** in cold/warm/hot modes with repeated runs.
6. **Result validation and sanitization** to reject impossible values.
7. **Rich single-page HTML analytics report** with KPIs, insights, tables, and charts.
8. **Timestamped run artifact isolation** (`performance_runs/run_<timestamp>/...`).

---

## 7) Important Logic / Algorithms

1. **Physical device de-duplication algorithm**
   - Extract physical serial using `ro.serialno` or `ro.boot.serialno`.
   - Group all active targets by serial.
   - Choose preferred alias based on priority (network target preferred over mDNS).

2. **ADB retry/backoff strategy**
   - Command retries with exponential delay.
   - Early break on non-transient semantic failures.

3. **Robust metric extraction fallbacks**
   - CPU: `top` parsing first, then `dumpsys cpuinfo`.
   - FPS: `gfxinfo` first, then SurfaceFlinger latency-derived FPS.
   - Runtime retries cache valid metric parts across attempts.

4. **Deterministic result serialization**
   - Sorting keys/device ids before writing JSON to ensure stable output order.

5. **Startup benchmark protocol**
   - Cold: force-stop before launch.
   - Warm: start then HOME before timing.
   - Hot: immediate relaunch while process warm.

6. **Validation boundaries**
   - CPU 0–400%, FPS 0–240, memory 0–64GB-equivalent MB bound.
   - Invalid runtime values converted to `"N/A"`; invalid startup samples filtered.

---

## 8) Configuration & Setup

### CLI configuration
- `orchestrator.py` arguments:
  - required: `--apk`, `--package`, `--activity`
  - optional: `--iterations`, `--max-threads`, `--timeout`, `--runtime-window`, `--sample-interval`, `--adb-retries`, `--quick`, `--output-dir`, `--debug`

### Runtime output configuration
- Orchestrator creates `run_<timestamp>` directories and configures:
  - logging directory via `logging_config.configure_logs_dir()`
  - install logs dir via `install_apk_parallel.set_logs_dir()`
  - collector output files via `performance_collector.set_output_directory()`

### Logging configuration
- One-time root logger setup with stream + file handlers and UTC timestamp-based file naming.

---

## 9) External Integrations

1. **Android Debug Bridge (ADB)**: primary integration point for all device operations and telemetry.
2. **Chart.js CDN**: optional download source for chart library.
3. **Android OS shell utilities**: `am`, `dumpsys`, `top`, `logcat`, `pidof`, `getprop`, filesystem reads.

No cloud APIs or databases are integrated.

---

## 10) Edge Cases / Known Issues

1. **Environment-dependent ADB output formats** can affect parser robustness (especially OEM `top` variants).
2. **Wi-Fi instability** may cause intermittent disconnects despite retries.
3. **GC count heuristic** depends on logcat format and PID/package matching; can under/overcount in noisy logs.
4. **Assumption-dependent runtime metrics**: benchmark assumes app remains foreground during sampling.
5. **Network-restricted environments** rely on fallback mini chart renderer rather than full Chart.js.
6. **Long benchmark durations** with default iterations/window can make multi-device runs lengthy.

---

## 11) Rebuild Instructions (From Scratch)

### Step 1 — Initialize project skeleton
Create files:
- `adb_client.py`
- `device_registry.py`
- `adb_wifi_setup.py`
- `adb_reconnect.py`
- `install_apk_parallel.py`
- `performance_collector.py`
- `report_generator.py`
- `orchestrator.py`
- `logging_config.py`
- `tests/test_metrics_parsing.py`
- `static/chart.min.js` (optional placeholder)
- `requirements.txt` (minimal)
- `README.md`

### Step 2 — Implement shared infrastructure
1. Build `logging_config.py` with one-time root logger setup.
2. Implement `adb_client.py` command runner with:
   - structured response object,
   - timeout handling,
   - retry/backoff,
   - non-transient fast-fail classification.

### Step 3 — Implement registry/state layer
1. JSON-backed `devices.json` persistence.
2. Thread-safe CRUD and status mutation.
3. `adb devices` parser for online device set.
4. Cleanup function for stale/duplicate pruning.

### Step 4 — Implement device onboarding and reconnect scripts
1. USB discovery and filtering (exclude emulator/network aliases).
2. TCP/IP enable + IP detection + `adb connect` + model capture.
3. Reconnect script that marks device `available/offline` with retry attempts.

### Step 5 — Implement parallel install/launch executor
1. Load available devices from registry.
2. Resolve ADB target from `device_id` or `ip:port` fields.
3. Run install and `am start` concurrently with thread pool.
4. Return sortable per-device status dataclass list.

### Step 6 — Implement performance collector
1. Parse and normalize runtime metrics (CPU/memory/FPS).
2. Add startup tests for cold/warm/hot with iterations.
3. Add GC counting from logcat.
4. Assemble result payload with device details and raw runtime context.
5. Validate/clamp outputs and fill `N/A` where needed.

### Step 7 — Implement report generator
1. Normalize results schema (support legacy aliases).
2. Build summary/KPIs/insights and table rows.
3. Build chart payload with nullable padding.
4. Emit single-page HTML with graceful no-data/error chart states.
5. Ensure local Chart.js presence (download + fallback mini renderer).

### Step 8 — Implement orchestrator pipeline
1. Parse CLI and validate inputs.
2. Create timestamped run directories.
3. Configure shared logs/output paths.
4. Connect/sync devices, install app, collect benchmarks in parallel.
5. Save sorted JSON outputs and generate report.
6. Print final execution summary.

### Step 9 — Add tests
Write unit tests for:
- CPU/memory/FPS/launch-time parsers,
- chart payload behavior (nullable padding),
- orchestrator save-order stability,
- benchmark result normalization.

### Step 10 — Operational runbook
1. Install Python + ADB.
2. Enable USB debugging on devices.
3. Register devices: `python adb_wifi_setup.py`.
4. Reconnect devices: `python adb_reconnect.py`.
5. Run benchmark:
   ```bash
   python orchestrator.py --apk /abs/path/app.apk --package com.example.app --activity .MainActivity
   ```
6. Inspect artifacts under `performance_runs/run_<timestamp>/`.

---

## Notes for another AI reproducing the project
- Keep all command execution list-based (`subprocess.run([...])`) to reduce shell injection risk.
- Preserve deterministic JSON ordering and payload key names; report layer depends on stable schema.
- Maintain retry/fallback behavior because device-farm ADB reliability is inherently variable.
- Preserve strict distinction between:
  - registry state (`devices.json`),
  - per-run artifacts (`performance_runs/run_*`),
  - generated report assets (`report.html`, `static/chart.min.js`).
