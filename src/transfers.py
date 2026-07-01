from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

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
ERROR_FIELD_LIMIT = 300
LOGGER = logging.getLogger(__name__)


def download_file(url: str, destination: Path, session: requests.Session | None = None) -> int:
    session = session or requests.Session()
    current_url = url
    for redirect_count in range(MAX_DOWNLOAD_REDIRECTS + 1):
        _require_https(current_url, DownloadFailed, "download redirect is not https")
        LOGGER.info(
            "download_request url=%s destination=%s redirect_count=%d timeout_connect=%ss timeout_stall=%ss max_bytes=%d",
            redacted_url(current_url),
            _path_name(destination),
            redirect_count,
            CONNECT_TIMEOUT_SECONDS,
            STALL_TIMEOUT_SECONDS,
            MAX_BYTES,
        )
        try:
            response = session.get(
                current_url,
                stream=True,
                allow_redirects=False,
                timeout=(CONNECT_TIMEOUT_SECONDS, STALL_TIMEOUT_SECONDS),
            )
        except requests.RequestException as exc:
            raise DownloadFailed(
                f"{_safe_exception_summary(exc)} while downloading {redacted_url(current_url)}"
            ) from None

        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            LOGGER.info(
                "download_redirect status=%d from=%s location=%s",
                response.status_code,
                redacted_url(current_url),
                redacted_url(urljoin(current_url, location)) if location else "<missing>",
            )
            response.close()
            if not location:
                raise DownloadFailed(f"redirect without Location while downloading {redacted_url(current_url)}")
            if redirect_count == MAX_DOWNLOAD_REDIRECTS:
                raise DownloadFailed(f"too many redirects while downloading {redacted_url(url)}")
            current_url = urljoin(current_url, location)
            continue

        if response.status_code not in DOWNLOAD_OK:
            detail = _response_detail(response)
            response.close()
            raise DownloadFailed(
                f"HTTP {response.status_code} while downloading {redacted_url(current_url)}; {detail}"
            )

        content_length = response.headers.get("Content-Length")
        LOGGER.info(
            "download_response status=%d url=%s content_length=%s content_type=%s",
            response.status_code,
            redacted_url(current_url),
            content_length or "<missing>",
            response.headers.get("Content-Type", "<missing>"),
        )
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
            raise DownloadFailed(
                f"{_safe_exception_summary(exc)} while downloading {redacted_url(current_url)}"
            ) from None
        finally:
            response.close()
        LOGGER.info(
            "download_complete url=%s bytes=%d destination=%s",
            redacted_url(current_url),
            total,
            _path_name(destination),
        )
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
    LOGGER.info(
        "upload_request url=%s source=%s bytes=%d header_names=%s timeout_connect=%ss timeout_stall=%ss",
        redacted_url(url),
        _path_name(path),
        size,
        sorted(request_headers.keys()),
        CONNECT_TIMEOUT_SECONDS,
        STALL_TIMEOUT_SECONDS,
    )
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
        raise UploadFailed(f"{_safe_exception_summary(exc)} while uploading {redacted_url(url)}") from None
    except OSError as exc:
        raise UploadFailed(f"local read failed while uploading {redacted_url(url)}: {exc}") from exc

    try:
        if response.status_code not in UPLOAD_OK:
            detail = _response_detail(response)
            raise UploadFailed(f"HTTP {response.status_code} while uploading {redacted_url(url)}; {detail}")
        LOGGER.info("upload_complete url=%s status=%d bytes=%d", redacted_url(url), response.status_code, size)
    finally:
        response.close()
    return size


def _require_https(url: str, exc_type: type[Exception], message: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise exc_type(f"{message}: {redacted_url(url)}")


def _path_name(path: Path) -> str:
    return getattr(path, "name", str(path))


def _response_detail(response: requests.Response) -> str:
    safe_headers = {
        name: response.headers[name]
        for name in (
            "Content-Type",
            "Content-Length",
            "x-amz-request-id",
            "x-amz-id-2",
            "x-amz-error-code",
        )
        if name in response.headers
    }
    error_fields = _safe_s3_error_fields(response)
    return f"response_headers={safe_headers} response_error={error_fields}"


def _safe_s3_error_fields(response: requests.Response) -> dict[str, str]:
    try:
        body = response.text
    except Exception:
        return {}
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return {}
    fields: dict[str, str] = {}
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1]
        if name in {"Code", "Message"} and element.text:
            fields[name] = _clean_error_field(element.text)
    return fields


def _clean_error_field(value: str) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) > ERROR_FIELD_LIMIT:
        return cleaned[:ERROR_FIELD_LIMIT] + "...<truncated>"
    return cleaned


def _safe_exception_summary(exc: requests.RequestException) -> str:
    return type(exc).__name__
