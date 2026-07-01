import subprocess

import pytest

from src.worker import handle_job


def test_rejects_raw_ffmpeg_string():
    result = handle_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp4",
                "ffmpeg_args": "-i {input} {output}",
            }
        }
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert "ffmpeg_args" in result["error"]


def test_requires_input_and_output_placeholders():
    result = handle_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp4",
                "ffmpeg_args": ["-i", "{input}", "-f", "null"],
            }
        }
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert "{output}" in result["error"]


def test_rejects_path_traversal_file_names():
    result = handle_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp4",
                "ffmpeg_args": ["-i", "{input}", "{output}"],
                "output_name": "../output.mp4",
            }
        }
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert "path separators" in result["error"]


def test_returns_ffmpeg_failure(monkeypatch, tmp_path):
    input_file = tmp_path / "input"
    output_file = tmp_path / "output"
    input_file.write_bytes(b"input")

    def fake_validate(_job):
        return {
            "source_url": "https://example.com/input.mp4",
            "upload_url": "https://example.com/output.mp4",
            "ffmpeg_args": ["-i", "{input}", "{output}"],
            "input_name": "input",
            "output_name": "output",
            "upload_method": "PUT",
            "upload_headers": {},
            "timeout_seconds": 60,
        }

    def fake_download(_url, destination):
        destination.write_bytes(b"input")
        return 5

    def fake_run(_command, _timeout):
        raise subprocess.CalledProcessError(2, ["ffmpeg"], stderr="bad codec")

    monkeypatch.setattr("src.worker._validate_job_input", fake_validate)
    monkeypatch.setattr("src.worker._download_file", fake_download)
    monkeypatch.setattr("src.worker._run_ffmpeg", fake_run)

    result = handle_job({"input": {}})

    assert input_file.exists()
    assert not output_file.exists()
    assert result["ok"] is False
    assert result["status"] == "ffmpeg_failed"
    assert result["stderr_tail"] == "bad codec"


def test_completed_job_downloads_runs_and_uploads(monkeypatch):
    seen = {}

    def fake_download(_url, destination):
        seen["download_path"] = destination
        destination.write_bytes(b"input")
        return 5

    def fake_run(command, _timeout):
        seen["command"] = command
        output_path = command[-1]
        with open(output_path, "wb") as file_obj:
            file_obj.write(b"output")
        return subprocess.CompletedProcess(command, 0, stderr="done")

    def fake_upload(_url, path, *, method, headers):
        seen["upload_path"] = path
        seen["method"] = method
        seen["headers"] = headers
        return path.stat().st_size

    monkeypatch.setattr("src.worker._download_file", fake_download)
    monkeypatch.setattr("src.worker._run_ffmpeg", fake_run)
    monkeypatch.setattr("src.worker._upload_file", fake_upload)

    result = handle_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp4",
                "ffmpeg_args": ["-i", "{input}", "-c:v", "libx264", "{output}"],
                "output_name": "output.mp4",
                "upload_headers": {"Content-Type": "video/mp4"},
            }
        }
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["downloaded_bytes"] == 5
    assert result["uploaded_bytes"] == 6
    assert seen["command"][0:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    assert seen["command"][-1].endswith("output.mp4")
    assert seen["method"] == "PUT"
    assert seen["headers"] == {"Content-Type": "video/mp4"}

