import logging

from src.errors import DownloadFailed
from src.worker import process_job


def valid_job():
    return {
        "input": {
            "source_url": "https://example.com/input.mp4",
            "upload_url": "https://example.com/output.mp3",
            "ffmpeg_args": ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"],
        }
    }


def test_successful_orchestration(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    phases = []

    def fake_download(_url, path):
        path.write_bytes(b"input")
        return 5

    def fake_probe(_path):
        return 120

    def fake_run(_config, _input_path, output_path, **kwargs):
        output_path.write_bytes(b"output")
        kwargs["progress_callback"](
            {
                "phase": "running_ffmpeg",
                "percent": 100.0,
                "progress_available": True,
                "duration_seconds": 1.0,
            }
        )

    def fake_upload(_url, _path, _headers):
        return 6

    monkeypatch.setattr("src.worker.download_file", fake_download)
    monkeypatch.setattr("src.worker.probe_duration", fake_probe)
    monkeypatch.setattr("src.worker.run_ffmpeg", fake_run)
    monkeypatch.setattr("src.worker.upload_file", fake_upload)

    result = process_job(valid_job(), phases.append)

    assert result["phase"] == "done"
    assert [payload["phase"] for payload in phases] == [
        "downloading",
        "probing",
        "running_ffmpeg",
        "running_ffmpeg",
        "uploading",
        "done",
    ]
    log_text = caplog.text
    assert "job_validated" in log_text
    assert "ffmpeg_args=['-vn', '-c:a', 'libmp3lame', '-b:a', '128k']" in log_text
    assert "stage_complete" in log_text
    assert "downloaded_bytes=5" in log_text
    assert "uploaded_bytes=6" in log_text


def test_logs_header_names_without_header_values(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    job = valid_job()
    job["input"]["upload_headers"] = {"Content-Type": "secret-audio-type"}

    monkeypatch.setattr("src.worker.download_file", lambda _url, path: path.write_bytes(b"input") or 5)
    monkeypatch.setattr("src.worker.probe_duration", lambda _path: 120)
    monkeypatch.setattr("src.worker.run_ffmpeg", lambda _config, _input_path, output_path, **_kwargs: output_path.write_bytes(b"output"))
    monkeypatch.setattr("src.worker.upload_file", lambda *_args, **_kwargs: 6)

    process_job(job)

    assert "upload_header_names=['Content-Type']" in caplog.text
    assert "secret-audio-type" not in caplog.text


def test_stage_failures_return_failed_phase_with_existing_prefix(monkeypatch):
    monkeypatch.setattr("src.worker.download_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(DownloadFailed("boom")))

    result = process_job(valid_job())

    assert result["phase"] == "failed"
    assert "error" not in result
    assert result["failed_phase"] == "downloading"
    assert result["error_prefix"] == "DOWNLOAD_FAILED"
    assert result["message"].startswith("DOWNLOAD_FAILED:")


def test_validation_failure_returns_failed_phase():
    result = process_job({"input": {"source_url": "bad"}})

    assert result["phase"] == "failed"
    assert "error" not in result
    assert result["failed_phase"] == "validating"
    assert result["error_prefix"] == "INVALID_INPUT"


def test_unexpected_exception_returns_failed_phase_without_raw_message(monkeypatch):
    monkeypatch.setattr("src.worker.download_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret raw bug")))

    result = process_job(valid_job())

    assert result["phase"] == "failed"
    assert "error" not in result
    assert result["failed_phase"] == "downloading"
    assert result["error_prefix"] == "WORKER_FAILED"
    assert "RuntimeError in downloading" in result["message"]
    assert "secret raw bug" not in result["message"]


def test_failed_phase_is_emitted_as_progress(monkeypatch):
    progress = []
    monkeypatch.setattr("src.worker.download_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(DownloadFailed("boom")))

    process_job(valid_job(), progress.append)

    assert progress[-1]["phase"] == "failed"
    assert progress[-1]["failed_phase"] == "downloading"


def test_done_progress_failure_does_not_turn_success_into_failure(monkeypatch):
    def fake_download(_url, path):
        path.write_bytes(b"input")
        return 5

    def fake_run(_config, _input_path, output_path, **_kwargs):
        output_path.write_bytes(b"output")

    def progress_update(payload):
        if payload["phase"] == "done":
            raise RuntimeError("progress transport failed")

    monkeypatch.setattr("src.worker.download_file", fake_download)
    monkeypatch.setattr("src.worker.probe_duration", lambda _path: 120)
    monkeypatch.setattr("src.worker.run_ffmpeg", fake_run)
    monkeypatch.setattr("src.worker.upload_file", lambda *_args, **_kwargs: 6)

    result = process_job(valid_job(), progress_update)

    assert result["phase"] == "done"


def test_ffmpeg_progress_failure_does_not_abort_success(monkeypatch):
    def fake_download(_url, path):
        path.write_bytes(b"input")
        return 5

    def fake_run(_config, _input_path, output_path, **kwargs):
        kwargs["progress_callback"](
            {
                "phase": "running_ffmpeg",
                "percent": 50.0,
                "progress_available": True,
                "duration_seconds": 1.0,
            }
        )
        output_path.write_bytes(b"output")

    def progress_update(payload):
        if payload["phase"] == "running_ffmpeg" and payload["progress_available"]:
            raise RuntimeError("progress transport failed")

    monkeypatch.setattr("src.worker.download_file", fake_download)
    monkeypatch.setattr("src.worker.probe_duration", lambda _path: 120)
    monkeypatch.setattr("src.worker.run_ffmpeg", fake_run)
    monkeypatch.setattr("src.worker.upload_file", lambda *_args, **_kwargs: 6)

    result = process_job(valid_job(), progress_update)

    assert result["phase"] == "done"
