from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import urlparse

from .errors import InvalidInput


ALLOWED_INPUT_EXTENSIONS = {
    "mp4",
    "mov",
    "m4v",
    "mkv",
    "webm",
    "avi",
    "mp3",
    "wav",
    "m4a",
    "aac",
    "flac",
    "ogg",
    "opus",
}
ALLOWED_OUTPUT_EXTENSIONS = {
    "mp4",
    "mov",
    "m4v",
    "webm",
    "mp3",
    "wav",
    "m4a",
    "aac",
    "flac",
    "ogg",
    "opus",
}

BLOCKED_UPLOAD_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
}

BOOLEAN_OPTIONS = {"-vn", "-an", "-sn", "-dn"}
CODEC_OPTIONS = {"-c:a", "-codec:a", "-acodec", "-c:v", "-codec:v", "-vcodec"}
BITRATE_OPTIONS = {"-b:a", "-b:v", "-maxrate", "-bufsize"}
NUMERIC_OPTIONS = {"-crf", "-r", "-ar", "-ac"}
FILTER_OPTIONS = {"-vf", "-filter:v"}
VALUE_OPTIONS = (
    CODEC_OPTIONS
    | BITRATE_OPTIONS
    | NUMERIC_OPTIONS
    | FILTER_OPTIONS
    | {"-t", "-to", "-preset", "-map", "-movflags"}
)
ALLOWED_CODECS = {"libmp3lame", "aac", "pcm_s16le", "flac", "libopus", "libx264", "libx265"}
ALLOWED_PRESETS = {
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
}
ALLOWED_FILTERS = {"scale", "fps", "crop", "pad", "transpose", "setsar", "format"}
BLOCKED_FILTERS = {"movie", "amovie", "subtitles", "ass", "frei0r"}
BLOCKED_PROTOCOL_MARKERS = (
    "http:",
    "https:",
    "file:",
    "pipe:",
    "ftp:",
    "tcp:",
    "udp:",
    "rtmp:",
    "data:",
    "concat:",
    "subfile:",
    "crypto:",
)

TIMESTAMP_RE = re.compile(r"^(?:\d+(?:\.\d+)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?)$")
BITRATE_RE = re.compile(r"^[1-9]\d*(?:\.\d+)?[kKmMgG]?$")
MAP_RE = re.compile(r"^0(?::[vas](?::\d+)?)?$")
STREAM_INDEXED_RE = re.compile(r"^-[A-Za-z0-9]+(?::[A-Za-z])?:\d+$")


@dataclass(frozen=True)
class JobConfig:
    source_url: str
    upload_url: str
    input_args: list[str]
    ffmpeg_args: list[str]
    upload_headers: dict[str, str]
    input_filename: str
    output_filename: str
    input_seek_seconds: float | None
    clip_t_seconds: float | None
    clip_to_seconds: float | None


def validate_job(job: dict[str, Any]) -> JobConfig:
    if not isinstance(job, dict):
        raise InvalidInput("job must be an object")
    job_input = job.get("input")
    if not isinstance(job_input, dict):
        raise InvalidInput("job.input must be an object")

    source_url = _required_string(job_input, "source_url")
    upload_url = _required_string(job_input, "upload_url")
    ffmpeg_args = _string_list(job_input.get("ffmpeg_args"), "ffmpeg_args", required=True)
    input_args = _string_list(job_input.get("input_args", []), "input_args", required=False)
    upload_headers = _upload_headers(job_input.get("upload_headers", {}))

    _validate_https_url(source_url, "source_url")
    _validate_https_url(upload_url, "upload_url")
    source_ext = derive_extension(source_url, ALLOWED_INPUT_EXTENSIONS, "source_url")
    upload_ext = derive_extension(upload_url, ALLOWED_OUTPUT_EXTENSIONS, "upload_url")

    input_seek_seconds = _validate_input_args(input_args)
    clip_t_seconds, clip_to_seconds = _validate_ffmpeg_args(ffmpeg_args)
    if clip_to_seconds is not None and input_seek_seconds is not None and clip_to_seconds <= input_seek_seconds:
        raise InvalidInput("-to must be greater than input_args -ss")

    return JobConfig(
        source_url=source_url,
        upload_url=upload_url,
        input_args=input_args,
        ffmpeg_args=ffmpeg_args,
        upload_headers=upload_headers,
        input_filename=f"input.{source_ext}",
        output_filename=f"output.{upload_ext}",
        input_seek_seconds=input_seek_seconds,
        clip_t_seconds=clip_t_seconds,
        clip_to_seconds=clip_to_seconds,
    )


def derive_extension(url: str, allowed: set[str], field: str) -> str:
    parsed = urlparse(url)
    path = PurePosixPath(parsed.path)
    name = path.name
    if not name or "." not in name:
        raise InvalidInput(f"{field} URL path must end with a filename extension")
    ext = name.rsplit(".", 1)[1].lower()
    if ext not in allowed:
        raise InvalidInput(f"{field} extension is not supported: {ext}")
    return ext


def parse_timestamp(value: str, field: str = "timestamp") -> float:
    if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
        raise InvalidInput(f"{field} must be seconds or HH:MM:SS")
    if ":" not in value:
        seconds = float(value)
    else:
        hours, minutes, seconds_part = value.split(":")
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds_part)
    if seconds < 0:
        raise InvalidInput(f"{field} must not be negative")
    return seconds


def redacted_url(url: str) -> str:
    parsed = urlparse(url)
    redacted = parsed._replace(query="", fragment="")
    return redacted.geturl()


