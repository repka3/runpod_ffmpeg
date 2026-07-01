from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .errors import DownloadFailed, LimitExceeded, UploadFailed
from .validation import redacted_url


MAX_BYTES = 5 * 1024 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
STALL_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024
MAX_DOWNLOAD_REDIRECTS = 5
DOWNLOAD_OK = {200, 206}
UPLOAD_OK = {200, 201, 204}


def download_file(url: str, destination: Path, session: requests.Session | None = None) -> int:
    session = session or requests.Session()
    current_url = url
    for redirect_count in range(MAX_DOWNLOAD_REDIRECTS + 1):
        _require_https(current_url, DownloadFailed, "download redirect is not https")
        try:
            response = session.get(
                current_url,
                stream=True,
                allow_redirects=False,
                timeout=(CONNECT_TIMEOUT_SECONDS, STALL_TIMEOUT_SECONDS),
            )
        except requests.RequestException as exc:
            raise DownloadFailed(f"{exc} while downloading {redacted_url(current_url)}") from exc

        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise DownloadFailed(f"redirect without Location while downloading {redacted_url(current_url)}")
            if redirect_count == MAX_DOWNLOAD_REDIRECTS:
                raise DownloadFailed(f"too many redirects while downloading {redacted_url(url)}")
            current_url = urljoin(current_url, location)
            continue

        if response.status_code not in DOWNLOAD_OK:
            response.close()
            raise DownloadFailed(f"HTTP {response.status_code} while downloading {redacted_url(current_url)}")

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BYTES:
                    response.close()
                    raise LimitExceeded("input exceeded 5 GB")
            except ValueError:
                pass

        total = 0
        try:
            with destination.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise LimitExceeded("input exceeded 5 GB")
                    file_obj.write(chunk)
        except LimitExceeded:
            raise
        except OSError as exc:
            raise DownloadFailed(f"local write failed while downloading {redacted_url(current_url)}: {exc}") from exc
        except requests.RequestException as exc:
            raise DownloadFailed(f"{exc} while downloading {redacted_url(current_url)}") from exc
        finally:
            response.close()
        return total
    raise DownloadFailed(f"too many redirects while downloading {redacted_url(url)}")


def upload_file(url: str, path: Path, headers: dict[str, str], session: requests.Session | None = None) -> int:
    session = session or requests.Session()
    _require_https(url, UploadFailed, "upload_url must be https")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UploadFailed(f"local output stat failed: {exc}") from exc
    if size > MAX_BYTES:
        raise LimitExceeded("output exceeded 5 GB")

    request_headers = dict(headers)
    request_headers["Content-Length"] = str(size)
    try:
        with path.open("rb") as file_obj:
            # requests does not expose a true per-chunk write timeout for file-backed PUT.
            # The connect/read timeout bounds socket stalls; RunPod executionTimeout is the outer bound.
            response = session.put(
                url,
                data=file_obj,
                headers=request_headers,
                allow_redirects=False,
                timeout=(CONNECT_TIMEOUT_SECONDS, STALL_TIMEOUT_SECONDS),
            )
    except requests.RequestException as exc:
        raise UploadFailed(f"{exc} while uploading {redacted_url(url)}") from exc
    except OSError as exc:
        raise UploadFailed(f"local read failed while uploading {redacted_url(url)}: {exc}") from exc

    try:
        if response.status_code not in UPLOAD_OK:
            raise UploadFailed(f"HTTP {response.status_code} while uploading {redacted_url(url)}")
    finally:
        response.close()
    return size


def _require_https(url: str, exc_type: type[Exception], message: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise exc_type(f"{message}: {redacted_url(url)}")
