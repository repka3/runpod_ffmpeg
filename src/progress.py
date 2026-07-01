from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable


ProgressCallback = Callable[[dict], None]

OUT_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)$")


def format_hhmmss(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_progress_time(key: str, value: str) -> float | None:
    if key == "out_time_us":
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    if key == "out_time":
        match = OUT_TIME_RE.match(value)
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return None


def expected_output_duration(
    probed_duration: float,
    input_seek_seconds: float | None,
    clip_t_seconds: float | None,
    clip_to_seconds: float | None,
) -> float:
    seek = input_seek_seconds or 0.0
    if clip_t_seconds is not None:
        expected = clip_t_seconds
    elif clip_to_seconds is not None:
        expected = clip_to_seconds - seek
    else:
        expected = probed_duration - seek
    return max(expected, 0.001)


class FFmpegProgress:
    def __init__(
        self,
        *,
        expected_seconds: float,
        callback: ProgressCallback,
        started_at: float,
        min_interval_seconds: float = 2.0,
        logger: logging.Logger | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self.expected_seconds = max(expected_seconds, 0.001)
        self.callback = callback
        self.started_at = started_at
        self.min_interval_seconds = min_interval_seconds
        self.logger = logger or logging.getLogger(__name__)
        self.now = now
        self.last_seen_percent = -1.0
        self.last_forwarded_percent = -1.0
        self.last_forwarded_at = 0.0
        self.last_logged_bucket = -10

    def update(self, processed_seconds: float, *, force: bool = False) -> None:
        raw_percent = processed_seconds / self.expected_seconds * 100
        percent = round(max(self.last_seen_percent, min(100.0, max(0.0, raw_percent))), 1)
        current = self.now()
        changed = percent != self.last_forwarded_percent
        should_forward = force or (changed and current - self.last_forwarded_at >= self.min_interval_seconds)
        self.last_seen_percent = percent
        if not should_forward:
            return
        self.last_forwarded_percent = percent
        self.last_forwarded_at = current
        bucket = int(percent // 10) * 10
        if bucket > self.last_logged_bucket:
            self.logger.info("ffmpeg progress %d%%", bucket)
            self.last_logged_bucket = bucket
        self.callback(
            {
                "phase": "running_ffmpeg",
                "percent": percent,
                "progress_available": True,
                "duration_seconds": round(current - self.started_at, 3),
            }
        )
