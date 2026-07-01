import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import runpod


DEFAULT_INPUT_NAME = "input"
DEFAULT_OUTPUT_NAME = "output"
DEFAULT_TIMEOUT_SECONDS = 3600
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024


class JobInputError(ValueError):
    pass


def handle_job(job: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()

    try:
        job_input = _validate_job_input(job)
        with tempfile.TemporaryDirectory(prefix="ffmpeg-job-") as workdir:
            workdir_path = Path(workdir)
            input_path = workdir_path / job_input["input_name"]
            output_path = workdir_path / job_input["output_name"]

            _progress(job, "downloading input")
            downloaded_bytes = _download_file(job_input["source_url"], input_path)
            _progress(job, "running ffmpeg")
            command = _build_ffmpeg_command(job_input["ffmpeg_args"], input_path, output_path)
            process = _run_ffmpeg(command, job_input["timeout_seconds"])

            if not output_path.is_file():
                raise RuntimeError("ffmpeg completed but did not create the expected output file")

            _progress(job, "uploading output")
            uploaded_bytes = _upload_file(
                job_input["upload_url"],
                output_path,
                method=job_input["upload_method"],
                headers=job_input["upload_headers"],
            )
            _progress(job, "completed")

        return {
            "ok": True,
            "status": "completed",
            "downloaded_bytes": downloaded_bytes,
            "uploaded_bytes": uploaded_bytes,
            "ffmpeg": {
                "returncode": process.returncode,
                "stderr_tail": _tail(process.stderr),
            },
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except JobInputError as exc:
        return {
            "ok": False,
            "status": "invalid_input",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "status": "timeout",
            "error": f"ffmpeg timed out after {exc.timeout} seconds",
            "stderr_tail": _tail(exc.stderr or ""),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.CalledProcessError as exc:
        return {
            "ok": False,
            "status": "ffmpeg_failed",
            "error": f"ffmpeg exited with status {exc.returncode}",
            "stderr_tail": _tail(exc.stderr or ""),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "transfer_failed",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 3),
        }


def _validate_job_input(job: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise JobInputError("job must be an object")

    payload = job.get("input")
    if not isinstance(payload, dict):
        raise JobInputError("job.input must be an object")

    source_url = _required_url(payload, "source_url")
    upload_url = _required_url(payload, "upload_url")

    ffmpeg_args = payload.get("ffmpeg_args")
    if not isinstance(ffmpeg_args, list) or not ffmpeg_args:
        raise JobInputError("ffmpeg_args must be a non-empty array of strings")
    if not all(isinstance(arg, str) and arg for arg in ffmpeg_args):
        raise JobInputError("ffmpeg_args must contain only non-empty strings")
    if "{input}" not in ffmpeg_args:
        raise JobInputError("ffmpeg_args must include the {input} placeholder")
    if "{output}" not in ffmpeg_args:
        raise JobInputError("ffmpeg_args must include the {output} placeholder")

    upload_method = payload.get("upload_method", "PUT")
    if not isinstance(upload_method, str):
        raise JobInputError("upload_method must be a string")
    upload_method = upload_method.upper()
    if upload_method not in {"PUT", "POST"}:
        raise JobInputError("upload_method must be PUT or POST")

    upload_headers = payload.get("upload_headers", {})
    if not isinstance(upload_headers, dict):
        raise JobInputError("upload_headers must be an object")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in upload_headers.items()):
        raise JobInputError("upload_headers must contain only string keys and values")

    timeout_seconds = payload.get(
        "timeout_seconds",
        int(os.getenv("FFMPEG_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise JobInputError("timeout_seconds must be a positive integer")

    return {
        "source_url": source_url,
        "upload_url": upload_url,
        "ffmpeg_args": ffmpeg_args,
        "input_name": _safe_file_name(payload.get("input_name"), DEFAULT_INPUT_NAME),
        "output_name": _safe_file_name(payload.get("output_name"), DEFAULT_OUTPUT_NAME),
        "upload_method": upload_method,
        "upload_headers": upload_headers,
        "timeout_seconds": timeout_seconds,
    }


def _required_url(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise JobInputError(f"{key} must be a non-empty URL string")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise JobInputError(f"{key} must be an http(s) URL")

    return value


def _safe_file_name(value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise JobInputError("file names must be non-empty strings")
    if value != Path(value).name or value in {".", ".."}:
        raise JobInputError("file names must not contain path separators")
    return value


def _download_file(url: str, destination: Path) -> int:
    with requests.get(url, stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        written = 0
        with destination.open("wb") as file_obj:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    file_obj.write(chunk)
                    written += len(chunk)
    return written


def _build_ffmpeg_command(ffmpeg_args: list[str], input_path: Path, output_path: Path) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    command.extend(
        arg.replace("{input}", str(input_path)).replace("{output}", str(output_path))
        for arg in ffmpeg_args
    )
    return command


def _run_ffmpeg(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or is not on PATH")

    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _upload_file(url: str, path: Path, *, method: str, headers: dict[str, str]) -> int:
    size = path.stat().st_size
    with path.open("rb") as file_obj:
        response = requests.request(
            method,
            url,
            data=_read_chunks(file_obj),
            headers=headers,
            timeout=(10, 60),
        )
    response.raise_for_status()
    return size


def _read_chunks(file_obj):
    while True:
        chunk = file_obj.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def _tail(value: str, limit: int = 4000) -> str:
    return value[-limit:]


def _progress(job: dict[str, Any], message: str) -> None:
    try:
        runpod.serverless.progress_update(job, message)
    except Exception:
        pass
