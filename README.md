# RunPod FFmpeg Worker

Private RunPod Serverless queue worker for one local FFmpeg transform per job.

The worker downloads one HTTPS media file, probes its duration, runs `ffmpeg` locally with allowlisted transform arguments, uploads the generated file with HTTP PUT, and returns a minimal completion payload. It is intended for CPU RunPod instances.

## Request

Submit jobs to RunPod `/run` and poll `/status/{job_id}`.

```json
{
  "input": {
    "source_url": "https://bucket.example/path/input.mp4?presigned=...",
    "upload_url": "https://bucket.example/path/output.mp3?presigned=...",
    "input_args": ["-ss", "00:30:00"],
    "ffmpeg_args": ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"],
    "upload_headers": {
      "Content-Type": "audio/mpeg"
    }
  }
}
```

Required fields:

- `source_url`: HTTPS URL for the input media file.
- `upload_url`: HTTPS presigned PUT URL for the output file.
- `ffmpeg_args`: JSON array of allowlisted transform/output options.

Optional fields:

- `input_args`: only `["-ss", "<timestamp>"]` in v1.
- `upload_headers`: string header map passed to upload PUT. The worker never logs header values.

The caller does not provide `-i`, local paths, output paths, upload method, output format, timeout, `-progress`, or `-threads`.

## Allowed Arguments

`input_args` is limited to:

- `-ss <timestamp>`

`timestamp` may be seconds, such as `120` or `120.5`, or `HH:MM:SS`, such as `00:02:00`.

`ffmpeg_args` supports these option names:

- boolean flags: `-vn`, `-an`, `-sn`, `-dn`
- codecs: `-c:a`, `-codec:a`, `-acodec`, `-c:v`, `-codec:v`, `-vcodec`
- bitrates/rate control: `-b:a`, `-b:v`, `-maxrate`, `-bufsize`
- numeric options: `-crf`, `-r`, `-ar`, `-ac`
- video filters: `-vf`, `-filter:v`
- clipping/output timing: `-t`, `-to`
- stream selection: `-map`
- MP4 playback: `-movflags +faststart`
- video preset: `-preset`

Allowed codecs are `libmp3lame`, `aac`, `pcm_s16le`, `flac`, `libopus`, `libx264`, and `libx265`. Codec `copy` is not allowed.