def _required_string(job_input: dict[str, Any], field: str) -> str:
    value = job_input.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidInput(f"{field} must be a non-empty string")
    if _has_control(value):
        raise InvalidInput(f"{field} contains control characters")
    return value


def _string_list(value: Any, field: str, *, required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise InvalidInput(f"{field} must be an array of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            raise InvalidInput(f"{field}[{index}] must be a non-empty string")
        if _has_control(item):
            raise InvalidInput(f"{field}[{index}] contains control characters")
        items.append(item)
    return items


def _validate_https_url(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise InvalidInput(f"{field} must be https")


def _upload_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidInput("upload_headers must be an object")
    headers: dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise InvalidInput("upload_headers names and values must be strings")
        if not name:
            raise InvalidInput("upload_headers names must be non-empty")
        if name.lower() in BLOCKED_UPLOAD_HEADERS:
            raise InvalidInput(f"upload_headers may not include {name}")
        if _has_control(name) or _has_control(header_value):
            raise InvalidInput("upload_headers may not contain control characters")
        headers[name] = header_value
    return headers


def _validate_input_args(args: list[str]) -> float | None:
    if not args:
        return None
    if len(args) != 2 or args[0] != "-ss":
        raise InvalidInput("input_args only supports -ss <timestamp>")
    return parse_timestamp(args[1], "-ss")


def _validate_ffmpeg_args(args: list[str]) -> tuple[float | None, float | None]:
    clip_t_seconds: float | None = None
    clip_to_seconds: float | None = None
    index = 0
    while index < len(args):
        option = args[index]
        if not option.startswith("-"):
            raise InvalidInput(f"stray positional token is not allowed: {option}")
        if STREAM_INDEXED_RE.match(option):
            raise InvalidInput(f"stream-index-qualified option is not supported: {option}")
        if option in {"-i", "-y", "-n", "-loglevel", "-hide_banner", "-nostdin", "-progress", "-threads", "-f"}:
            raise InvalidInput(f"{option} is worker-owned or unsupported")
        if option in BOOLEAN_OPTIONS:
            index += 1
            continue
        if option not in VALUE_OPTIONS:
            raise InvalidInput(f"unsupported ffmpeg option: {option}")
        if index + 1 >= len(args):
            raise InvalidInput(f"{option} requires a value")
        value = args[index + 1]
        if value.startswith("-"):
            raise InvalidInput(f"{option} requires a value")
        _validate_option_value(option, value)
        if option == "-t":
            if clip_to_seconds is not None:
                raise InvalidInput("-t and -to are mutually exclusive")
            clip_t_seconds = parse_timestamp(value, "-t")
        elif option == "-to":
            if clip_t_seconds is not None:
                raise InvalidInput("-t and -to are mutually exclusive")
            clip_to_seconds = parse_timestamp(value, "-to")
        index += 2
    return clip_t_seconds, clip_to_seconds


def _validate_option_value(option: str, value: str) -> None:
    if option in CODEC_OPTIONS:
        if value == "copy":
            raise InvalidInput("copy codecs are not allowed")
        if value not in ALLOWED_CODECS:
            raise InvalidInput(f"unsupported codec: {value}")
    elif option in BITRATE_OPTIONS:
        if not BITRATE_RE.match(value):
            raise InvalidInput(f"{option} must be a positive bitrate")
    elif option == "-crf":
        _int_range(value, option, 0, 51)
    elif option == "-r":
        _float_range(value, option, 1, 120)
    elif option == "-ar":
        _int_range(value, option, 8000, 192000)
    elif option == "-ac":
        _int_range(value, option, 1, 8)
    elif option == "-preset":
        if value not in ALLOWED_PRESETS:
            raise InvalidInput(f"unsupported preset: {value}")
    elif option in FILTER_OPTIONS:
        _validate_filter(value)
    elif option == "-map":
        if not MAP_RE.match(value):
            raise InvalidInput(f"unsupported -map selector: {value}")
    elif option == "-movflags":
        if value != "+faststart":
            raise InvalidInput("-movflags only supports +faststart")
    elif option in {"-t", "-to"}:
        parse_timestamp(value, option)


def _validate_filter(value: str) -> None:
    if len(value) > 500:
        raise InvalidInput("filter string is too long")
    lowered = value.lower()
    if ";" in value or "`" in value or _has_control(value):
        raise InvalidInput("filter string contains unsafe characters")
    for marker in BLOCKED_PROTOCOL_MARKERS:
        if marker in lowered:
            raise InvalidInput(f"filter string contains blocked protocol marker: {marker}")
    for part in value.split(","):
        name = part.strip().split("=", 1)[0].split(":", 1)[0].strip().lower()
        if not name:
            raise InvalidInput("filter string contains an empty filter")
        if name in BLOCKED_FILTERS or (name == "drawtext" and "textfile=" in lowered):
            raise InvalidInput(f"filter is not allowed: {name}")
        if name not in ALLOWED_FILTERS:
            raise InvalidInput(f"filter is not allowed: {name}")


def _int_range(value: str, option: str, minimum: int, maximum: int) -> None:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InvalidInput(f"{option} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise InvalidInput(f"{option} must be between {minimum} and {maximum}")


def _float_range(value: str, option: str, minimum: float, maximum: float) -> None:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise InvalidInput(f"{option} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise InvalidInput(f"{option} must be between {minimum:g} and {maximum:g}")


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)
