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
  "duration_seconds": 123.4
}
```

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

Use presigned URLs that stay valid for queue delay plus execution time. Provision at least 12 GB writable temp disk per concurrent job. RunPod async results are retained for a limited time after completion, so persist the final outcome promptly.

## S3 Presigned PUT

Generate S3 PUT URLs against the bucket's regional endpoint. A URL signed for the global `s3.amazonaws.com` host can fail with `307 TemporaryRedirect`; the worker intentionally does not follow upload redirects.

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
RUNPOD_API_KEY=... python scripts/smoke_test.py \
  --endpoint-id "$RUNPOD_ENDPOINT_ID" \
  --source-url "https://..." \
  --upload-url "https://..." \
  --input-args '["-ss", "00:30:00"]' \
  --ffmpeg-args '["-vn", "-c:a", "libmp3lame", "-b:a", "128k"]' \
  --upload-headers-json '{"Content-Type":"audio/mpeg"}'
```

The script submits with `/run`, polls every 5 seconds by default, and exits non-zero on failure or timeout.
