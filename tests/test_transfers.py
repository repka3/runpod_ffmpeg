from pathlib import Path

import pytest
import responses

from src.errors import DownloadFailed, LimitExceeded, UploadFailed
from src.transfers import MAX_BYTES, download_file, upload_file


@responses.activate
def test_download_counts_bytes_and_writes_file(tmp_path):
    responses.add(responses.GET, "https://example.com/input.mp4", body=b"abc", status=200)

    total = download_file("https://example.com/input.mp4?secret=1", tmp_path / "input.mp4")

    assert total == 3
    assert (tmp_path / "input.mp4").read_bytes() == b"abc"


@responses.activate
def test_download_rejects_oversized_content_length(tmp_path):
    responses.add(
        responses.GET,
        "https://example.com/input.mp4",
        body=b"",
        status=200,
        headers={"Content-Length": str(MAX_BYTES + 1)},
    )

    with pytest.raises(LimitExceeded, match="input exceeded"):
        download_file("https://example.com/input.mp4", tmp_path / "input.mp4")


@responses.activate
def test_download_aborts_when_streamed_bytes_exceed_limit(monkeypatch, tmp_path):
    monkeypatch.setattr("src.transfers.MAX_BYTES", 2)
    responses.add(responses.GET, "https://example.com/input.mp4", body=b"abc", status=200)

    with pytest.raises(LimitExceeded, match="input exceeded"):
        download_file("https://example.com/input.mp4", tmp_path / "input.mp4")


@responses.activate
def test_download_rejects_non_https_redirect(tmp_path):
    responses.add(
        responses.GET,
        "https://example.com/input.mp4",
        status=302,
        headers={"Location": "http://example.com/input.mp4"},
    )

    with pytest.raises(DownloadFailed, match="not https"):
        download_file("https://example.com/input.mp4", tmp_path / "input.mp4")


@responses.activate
def test_download_rejects_too_many_redirects(monkeypatch, tmp_path):
    monkeypatch.setattr("src.transfers.MAX_DOWNLOAD_REDIRECTS", 0)
    responses.add(
        responses.GET,
        "https://example.com/input.mp4",
        status=302,
        headers={"Location": "https://example.com/next.mp4"},
    )

    with pytest.raises(DownloadFailed, match="too many redirects"):
        download_file("https://example.com/input.mp4", tmp_path / "input.mp4")


@responses.activate
def test_download_status_failure_redacts_query(tmp_path):
    responses.add(responses.GET, "https://example.com/input.mp4", status=403)

    with pytest.raises(DownloadFailed) as exc_info:
        download_file("https://example.com/input.mp4?token=secret", tmp_path / "input.mp4")

    assert "token=secret" not in str(exc_info.value)
    assert "https://example.com/input.mp4" in str(exc_info.value)


@responses.activate
def test_download_write_error_maps_to_download_failed():
    responses.add(responses.GET, "https://example.com/input.mp4", body=b"abc", status=200)

    class BadFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, _chunk):
            raise OSError("disk full")

    class BadDestination:
        def open(self, _mode):
            return BadFile()

    with pytest.raises(DownloadFailed, match="local write failed"):
        download_file("https://example.com/input.mp4", BadDestination())


@responses.activate
def test_upload_puts_file_with_content_length_and_headers(tmp_path):
    path = tmp_path / "output.mp3"
    path.write_bytes(b"abc")
    responses.add(responses.PUT, "https://example.com/output.mp3", status=200)

    uploaded = upload_file("https://example.com/output.mp3?token=secret", path, {"Content-Type": "audio/mpeg"})

    assert uploaded == 3
    request = responses.calls[0].request
    assert request.headers["Content-Length"] == "3"
    assert request.headers["Content-Type"] == "audio/mpeg"


def test_upload_rejects_oversized_output(monkeypatch, tmp_path):
    path = tmp_path / "output.mp3"
    path.write_bytes(b"")

    class FakeStat:
        st_size = MAX_BYTES + 1

    monkeypatch.setattr(Path, "stat", lambda self: FakeStat())

    with pytest.raises(LimitExceeded, match="output exceeded"):
        upload_file("https://example.com/output.mp3", path, {})


def test_upload_read_error_maps_to_upload_failed(monkeypatch, tmp_path):
    path = tmp_path / "output.mp3"
    path.write_bytes(b"abc")

    class BadSession:
        def put(self, _url, *, data, **_kwargs):
            data.read()

    class BadFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, *_args):
            raise OSError("read failed")

    monkeypatch.setattr(type(path), "open", lambda self, mode="r", *args, **kwargs: BadFile())
    with pytest.raises(UploadFailed, match="local read failed"):
        upload_file("https://example.com/output.mp3", path, {}, session=BadSession())


@responses.activate
def test_upload_rejects_redirect_without_following(tmp_path):
    path = tmp_path / "output.mp3"
    path.write_bytes(b"abc")
    responses.add(responses.PUT, "https://example.com/output.mp3", status=307)

    with pytest.raises(UploadFailed, match="HTTP 307"):
        upload_file("https://example.com/output.mp3", path, {})
