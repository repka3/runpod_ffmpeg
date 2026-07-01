from __future__ import annotations

import logging
import time

import runpod

from .worker import process_job


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOGGER = logging.getLogger(__name__)


def handler(job: dict) -> dict:
    started_at = time.monotonic()

    def progress_update(payload: dict) -> None:
        runpod.serverless.progress_update(job, payload)

    try:
        return process_job(job, progress_update)
    except Exception as exc:
        payload = {
            "phase": "failed",
            "failed_phase": "handler",
            "error_prefix": "WORKER_FAILED",
            "message": f"{type(exc).__name__} in handler; see RunPod logs",
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
        LOGGER.error("handler_failed error=%s", exc, exc_info=True)
        try:
            progress_update(payload)
        except Exception:
            LOGGER.warning("handler_failed_progress_update_failed", exc_info=True)
        return payload


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
