from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .errors import FFprobeFailed
from .progress import format_hhmmss


LOGGER = logging.getLogger(__name__)


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFprobeFailed(f"ffprobe execution failed: {exc}") from exc

    if result.returncode != 0:
        tail = (result.stderr or "")[-1000:]
        raise FFprobeFailed(f"ffprobe exited with {result.returncode}: {tail}")

    first_line = (result.stdout or "").strip().splitlines()[0:1]
    if not first_line:
        raise FFprobeFailed("duration is missing or not positive")
    try:
        duration = float(first_line[0])
    except ValueError as exc:
        raise FFprobeFailed("duration is missing or not positive") from exc
    if duration <= 0:
        raise FFprobeFailed("duration is missing or not positive")

    LOGGER.info("media duration %s (%.3fs)", format_hhmmss(duration), duration)
    return duration
