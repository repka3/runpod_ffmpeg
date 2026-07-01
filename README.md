# RunPod FFmpeg Worker

Queue-based RunPod Serverless worker that downloads one HTTP(S) input file, runs `ffmpeg`, uploads the output to an HTTP(S) URL, and returns a structured status.

The worker is intentionally CPU-only. The Docker image uses `python:3.11-slim` and installs Debian's `ffmpeg` package.

## Request

Send a RunPod job with an `input` object:

```json
{
  "input": {
    "source_url": "https://bucket.example/input.mp4?presigned=...",
    "upload_url": "https://bucket.example/output.mp4?presigned=...",
    "ffmpeg_args": ["-i", "{input}", "-c:v", "libx264", "-c:a", "aac", "{output}"],
    "output_name": "output.mp4",
    "upload_method": "PUT",
    "upload_headers": {
      "Content-Type": "video/mp4"
    },
    "timeout_seconds": 3600
  }
}
```

Required fields:

- `source_url`: HTTP(S) URL readable by the worker.
- `upload_url`: HTTP(S) URL writable by the worker, usually a presigned S3/Hetzner URL.
- `ffmpeg_args`: array of ffmpeg arguments. It must include `{input}` and `{output}` placeholders.

Optional fields:

- `input_name`: local input filename, defaults to `input`.
- `output_name`: local output filename, defaults to `output`.
- `upload_method`: `PUT` or `POST`, defaults to `PUT`.
- `upload_headers`: string header map for the upload request.
- `timeout_seconds`: ffmpeg timeout, defaults to `FFMPEG_TIMEOUT_SECONDS` or `3600`.

Raw shell strings are rejected. Pass ffmpeg arguments as an array so the worker can execute `ffmpeg` without a shell.

## Response

Success:

```json
{
  "ok": true,
  "status": "completed",
  "downloaded_bytes": 123,
  "uploaded_bytes": 456,
  "ffmpeg": {
    "returncode": 0,
    "stderr_tail": "..."
  },
  "duration_seconds": 12.345
}
```

Failure:

```json
{
  "ok": false,
  "status": "ffmpeg_failed",
  "error": "ffmpeg exited with status 1",
  "stderr_tail": "...",
  "duration_seconds": 1.234
}
```

Possible failure statuses are `invalid_input`, `timeout`, `ffmpeg_failed`, `transfer_failed`, and `error`.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Run the handler directly with `test_input.json`:

```bash
python -m src.handler
```

Start RunPod's local API server:

```bash
python -m src.handler --rp_serve_api
curl -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d @test_input.json
```

## Calling It Asynchronously

For production, prefer RunPod's async `/run` endpoint so your server does not hold an HTTP connection open while ffmpeg runs.

Submit the job:

```bash
curl -X POST https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/run \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "source_url": "https://bucket.example/input.mp4?presigned=...",
      "upload_url": "https://bucket.example/output.mp4?presigned=...",
      "ffmpeg_args": ["-i", "{input}", "-c:v", "libx264", "-c:a", "aac", "{output}"],
      "output_name": "output.mp4",
      "timeout_seconds": 3600
    },
    "policy": {
      "executionTimeout": 3600000,
      "ttl": 7200000
    }
  }'
```

Poll the job:

```bash
curl https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/status/$JOB_ID \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

The worker emits progress updates for `downloading input`, `running ffmpeg`, `uploading output`, and `completed`. RunPod exposes those updates when polling job status.

Use `/runsync` only for local testing or short files. RunPod async results are retained for a limited time after completion, so your server should persist the returned job ID and poll or reconcile promptly.

## Docker

```bash
docker build --platform linux/amd64 -t runpod-ffmpeg-worker:local .
docker run --rm runpod-ffmpeg-worker:local
```

For a local API server inside Docker:

```bash
docker run --rm -p 8000:8000 runpod-ffmpeg-worker:local \
  python -u -m src.handler --rp_serve_api --rp_api_host 0.0.0.0
```

## Deploy On RunPod

RunPod supports deploying workers from a GitHub repository that contains a working handler and a root `Dockerfile`, or from a pushed Docker image.

For GitHub deployment:

1. Push this repository to GitHub.
2. In RunPod, create a new Serverless endpoint from the repository.
3. Use endpoint type `Queue`.
4. Select CPU resources for the endpoint.
5. Set timeout values high enough for the largest expected ffmpeg job.

The container command is already defined as:

```bash
python -u -m src.handler
```
