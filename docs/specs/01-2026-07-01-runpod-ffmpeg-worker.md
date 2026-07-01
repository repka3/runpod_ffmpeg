# RunPod FFmpeg Serverless Worker Specs

## Goal

Create a private RunPod Serverless queue worker that:

1. Receives one source media URL and one upload URL.
2. Downloads the source file locally.
3. Runs `ffprobe` to get duration.
4. Runs `ffmpeg` locally with allowlisted caller-provided transform options.
5. Uploads the generated output file with HTTP PUT.
6. Reports progress through RunPod status updates.
7. Returns a minimal success payload or raises a stage-specific error.

The worker is intended for CPU instances. Main workflows are audio extraction/conversion for transcription, video/audio to MP3 or WAV, meeting-video compression, and meeting clip creation.

## Deployment Model

- Primary deployment path: RunPod GitHub source build.
- The repository must contain a root `Dockerfile`.
- RunPod builds the image from GitHub and deploys it manually.
- Auto-deploy can be enabled later, but v1 assumes manual rebuild/deploy.
- Local Docker builds remain useful for development, but are not the primary deployment path.
- Recommended RunPod queue request policy:
  - `executionTimeout`: 70 minutes.
  - `ttl`: long enough to cover queue delay plus execution.
- The worker ffmpeg timeout is fixed at 1 hour.
- Caller should use RunPod `/run`, then poll `/status/{job_id}`. `/runsync` is only for local/short tests.
- RunPod async results are retained for a limited time after completion, so the caller should persist the final outcome promptly.

## Request Contract

RunPod job input:

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

Required:

- `source_url`: HTTPS URL for the input media file.
- `upload_url`: HTTPS presigned PUT URL for the output file.
- `ffmpeg_args`: JSON array of strings containing transform/output options only.

Optional:

- `input_args`: JSON array of strings for pre-input ffmpeg options. In v1, only `-ss <timestamp>` is allowed.
- `upload_headers`: object of string header names to string values for the upload PUT request.

Not supported in v1:

- Multiple input files.
- Multiple output files.
- Raw shell command strings.
- Caller-provided local filenames.
- Caller-provided upload method.
- Caller-provided timeout.
- Caller-provided output format field.
- Direct S3 credentials.
- Webhooks implemented inside the worker.

## URL Rules

- URLs must use `https://`.
- Filename extension is derived from the URL path only.
- Query string and fragment are ignored for filename/extension derivation.
- If either URL path does not end in a filename with an extension, the job fails with `INVALID_INPUT`.
- Local input filename is `input.<source_ext>`.
- Local output filename is `output.<upload_ext>`.
- Logs may include scheme, host, and path, but must redact query string and fragment.
- Downloads may follow redirects only if the final URL remains HTTPS.
- Uploads must not follow redirects.

Allowed input extensions:

```txt
mp4, mov, m4v, mkv, webm, avi, mp3, wav, m4a, aac, flac, ogg, opus
```

Allowed output extensions:

```txt
mp4, mov, m4v, webm, mp3, wav, m4a, aac, flac, ogg, opus
```

## Transfer Rules

- Download source with streaming HTTP GET.
- Upload output with streaming HTTP PUT.
- Maximum downloaded input size: 5 GB.
- Maximum uploaded output size: 5 GB.
- If input download exceeds 5 GB, fail with `LIMIT_EXCEEDED`.
- If generated output exceeds 5 GB, fail before upload with `LIMIT_EXCEEDED`.
- `upload_headers` are passed only to the upload request.
- The worker does not add `Content-Type` or other headers automatically.
- Reject upload headers named:
  - `Host`
  - `Content-Length`
  - `Transfer-Encoding`
  - `Connection`

## FFmpeg Command Shape

The caller provides only transform options. The worker owns input/output placement and operational flags.

Command shape:

```bash
ffmpeg -hide_banner -nostdin -y <input_args> -i input.ext <progress flags> <ffmpeg_args> output.ext
```

The worker owns:

- `-hide_banner`
- `-nostdin`
- `-y`
- progress flags
- local input path
- local output path
- timeout enforcement
- logging behavior

The caller must not pass:

- `-i`
- output path
- `-y`
- `-n`
- `-loglevel`
- `-hide_banner`
- `-nostdin`
- `-progress`
- `-threads`
- network/protocol inputs
- raw shell syntax

No shell is used to execute ffmpeg.

## FFprobe

- After download, run `ffprobe` on the local input file.
- `ffprobe` must return a positive numeric duration.
- If `ffprobe` fails or duration is missing/zero/non-numeric, fail with `FFPROBE_FAILED`.
- Log duration in human-readable `HH:MM:SS` format.
- Duration is used to estimate ffmpeg progress.

## Allowed FFmpeg Arguments

Validation should block obvious mistakes and unsafe shapes, not reimplement ffmpeg.

### `input_args`

Allowed:

- `-ss <timestamp>`

Timestamp values:

- seconds: `90`, `90.5`
- clock form: `00:01:30`, `00:01:30.500`

### `ffmpeg_args`

