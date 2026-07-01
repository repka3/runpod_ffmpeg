from __future__ import annotations

import logging
from pathlib import Path
import selectors
import subprocess
import time

from .errors import FFmpegFailed
from .progress import FFmpegProgress, ProgressCallback, expected_output_duration, parse_progress_time
from .validation import JobConfig


FFMPEG_TIMEOUT_SECONDS = 3600
STDERR_TAIL_LIMIT = 4000
LOGGER = logging.getLogger(__name__)


def build_ffmpeg_command(config: JobConfig, input_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        *config.input_args,
        "-i",
        str(input_path),
        "-progress",
        "pipe:1",
        "-nostats",
        *config.ffmpeg_args,
        str(output_path),
    ]


def run_ffmpeg(
    config: JobConfig,
    input_path: Path,
    output_path: Path,
    *,
    probed_duration: float,
    progress_callback: ProgressCallback,
    started_at: float,
) -> None:
    command = build_ffmpeg_command(config, input_path, output_path)
    expected_seconds = expected_output_duration(
        probed_duration,
        config.input_seek_seconds,
        config.clip_t_seconds,
        config.clip_to_seconds,
    )
    LOGGER.info(
        "ffmpeg_command command=%r expected_output_seconds=%.3f probed_duration_seconds=%.3f "
        "input_seek_seconds=%r clip_t_seconds=%r clip_to_seconds=%r ffmpeg_timeout_s=%d",
        command,
        expected_seconds,
        probed_duration,
        config.input_seek_seconds,
        config.clip_t_seconds,
        config.clip_to_seconds,
        FFMPEG_TIMEOUT_SECONDS,
    )
    tracker = FFmpegProgress(
        expected_seconds=expected_seconds,
        callback=progress_callback,
        started_at=started_at,
        logger=LOGGER,
    )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise FFmpegFailed(f"failed to start ffmpeg: {exc}") from exc

    stderr_tail = ""
    deadline = time.monotonic() + FFMPEG_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")

    try:
        while selector.get_map():
            if time.monotonic() > deadline:
                process.kill()
                _, stderr = process.communicate()
                stderr_tail = _tail(stderr_tail + (stderr or ""))
                LOGGER.error("ffmpeg_timeout ffmpeg_timeout_s=%d stderr_tail=%r", FFMPEG_TIMEOUT_SECONDS, stderr_tail)
                raise FFmpegFailed(f"timeout after {FFMPEG_TIMEOUT_SECONDS}s: {stderr_tail}")
            for key, _ in selector.select(timeout=0.5):
                line = key.fileobj.readline()
                if line == "":
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    seconds = _parse_progress_line(line)
                    if seconds is not None:
                        tracker.update(seconds)
                else:
                    stderr_tail = _tail(stderr_tail + line)
            if process.poll() is not None and not selector.get_map():
                break
        returncode = process.wait(timeout=1)
    except FFmpegFailed:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        process.kill()
        raise FFmpegFailed(f"ffmpeg execution failed: {exc}") from exc
    finally:
        selector.close()

    if returncode != 0:
        LOGGER.error("ffmpeg_failed returncode=%d stderr_tail=%r", returncode, stderr_tail)
        raise FFmpegFailed(f"exit code {returncode}: {stderr_tail}")

    if not output_path.exists():
        LOGGER.error("ffmpeg_missing_output output_path=%s", output_path)
        raise FFmpegFailed("ffmpeg completed but output file is missing")
    try:
        output_stat = output_path.stat()
    except OSError as exc:
        raise FFmpegFailed(f"output file stat failed: {exc}") from exc

    LOGGER.info("ffmpeg_complete returncode=%d output_file=%s output_bytes=%d", returncode, output_path.name, output_stat.st_size)

    tracker.update(expected_seconds, force=True)


def _parse_progress_line(line: str) -> float | None:
    if "=" not in line:
        return None
    key, value = line.strip().split("=", 1)
    return parse_progress_time(key, value)


def _tail(value: str) -> str:
    return value[-STDERR_TAIL_LIMIT:]