Allowed presets are `ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, and `veryslow`.

Allowed top-level video filters are `scale`, `fps`, `crop`, `pad`, `transpose`, `setsar`, and `format`. Filter strings are intentionally conservative; filters that read files, network URLs, subtitles, or external media are rejected.

`-map` only supports selectors from the single input, such as `0`, `0:a`, `0:v`, `0:s`, `0:a:0`, and `0:v:0`.

Worker-owned or unsupported options are rejected, including `-i`, `-y`, `-n`, `-loglevel`, `-hide_banner`, `-nostdin`, `-progress`, `-threads`, and `-f`.

## Examples

MP3 extraction:

```json
{
  "input": {
    "source_url": "https://bucket.example/meeting.mp4?presigned=...",
    "upload_url": "https://bucket.example/audio.mp3?presigned=...",
    "ffmpeg_args": ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"],
    "upload_headers": {"Content-Type": "audio/mpeg"}
  }
}
```

WAV transcription prep:

```json
{
  "input": {
    "source_url": "https://bucket.example/meeting.mp4?presigned=...",
    "upload_url": "https://bucket.example/audio.wav?presigned=...",
    "ffmpeg_args": ["-vn", "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1"],
    "upload_headers": {"Content-Type": "audio/wav"}
  }
}
```

Meeting video compression:

```json
{
  "input": {
    "source_url": "https://bucket.example/meeting.mp4?presigned=...",
    "upload_url": "https://bucket.example/meeting-compressed.mp4?presigned=...",
    "ffmpeg_args": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-vf", "scale=1280:-2,fps=15", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"],
    "upload_headers": {"Content-Type": "video/mp4"}
  }
}
```

Clip creation:

```json
{
  "input": {
    "source_url": "https://bucket.example/meeting.mp4?presigned=...",
    "upload_url": "https://bucket.example/clip.mp4?presigned=...",
    "input_args": ["-ss", "00:30:00"],
    "ffmpeg_args": ["-t", "120", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
    "upload_headers": {"Content-Type": "video/mp4"}
  }
}
```

## Progress And Result

Progress updates use this shape:

```json
{
  "phase": "running_ffmpeg",
  "percent": 42.5,
  "progress_available": true,
  "duration_seconds": 312.4
}
```

Non-FFmpeg phases use `percent: null` and `progress_available: false`.

Phases are `downloading`, `probing`, `running_ffmpeg`, `uploading`, and `done`.

Success returns:

```json
{
  "phase": "done",
  "media_duration_seconds": 3600.123,
  "duration_seconds": 123.4
}
```

`media_duration_seconds` is the probed duration of the generated output media file. `duration_seconds` is worker wall-clock runtime.

Failures return a stable payload:

```json
{
  "phase": "failed",
  "failed_phase": "uploading",
  "error_prefix": "UPLOAD_FAILED",
  "message": "UPLOAD_FAILED: HTTP 403 while uploading https://bucket.example/path/output.mp3",
  "duration_seconds": 123.4
}
```

Stable prefixes are `INVALID_INPUT`, `DOWNLOAD_FAILED`, `FFPROBE_FAILED`, `FFMPEG_FAILED`, `UPLOAD_FAILED`, `LIMIT_EXCEEDED`, and `WORKER_FAILED`.

## RunPod Settings

Recommended request policy:

```json
{
  "policy": {
    "executionTimeout": 4200000,
    "ttl": 7200000
  }
}
```

Use presigned URLs that stay valid for queue delay plus execution time. For production jobs, include enough validity for one RunPod retry, because queue workers may retry a job if RunPod does not persist the final result cleanly. Provision at least 12 GB writable temp disk per concurrent job. RunPod async results are retained for a limited time after completion, so persist the final outcome promptly.

## S3 Presigned PUT

Generate S3 PUT URLs against the bucket's regional endpoint. A URL signed for the global `s3.amazonaws.com` host can fail with `307 TemporaryRedirect`; the worker intentionally does not follow upload redirects.

The `Content-Type` header passed to the worker must match the `ContentType` value used when generating the presigned PUT URL. A malformed, stale, copied incorrectly, or wrong-session S3 presign can fail with `HTTP 400 InvalidToken` even when the bucket path and filename are correct.

```bash
AWS_REGION=eu-central-1 \
S3_BUCKET=your-bucket-name \
S3_KEY=path/output.mp3 \
S3_CONTENT_TYPE=audio/mpeg \
python3 - <<'PY'
import os
import boto3
from botocore.config import Config

region = os.environ["AWS_REGION"]
bucket = os.environ["S3_BUCKET"]
key = os.environ["S3_KEY"]
content_type = os.environ["S3_CONTENT_TYPE"]

s3 = boto3.client(
    "s3",
    region_name=region,
    endpoint_url=f"https://s3.{region}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)

print(
    s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=7200,
        HttpMethod="PUT",
    )
)
PY
```

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Run local handler mode:

```bash
python -u -m src.handler
```

RunPod local API mode:

```bash
python -u -m src.handler --rp_serve_api
```

## Deploy

The primary deployment path is RunPod GitHub source build:

1. Push this repository to GitHub.
2. Create a RunPod Serverless queue endpoint from the repository.
3. Choose CPU resources.
4. Set the endpoint or request execution timeout to about 70 minutes.
5. Ensure the root `Dockerfile` is used.

The container command is:

```bash
python -u -m src.handler
```

## Smoke Test

After deployment:

```bash
RUNPOD_API_KEY=... python3 scripts/smoke_test.py \
  --endpoint-id "$RUNPOD_ENDPOINT_ID" \
  --source-url "https://..." \
  --upload-url "https://..." \
  --input-args '["-ss", "00:30:00"]' \
  --ffmpeg-args '["-vn", "-c:a", "libmp3lame", "-b:a", "128k"]' \
  --upload-headers-json '{"Content-Type":"audio/mpeg"}'
```

The script submits with `/run`, polls every 5 seconds by default, and exits non-zero on failure or timeout.

For cheap validation, first smoke test with a small media file and fresh presigned GET/PUT URLs. Do not reuse old presigned URLs from logs, chat, or previous failed runs; generate a new PUT URL for the exact output key and content type you are testing.

Expected success shape:

```json
{
  "phase": "done",
  "media_duration_seconds": 12.345,
  "duration_seconds": 4.969
}
```

Validated deployed examples:

- small MP3 to MP3 completed with `phase: "done"`.
- large MP4 to MP3 completed with `phase: "done"` and uploaded a playable MP3.