Allowed audio options:

```txt
-vn
-an
-c:a
-codec:a
-acodec
-b:a
-ar
-ac
```

Allowed video/compression options:

```txt
-c:v
-codec:v
-vcodec
-crf
-preset
-b:v
-maxrate
-bufsize
-r
-vf
-filter:v
```

Allowed stream/subtitle/data options:

```txt
-map
-sn
-dn
```

Allowed MP4 playback option:

```txt
-movflags +faststart
```

Not allowed in v1:

- `-f`
- `-c copy`
- `-codec copy`
- `-acodec copy`
- `-vcodec copy`
- arbitrary metadata editing
- arbitrary protocol/file/network options

Allowed codecs:

```txt
libmp3lame
aac
pcm_s16le
flac
libopus
libx264
libx265
```

Allowed presets:

```txt
ultrafast
superfast
veryfast
faster
fast
medium
slow
slower
veryslow
```

Numeric validation:

- `-crf`: integer `0-51`.
- `-r`: number from `1` to `120`.
- `-ar`: integer from `8000` to `192000`.
- `-ac`: integer from `1` to `8`.

Bitrate-like validation for `-b:a`, `-b:v`, `-maxrate`, `-bufsize`:

- Positive number with optional ffmpeg-style unit, e.g. `96k`, `128k`, `1M`, `2500k`.

Filter string validation for `-vf` and `-filter:v`:

- Maximum 500 characters.
- Reject semicolon.
- Reject backticks.
- Reject newline/control characters.
- Reject URL/protocol-looking values containing:
  - `http://`
  - `https://`
  - `file:`
  - `pipe:`

`-map` validation:

- Allow only conservative single-input stream selectors such as:
  - `0`
  - `0:v`
  - `0:v:0`
  - `0:a`
  - `0:a:0`
  - `0:s`
  - `0:s:0`

## Progress Contract

The worker sends RunPod progress updates with this shape:

```json
{
  "phase": "running_ffmpeg",
  "percent": 42.5,
  "progress_available": true,
  "duration_seconds": 312.4
}
```

For phases without percent:

```json
{
  "phase": "downloading",
  "percent": null,
  "progress_available": false,
  "duration_seconds": 1.2
}
```

Phases:

- `downloading`
- `probing`
- `running_ffmpeg`
- `uploading`
- `done`

Progress behavior:

- Parse ffmpeg progress continuously using ffmpeg progress output.
- Estimate percent as `out_time_ms / input_duration_ms * 100`.
- Clamp percent to `0-100`.
- Forward progress to RunPod at most once every 2 seconds when percent changes.
- The forwarded percent is the current estimate, rounded to one decimal.
- RunPod logs should log ffmpeg progress only at 10% buckets to avoid noise.
- External systems may poll RunPod `/status` at whatever cadence they choose and receive the latest forwarded progress.

## Success Response

On success, return:

```json
{
  "phase": "done",
  "duration_seconds": 123.4
}
```

No presigned URLs are returned.
No byte counts are required in the final response.
Operational detail belongs in RunPod logs.

## Error Contract

Anything wrong raises an exception. RunPod marks the job failed.

Raised error messages must use stable prefixes:

```txt
INVALID_INPUT
DOWNLOAD_FAILED
FFPROBE_FAILED
FFMPEG_FAILED
UPLOAD_FAILED
LIMIT_EXCEEDED
```

Examples:

```txt
INVALID_INPUT: source_url must be https
DOWNLOAD_FAILED: HTTP 403 while downloading https://bucket.example/path/input.mp4
FFPROBE_FAILED: duration is missing or not positive
FFMPEG_FAILED: exit code 1: <stderr tail>
UPLOAD_FAILED: HTTP 403 while uploading https://bucket.example/path/output.mp3
LIMIT_EXCEEDED: input exceeded 5 GB
```

FFmpeg failures should include a capped stderr tail, max 4000 characters.

Logs may include more detailed operational context, but must not include presigned query strings or fragments.

## Cleanup

- Use a per-job temporary directory.
- Cleanup is best effort.
- Cleanup failure must not turn a confirmed successful upload into a failed job.

## Tests

Include focused unit tests for worker-owned behavior:

- request validation
- URL extension derivation
- upload header validation
- allowlist validation
- unsafe filter rejection
- command construction
- size-limit failures
- ffprobe duration handling
- ffmpeg failure error formatting

Automated tests do not need real bucket/network calls.

Include a deployed smoke-test script:

```bash
python scripts/smoke_test.py \
  --endpoint-id "$RUNPOD_ENDPOINT_ID" \
  --api-key "$RUNPOD_API_KEY" \
  --source-url "https://..." \
  --upload-url "https://..." \
  --ffmpeg-args '["-vn", "-c:a", "libmp3lame", "-b:a", "128k"]'
```

The smoke test should:

1. Submit with RunPod `/run`.
2. Poll `/status/{job_id}` every 5 seconds.
3. Print progress/status.
4. Exit `0` on successful completion.
5. Exit non-zero on failure, timeout, or malformed response.

