"""Shared adb command runner with retries and structured responses."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List

import logging_config

LOGGER = logging_config.get_logger("adb_client")


@dataclass
class AdbResponse:
    success: bool
    output: str
    error: str
    returncode: int
    attempts: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "returncode": self.returncode,
            "attempts": self.attempts,
        }


def run_adb_command(
    cmd: List[str],
    timeout: int = 10,
    retries: int = 1,
    retry_delay_seconds: float = 0.8,
) -> AdbResponse:
    """Run command with bounded retries for transient adb failures."""
    attempts = 0
    last_error = ""
    last_output = ""
    last_return_code = -1

    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            last_output = (error.stdout or "").strip()
            last_error = f"timeout after {timeout}s"
            last_return_code = -1
        except OSError as error:
            last_output = ""
            last_error = str(error)
            last_return_code = -1
        else:
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            merged = f"{stdout}\n{stderr}".lower()

            last_output = stdout
            last_error = stderr
            last_return_code = result.returncode

            if result.returncode == 0:
                return AdbResponse(True, stdout, stderr, result.returncode, attempts)

            if not last_error:
                last_error = f"exit code {result.returncode}"

            # Fast-fail for known non-transient command errors.
            non_transient = (
                "unknown package",
                "activity class",
                "invalid apk",
                "failed to parse",
                "is not a valid",
            )
            if any(token in merged for token in non_transient):
                break

        if attempt < retries:
            sleep_seconds = retry_delay_seconds * (2**attempt)
            LOGGER.warning("ADB command failed (attempt %d/%d): %s", attempts, retries + 1, " ".join(cmd))
            time.sleep(sleep_seconds)

    return AdbResponse(False, last_output, last_error, last_return_code, attempts)
