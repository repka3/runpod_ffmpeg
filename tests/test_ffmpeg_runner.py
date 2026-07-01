from pathlib import Path
import os
import subprocess

import pytest

from src.errors import FFmpegFailed
import src.ffmpeg_runner as ffmpeg_runner
from src.ffmpeg_runner import build_ffmpeg_command, run_ffmpeg
from src.validation import validate_job


def test_builds_worker_owned_command_order():
    config = validate_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp3",
                "input_args": ["-ss", "10"],
                "ffmpeg_args": ["-vn", "-c:a", "libmp3lame"],
            }
        }
    )

    command = build_ffmpeg_command(config, Path("/tmp/input.mp4"), Path("/tmp/output.mp3"))

    assert command[:5] == ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss"]
    assert command[5:8] == ["10", "-i", "/tmp/input.mp4"]
    assert command[8:11] == ["-progress", "pipe:1", "-nostats"]
    assert command[-1] == "/tmp/output.mp3"


def test_ffmpeg_failure_includes_capped_stderr(monkeypatch, tmp_path):
    config = validate_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp3",
                "ffmpeg_args": ["-vn", "-c:a", "libmp3lame"],
            }
        }
    )

    class FakeProcess:
        def __init__(self):
            self.stdout = None
            self.stderr = None

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(FFmpegFailed, match="exit code 1"):
        run_ffmpeg(
            config,
            tmp_path / "input.mp4",
            tmp_path / "output.mp3",
            probed_duration=10,
            progress_callback=lambda payload: None,
            started_at=0,
        )


def test_ffmpeg_timeout_raises_ffmpeg_failed(monkeypatch, tmp_path):
    config = validate_job(
        {
            "input": {
                "source_url": "https://example.com/input.mp4",
                "upload_url": "https://example.com/output.mp3",
                "ffmpeg_args": ["-vn", "-c:a", "libmp3lame"],
            }
        }
    )
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "r")

    class FakeProcess:
        def __init__(self):
            self.stdout = stdout
            self.stderr = None
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True
            os.close(write_fd)

        def communicate(self):
            return "", "still running"

    fake_process = FakeProcess()
    times = iter([0.0, 2.0])
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake_process)
    monkeypatch.setattr(ffmpeg_runner, "FFMPEG_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(ffmpeg_runner.time, "monotonic", lambda: next(times))

    with pytest.raises(FFmpegFailed, match="timeout after 1s"):
        run_ffmpeg(
            config,
            tmp_path / "input.mp4",
            tmp_path / "output.mp3",
            probed_duration=10,
            progress_callback=lambda payload: None,
            started_at=0,
        )

    assert fake_process.killed is True
    stdout.close()
