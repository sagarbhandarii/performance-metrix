# Performance-Metrix: Android Device Farm Testing (Python)

This project runs Android performance tests across multiple devices and generates an HTML report.

## Modules included

- `adb_wifi_setup.py`: register USB devices for Wi-Fi ADB.
- `device_registry.py`: maintain `devices.json` registry and statuses.
- `adb_reconnect.py`: reconnect devices by IP:port and refresh availability.
- `install_apk_parallel.py`: install APK + launch app in parallel.
- `performance_collector.py`: collect CPU, memory, launch time, FPS.
- `report_generator.py`: generate HTML report from JSON metrics.
- `orchestrator.py`: run full end-to-end flow.

---

## 1) Environment setup

## 1.1 Install Python (recommended: Python 3.10+)

This codebase uses modern Python features and is recommended on **Python 3.10 or newer**.

- **Windows**
  - Download installer: https://www.python.org/downloads/windows/
  - During install, check **“Add Python to PATH”**.

- **macOS**
  - `brew install python@3.11`
  - or install from python.org.

- **Linux (Debian/Ubuntu)**
  - `sudo apt update`
  - `sudo apt install -y python3 python3-pip python3-venv`

Verify:

```bash
python --version
# or
python3 --version
```

## 1.2 Create a virtual environment and install packages

From the project root:

- **Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

- **Windows (CMD)**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

- **macOS/Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Note: current project dependencies are standard-library only, so requirements installation is lightweight.

Verify:

```bash
python -m pip --version
```

## 1.3 Install Android ADB (platform-tools)

Install Android SDK Platform Tools (contains `adb`):

- Official download: https://developer.android.com/tools/releases/platform-tools

Alternative package managers:

- **macOS**: `brew install android-platform-tools`
- **Linux (Debian/Ubuntu)**: `sudo apt install -y android-sdk-platform-tools`
- **Windows**: download ZIP from official page and extract (e.g. `C:\platform-tools`).

## 1.4 Add ADB to system PATH

- **Windows**
  - Add platform-tools path (example `C:\platform-tools`) to Environment Variables → `Path`.
  - Restart terminal.

- **macOS/Linux**
  - Add to shell profile (`~/.zshrc` or `~/.bashrc`):
  ```bash
  export PATH="$PATH:/path/to/platform-tools"
  ```
  - Reload profile:
  ```bash
  source ~/.zshrc
  # or
  source ~/.bashrc
  ```

Verify ADB:

```bash
adb version
adb devices
```

---

## 2) Device setup (every new phone/tablet)

1. On Android device, enable **Developer Options**:
   - Settings → About phone → tap **Build number** 7 times.
2. Open Developer Options and enable **USB debugging**.
3. Connect device to host machine via USB.
4. When RSA prompt appears on device, tap **Allow** (optionally “Always allow from this computer”).

Verify:

```bash
adb devices
```

Expected state should be `device` (not `unauthorized`/`offline`).

---

## 3) First-time device registration (USB ➜ Wi-Fi ADB)

Run registration:

```bash
python adb_wifi_setup.py
```

What this script does for each USB-online device:

1. Detects USB devices using `adb devices`.
2. Enables TCP/IP mode on port `5555` (`adb -s <id> tcpip 5555`).
3. Gets device Wi-Fi IP address.
4. Connects over Wi-Fi (`adb connect <ip>:5555`).
5. Reads model name and writes records to `devices.json`.

Verify `devices.json` is created:

```bash
# macOS/Linux
cat devices.json

# Windows PowerShell
Get-Content .\devices.json
```

You should see entries with fields such as `device_id`, `ip_address`, `port`, `device_name`.

---

## 4) Wi-Fi connection setup (reconnect workflow)

Before reconnecting, ensure:

- Host machine and devices are on the **same Wi-Fi network/subnet**.
- Devices stay awake (disable aggressive battery optimization for testing if needed).

Run reconnect script:

```bash
python adb_reconnect.py
```

Then verify ADB targets:

```bash
adb devices
```

You should see `IP:5555` targets in `device` state (for example `192.168.1.20:5555`).

---

## 5) Run the full project

Run the orchestrator from project root:

