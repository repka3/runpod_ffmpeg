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


def test_stage_failures_raise_with_existing_prefix(monkeypatch):
    monkeypatch.setattr("src.worker.download_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(DownloadFailed("boom")))

    try:
        process_job(valid_job())
    except Exception as exc:
        assert str(exc).startswith("DOWNLOAD_FAILED:")
    else:
        raise AssertionError("expected exception")
