from __future__ import annotations

import logging
from pathlib import Path
import shutil
import tempfile
import time

from .ffmpeg_runner import run_ffmpeg
from .probe import probe_duration
from .progress import ProgressCallback
from .transfers import download_file, upload_file
from .validation import validate_job


LOGGER = logging.getLogger(__name__)


def process_job(job: dict, progress_update: ProgressCallback | None = None) -> dict:
    started_at = time.monotonic()
    progress_update = progress_update or (lambda _payload: None)
    config = validate_job(job)
    temp_dir = Path(tempfile.mkdtemp(prefix="runpod-ffmpeg-"))

    def emit(phase: str) -> None:
        payload = {
            "phase": phase,
            "percent": None,
            "progress_available": False,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
        progress_update(payload)

    try:
        input_path = temp_dir / config.input_filename
        output_path = temp_dir / config.output_filename

        emit("downloading")
        LOGGER.info("downloading input to %s", input_path.name)
        download_file(config.source_url, input_path)

        emit("probing")
        LOGGER.info("probing input")
        duration = probe_duration(input_path)

        emit("running_ffmpeg")
        LOGGER.info("running ffmpeg")
        run_ffmpeg(
            config,
            input_path,
            output_path,
            probed_duration=duration,
            progress_callback=progress_update,
            started_at=started_at,
        )

        emit("uploading")
        LOGGER.info("uploading output from %s", output_path.name)
        upload_file(config.upload_url, output_path, config.upload_headers)

        emit("done")
        return {"phase": "done", "duration_seconds": round(time.monotonic() - started_at, 3)}
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            LOGGER.warning("temporary directory cleanup failed", exc_info=True)
