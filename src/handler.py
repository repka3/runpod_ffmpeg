from __future__ import annotations

import logging

import runpod

from .worker import process_job


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def handler(job: dict) -> dict:
    def progress_update(payload: dict) -> None:
        runpod.serverless.progress_update(job, payload)

    return process_job(job, progress_update)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
