from __future__ import annotations

import logging
from pathlib import Path
import shutil
import tempfile
import time

from .errors import WorkerError
from .ffmpeg_runner import run_ffmpeg
from .probe import probe_duration
from .progress import ProgressCallback
from .transfers import download_file, upload_file
from .validation import redacted_url, validate_job


LOGGER = logging.getLogger(__name__)


def process_job(job: dict, progress_update: ProgressCallback | None = None) -> dict:
    started_at = time.monotonic()
    progress_update = progress_update or (lambda _payload: None)
    job_id = str(job.get("id", "<unknown>")) if isinstance(job, dict) else "<invalid-job>"
    current_stage = "validating"
    temp_dir: Path | None = None
    LOGGER.info("job_received job_id=%s input_keys=%s", job_id, _input_keys(job))

    def safe_progress_update(payload: dict) -> None:
        try:
            progress_update(payload)
        except Exception:
            LOGGER.warning(
                "progress_update_failed job_id=%s phase=%s",
                job_id,
                payload.get("phase"),
                exc_info=True,
            )

    try:
        config = validate_job(job)
        LOGGER.info(
            "job_validated job_id=%s source_url=%s upload_url=%s input_file=%s output_file=%s "
            "input_args=%r ffmpeg_args=%r upload_header_names=%s",
            job_id,
            redacted_url(config.source_url),
            redacted_url(config.upload_url),
            config.input_filename,
            config.output_filename,
            config.input_args,
            config.ffmpeg_args,
            sorted(config.upload_headers.keys()),
        )

        current_stage = "initializing"
        temp_dir = Path(tempfile.mkdtemp(prefix="runpod-ffmpeg-"))
        stage_started_at = started_at

        def emit(phase: str) -> None:
            payload = {
                "phase": phase,
                "percent": None,
                "progress_available": False,
                "duration_seconds": round(time.monotonic() - started_at, 3),
            }
            safe_progress_update(payload)

        def stage_start(phase: str, **context: object) -> None:
            nonlocal current_stage, stage_started_at
            current_stage = phase
            stage_started_at = time.monotonic()
            emit(phase)
            LOGGER.info(
                "stage_start job_id=%s phase=%s elapsed_seconds=%.3f %s",
                job_id,
                phase,
                stage_started_at - started_at,
                _format_context(context),
            )

        def stage_complete(phase: str, **context: object) -> None:
            now = time.monotonic()
            LOGGER.info(
                "stage_complete job_id=%s phase=%s phase_seconds=%.3f elapsed_seconds=%.3f %s",
                job_id,
                phase,
                now - stage_started_at,
                now - started_at,
                _format_context(context),
            )

        input_path = temp_dir / config.input_filename
        output_path = temp_dir / config.output_filename

        stage_start(
            "downloading",
            source_url=redacted_url(config.source_url),
            input_file=input_path.name,
        )
        downloaded_bytes = download_file(config.source_url, input_path)
        stage_complete("downloading", downloaded_bytes=downloaded_bytes, input_file=input_path.name)

        stage_start("probing", input_file=input_path.name)
        duration = probe_duration(input_path)
        stage_complete("probing", media_duration_seconds=round(duration, 3))

        stage_start(
            "running_ffmpeg",
            input_file=input_path.name,
            output_file=output_path.name,
            input_args=config.input_args,
            ffmpeg_args=config.ffmpeg_args,
        )
        run_ffmpeg(
            config,
            input_path,
            output_path,
            probed_duration=duration,
            progress_callback=safe_progress_update,
            started_at=started_at,
        )
        output_size = output_path.stat().st_size
        output_duration = probe_duration(output_path)
        media_duration_seconds = round(output_duration, 3)
        stage_complete(
            "running_ffmpeg",
            output_file=output_path.name,
            output_bytes=output_size,
            media_duration_seconds=media_duration_seconds,
        )

        stage_start(
            "uploading",
            upload_url=redacted_url(config.upload_url),
            output_file=output_path.name,
            output_bytes=output_size,
            upload_header_names=sorted(config.upload_headers.keys()),
        )
        uploaded_bytes = upload_file(config.upload_url, output_path, config.upload_headers)
        stage_complete("uploading", uploaded_bytes=uploaded_bytes, output_file=output_path.name)

        duration_seconds = round(time.monotonic() - started_at, 3)
        result = {
            "phase": "done",
            "media_duration_seconds": media_duration_seconds,
            "duration_seconds": duration_seconds,
        }
        emit("done")
        LOGGER.info("job_complete job_id=%s duration_seconds=%.3f output_file=%s", job_id, duration_seconds, output_path.name)
        return result
    except Exception as exc:
        failed_payload = _failed_payload(exc, current_stage, started_at)
        LOGGER.error(
            "stage_failed job_id=%s phase=%s elapsed_seconds=%.3f error=%s",
            job_id,
            current_stage,
            failed_payload["duration_seconds"],
            exc,
            exc_info=True,
        )
        safe_progress_update(failed_payload)
        return failed_payload
    finally:
        if temp_dir is not None:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                LOGGER.info("cleanup_complete job_id=%s temp_dir=%s", job_id, temp_dir)
            except Exception:
                LOGGER.warning("cleanup_failed job_id=%s temp_dir=%s", job_id, temp_dir, exc_info=True)


def _input_keys(job: dict) -> list[str]:
    if not isinstance(job, dict) or not isinstance(job.get("input"), dict):
        return []
    return sorted(job["input"].keys())


def _format_context(context: dict[str, object]) -> str:
    if not context:
        return ""
    return " ".join(f"{key}={value!r}" for key, value in context.items())


def _failed_payload(exc: Exception, failed_phase: str, started_at: float) -> dict:
    if isinstance(exc, WorkerError):
        error_prefix = exc.prefix
        message = str(exc)
    else:
        error_prefix = "WORKER_FAILED"
        message = f"{type(exc).__name__} in {failed_phase}; see RunPod logs"
    return {
        "phase": "failed",
        "failed_phase": failed_phase,
        "error_prefix": error_prefix,
        "message": message,
        "duration_seconds": round(time.monotonic() - started_at, 3),
    }
