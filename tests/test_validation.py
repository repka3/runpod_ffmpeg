import pytest

from src.errors import InvalidInput
from src.validation import derive_extension, redacted_url, validate_job


def valid_job(**overrides):
    payload = {
        "source_url": "https://bucket.example/input.mp4?signature=secret",
        "upload_url": "https://bucket.example/output.mp3?signature=secret",
        "input_args": ["-ss", "00:00:10"],
        "ffmpeg_args": ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"],
        "upload_headers": {"Content-Type": "audio/mpeg"},
    }
    payload.update(overrides)
    return {"input": payload}


def test_valid_job_derives_worker_owned_filenames():
    config = validate_job(valid_job())

    assert config.input_filename == "input.mp4"
    assert config.output_filename == "output.mp3"
    assert config.input_seek_seconds == 10
    assert config.clip_t_seconds is None
    assert config.upload_headers == {"Content-Type": "audio/mpeg"}


def test_rejects_raw_ffmpeg_string():
    with pytest.raises(InvalidInput, match="ffmpeg_args"):
        validate_job(valid_job(ffmpeg_args="-vn"))


def test_rejects_non_https_url():
    with pytest.raises(InvalidInput, match="source_url must be https"):
        validate_job(valid_job(source_url="http://bucket.example/input.mp4"))


def test_rejects_missing_url_extension():
    with pytest.raises(InvalidInput, match="filename extension"):
        validate_job(valid_job(upload_url="https://bucket.example/output?x=1"))


def test_rejects_blocked_upload_header_case_insensitive():
    with pytest.raises(InvalidInput, match="Content-Length"):
        validate_job(valid_job(upload_headers={"Content-Length": "123"}))


def test_rejects_upload_header_control_chars():
    with pytest.raises(InvalidInput, match="control"):
        validate_job(valid_job(upload_headers={"X-Test": "ok\nbad"}))


@pytest.mark.parametrize("option", ["-i", "-y", "-n", "-loglevel", "-progress", "-threads", "-f"])
def test_rejects_worker_owned_or_unsupported_ffmpeg_flags(option):
    with pytest.raises(InvalidInput):
        validate_job(valid_job(ffmpeg_args=[option, "value"]))


def test_rejects_stray_positional_token():
    with pytest.raises(InvalidInput, match="stray"):
        validate_job(valid_job(ffmpeg_args=["output.mp3"]))


def test_rejects_copy_codec():
    with pytest.raises(InvalidInput, match="copy"):
        validate_job(valid_job(ffmpeg_args=["-c:v", "copy"]))


def test_rejects_t_and_to_together():
    with pytest.raises(InvalidInput, match="mutually exclusive"):
        validate_job(valid_job(ffmpeg_args=["-t", "10", "-to", "20", "-c:a", "aac"]))


def test_rejects_unsafe_filter_protocol():
    with pytest.raises(InvalidInput, match="blocked protocol"):
        validate_job(valid_job(ffmpeg_args=["-vf", "scale=1280:-2,movie=https://example.test/x"]))


def test_rejects_unlisted_filter():
    with pytest.raises(InvalidInput, match="filter is not allowed"):
        validate_job(valid_job(ffmpeg_args=["-vf", "drawtext=text=hello"]))


def test_accepts_compression_args():
    config = validate_job(
        valid_job(
            upload_url="https://bucket.example/output.mp4?signature=secret",
            ffmpeg_args=[
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "32",
                "-vf",
                "scale=1280:-2,fps=15",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
            ],
        )
    )

    assert config.output_filename == "output.mp4"


def test_redacts_query_and_fragment():
    assert redacted_url("https://host.example/path/file.mp4?token=secret#frag") == "https://host.example/path/file.mp4"


def test_derive_extension_ignores_query_string():
    assert derive_extension("https://host.example/file.MP4?x=.mp3", {"mp4"}, "source_url") == "mp4"