```bash
python orchestrator.py --apk /absolute/path/to/app.apk --package com.example.app --activity .MainActivity
```

Optional arguments:

```bash
--max-threads 4 --timeout 90 --verbose
```

## Execution flow (what happens internally)

1. **Register new devices**: discovers USB devices and adds unregistered devices into `devices.json`.
2. **Reconnect known devices**: retries `adb connect` and marks each device `available` or `offline`.
3. **Filter available devices**: only devices with status `available` continue.
4. **Install and launch in parallel**: installs APK and starts `<package>/<activity>`.
5. **Collect metrics** per successful device: CPU, memory, launch time, FPS.
6. **Generate HTML report** from collected JSON data.

At end, a summary is printed: device count, success count, failure count, report path.

---

## 6) Output files

All paths below are generated in the **project root**:

- Device registry: `devices.json`
- Metrics output: `performance_results.json`
- HTML report: `report.html`
- Logs: `logs/test_run_<timestamp>.log`

Open HTML report:

- **Windows**
  ```powershell
  start .\report.html
  ```
- **macOS**
  ```bash
  open report.html
  ```
- **Linux**
  ```bash
  xdg-open report.html
  ```

---

## 7) Troubleshooting

## A) `adb` not recognized

Symptoms:
- `adb: command not found` (macOS/Linux)
- `'adb' is not recognized...` (Windows)

Fix:
1. Install platform-tools.
2. Add platform-tools directory to PATH.
3. Restart terminal and run `adb version`.

## B) Device shows `unauthorized`

Fix:
1. Reconnect USB cable.
2. On device, accept RSA fingerprint prompt.
3. Run:
```bash
adb kill-server
adb start-server
adb devices
```

## C) Device shows `offline`

Fix:
1. Toggle USB debugging off/on.
2. Re-plug cable / reconnect Wi-Fi.
3. Restart ADB server:
```bash
adb kill-server
adb start-server
adb devices
```
4. Run `python adb_reconnect.py` again.

## D) Frequent Wi-Fi disconnects

Fixes:
- Keep devices and host on same stable network.
- Disable AP/client isolation on test Wi-Fi.
- Keep devices charging and awake.
- Re-run reconnect script before orchestration:
```bash
python adb_reconnect.py
```

## E) APK install failure

Common reasons: signature conflict, insufficient storage, incompatible ABI/minSdk.

Useful commands:

```bash
adb -s <target> uninstall com.example.app
adb -s <target> install -r /path/to/app.apk
adb -s <target> shell pm list packages | grep example
```

If storage issue:

```bash
adb -s <target> shell df -h
```

---

## 8) Optional / production usage

## 8.1 Run on multiple devices

- Register all devices once via USB.
- Ensure all are in `devices.json`.
- Run with higher parallelism:

```bash
python orchestrator.py --apk /path/app.apk --package com.example.app --activity .MainActivity --max-threads 8
```

Choose `--max-threads` based on host CPU/network capacity.

## 8.2 Change APK path

Provide a different file at runtime:

```bash
python orchestrator.py --apk /new/builds/app-release.apk --package com.example.app --activity .MainActivity
```

Tip: use absolute paths in CI to avoid working-directory issues.

## 8.3 CI/CD integration example

In CI job:

1. Install Python + ADB platform-tools.
2. Restore/connect test devices/network (or use dedicated farm host).
3. Run:

```bash
python adb_reconnect.py
python orchestrator.py --apk ./artifacts/app.apk --package com.example.app --activity .MainActivity --max-threads 6 --timeout 120
```

4. Publish artifacts:
   - `performance_results.json`
   - `report.html`
   - `logs/`

Recommended for reliability:
- Fixed host with static networking.
- Dedicated AP for test devices.
- Pre-flight check step (`adb devices`) before each run.

---

## Quick start (minimal)

```bash
# 1) one-time environment
python -m venv .venv
# activate venv for your OS
pip install -r requirements.txt
adb version

# 2) one-time device registration over USB
python adb_wifi_setup.py

# 3) per test run
python adb_reconnect.py
python orchestrator.py --apk /path/app.apk --package com.example.app --activity .MainActivity
```
