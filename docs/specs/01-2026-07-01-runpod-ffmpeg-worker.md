# RunPod FFmpeg Serverless Worker Specs

## Goal

Create a private RunPod Serverless queue worker that:

1. Receives one source media URL and one upload URL.
2. Downloads the source file locally.
3. Runs `ffprobe` to get source duration.
4. Runs `ffmpeg` locally with allowlisted caller-provided transform options.
5. Runs `ffprobe` on the generated output file to get output media duration.
6. Uploads the generated output file with HTTP PUT.
7. Reports progress through RunPod status updates.
8. Returns a minimal success payload or a stable failed payload.

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
- Presigned source and upload URLs must remain valid for the full queue delay plus execution window. In practice, sign them for longer than the configured RunPod `ttl`.
- The endpoint should be provisioned with enough temporary disk for at least the maximum input plus maximum output plus container/runtime overhead. V1 requires at least 12 GB of writable temp space per concurrent job.

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

All request validation failures are reported with `INVALID_INPUT`.

## URL Rules

- URLs must use `https://`.
- Filename extension is derived from the URL path only.
- Query string and fragment are ignored for filename/extension derivation.
- If either URL path does not end in a filename with an extension, the job fails with `INVALID_INPUT`.
- If either derived extension is not in the corresponding allowed extension list, the job fails with `INVALID_INPUT`.
- Local input filename is `input.<source_ext>`.
- Local output filename is `output.<upload_ext>`.
- Logs may include scheme, host, and path, but must redact query string and fragment.
- Downloads may follow redirects only if every redirect hop and the final URL remain HTTPS.
- Downloads should cap redirects at a small fixed number, such as 5.
- Uploads must not follow redirects. Any upload `3xx` response is `UPLOAD_FAILED`.

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
- Enforce the input limit by counting streamed bytes while downloading. Do not trust `Content-Length` alone.
- If `Content-Length` is present and already exceeds 5 GB, fail before downloading with `LIMIT_EXCEEDED`.
- If input download exceeds 5 GB while streaming, abort the transfer and fail with `LIMIT_EXCEEDED`.
- If generated output exceeds 5 GB, fail before upload with `LIMIT_EXCEEDED`.
- The worker should use bounded transfer timeouts:
  - connect timeout: 10 seconds.
  - read/write stall timeout: 60 seconds.
- A transfer timeout fails with `DOWNLOAD_FAILED` or `UPLOAD_FAILED`, depending on the stage.
- Download success statuses are `200 OK` and `206 Partial Content`.
- Upload success statuses are `200 OK`, `201 Created`, and `204 No Content`.
- Any other transfer status is a stage-specific failure.
- `upload_headers` are passed only to the upload request.
- The worker does not add `Content-Type` or other headers automatically.
- The worker may set HTTP mechanics headers required for a correct upload, such as `Content-Length`, from the known output file size.
- Caller-provided upload header names are matched case-insensitively.
- Reject caller-provided upload headers named:
  - `Host`
  - `Content-Length`
  - `Transfer-Encoding`
  - `Connection`
- Reject upload header names or values containing newline/control characters.
- Never log upload header values.

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
The worker does not set `-threads` in v1; ffmpeg and the selected encoder use their defaults.

## FFprobe

- After download, run `ffprobe` on the local input file.
- Source `ffprobe` must return a positive numeric duration.
- Source duration is used to estimate ffmpeg progress.
- After `ffmpeg` completes, run `ffprobe` on the generated output file.
- Output `ffprobe` must return a positive numeric duration.
- Return the output media duration as `media_duration_seconds` in the success payload.
- If either `ffprobe` fails or duration is missing/zero/non-numeric, fail with `FFPROBE_FAILED`.
- Log durations in human-readable `HH:MM:SS` format.
- This is a deliberate v1 tradeoff: files without a usable media duration may still be transcodable by ffmpeg, but v1 rejects them so progress and clipping behavior stay predictable.

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

Allowed clipping options:

```txt
-t
-to
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
- Additional filter-specific safety rules are listed below.

`-map` validation:

- Allow only conservative single-input stream selectors such as:
  - `0`
  - `0:v`
  - `0:v:0`
  - `0:a`
  - `0:a:0`
  - `0:s`
  - `0:s:0`

Argument parsing rules:

- Options are parsed left to right.
- Any unrecognized flag fails with `INVALID_INPUT`.
- Any stray positional token fails with `INVALID_INPUT`.
- Any option missing its required value fails with `INVALID_INPUT`.
- Boolean flags consume no value:
  - `-vn`
  - `-an`
  - `-sn`
  - `-dn`
- Value options consume exactly one following token:
  - `-ss`
  - `-t`
  - `-to`
  - codec options
  - bitrate options
  - numeric options
  - filter options
  - `-map`
  - `-movflags`
- Stream-index-qualified option names such as `-c:a:0` and `-b:a:1` are not supported in v1.
- `-t` and `-to` use the same timestamp validation as `-ss`.
- A request must not include both `-t` and `-to`; choose one clipping duration/end model.
- For clipping, use `input_args: ["-ss", "..."]` plus `ffmpeg_args: ["-t", "...", ...]` or `ffmpeg_args: ["-to", "...", ...]`.

Filter safety rules:

- V1 allows only simple video filter strings intended for scaling and basic video transforms. This is not a full ffmpeg filter parser.
- Allowed top-level video filter names:
  - `scale`
  - `fps`
  - `crop`
  - `pad`
  - `transpose`
  - `setsar`
  - `format`
- Reject any top-level filter name outside that allowlist.
- Reject filters known to read files, network resources, or external plugins, including:
  - `movie`
  - `amovie`
  - `subtitles`
  - `ass`
  - `drawtext` when it contains `textfile=`
  - `frei0r`
- Reject protocol-looking values containing:
  - `http:`
  - `https:`
  - `file:`
  - `pipe:`
  - `ftp:`
  - `tcp:`
  - `udp:`
  - `rtmp:`
  - `data:`
  - `concat:`
  - `subfile:`
  - `crypto:`

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

- `duration_seconds` in progress payloads always means elapsed wall-clock time since the worker started the job. It does not mean media duration.
- Progress updates are best-effort telemetry. A failure to send a progress update must be logged, but must not change the final `done` or `failed` result of the job.
- Parse ffmpeg progress continuously using ffmpeg progress output.
- Parse `out_time_us` when ffmpeg emits it. Fallback to parsing `out_time` as `HH:MM:SS.microseconds`. Do not assume `out_time_ms` means milliseconds without an explicit implementation test for the installed ffmpeg version.
- Convert ffmpeg progress time to seconds before calculating percent.
- Estimate percent as `processed_media_seconds / expected_output_media_seconds * 100`.
- `expected_output_media_seconds` is:
  - probed media duration when no clipping duration/end is provided.
  - requested `-t` duration when `-t` is provided.
  - requested `-to` minus input `-ss` offset when both are provided.
  - requested `-to` when `-to` is provided without input `-ss`.
  - probed media duration minus input `-ss` offset when only input `-ss` is provided.
- Clamp percent to `0-100`.
- Percent should be monotonic for a single job; never send a lower percent than the last forwarded percent.
- Forward progress to RunPod at most once every 2 seconds when percent changes.
- The forwarded percent is the current estimate, rounded to one decimal.
- RunPod logs should log ffmpeg progress only at 10% buckets to avoid noise.
- Send a final `running_ffmpeg` progress update with `percent: 100.0` before moving to `uploading`, if ffmpeg completed successfully.
- External systems may poll RunPod `/status` at whatever cadence they choose and receive the latest forwarded progress.

## Success Response

On success, return:

```json
{
  "phase": "done",
  "media_duration_seconds": 3600.123,
  "duration_seconds": 123.4
}
```

`duration_seconds` in the success response means total elapsed wall-clock time for the worker job.
`media_duration_seconds` means the probed duration of the generated output media file.

No presigned URLs are returned.
No byte counts are required in the final response.
Operational detail belongs in RunPod logs.

## Error Contract

Stage code may use exceptions internally, but the worker boundary must catch all exceptions and return a stable failure payload. `process_job` is the primary boundary for normal worker execution, and `handler` has an additional outer catch as a backstop so unexpected leaks still become `phase: "failed"`. Expected worker failures must not escape to RunPod as top-level handler exceptions, because queue-based RunPod endpoints may retry failed jobs.

This contract covers every Python-level exception that reaches the handler path. Hard platform failures where Python cannot continue running, such as container kill, host loss, or import failure before the handler starts, must still be treated as failures by the caller from RunPod's own terminal status.

On failure, return:

```json
{
  "phase": "failed",
  "failed_phase": "uploading",
  "error_prefix": "UPLOAD_FAILED",
  "message": "UPLOAD_FAILED: HTTP 403 while uploading https://bucket.example/path/output.mp3",
  "duration_seconds": 123.4
}
```

The response must not use a top-level key named `error`, because the RunPod SDK treats that as a failed job result rather than a normal output payload.

For known worker errors, `message` may include safe operational detail with redacted URLs.
For unexpected Python exceptions, return `error_prefix: "WORKER_FAILED"` and a generic message that points to RunPod logs rather than exposing raw exception text.

Known worker errors must use stable prefixes. Unexpected Python exceptions that are caught at the worker boundary use `WORKER_FAILED`.

```txt
INVALID_INPUT
DOWNLOAD_FAILED
FFPROBE_FAILED
FFMPEG_FAILED
UPLOAD_FAILED
LIMIT_EXCEEDED
WORKER_FAILED
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

FFmpeg failures should include a capped stderr tail in the failure message and logs, max 4000 characters.
FFmpeg timeout fails as `FFMPEG_FAILED`.
Out-of-disk errors fail with the prefix for the active stage:

- during download: `DOWNLOAD_FAILED`.
- during ffmpeg/output write: `FFMPEG_FAILED`.
- during upload: `UPLOAD_FAILED`.

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
- input and output size-limit failures
- ffprobe duration handling
- ffmpeg failure error formatting
- ffmpeg progress unit parsing
- clipping progress denominator with `-ss`, `-t`, and `-to`

Automated tests do not need real bucket/network calls.

Include a deployed smoke-test script:

```bash
python scripts/smoke_test.py \
  --endpoint-id "$RUNPOD_ENDPOINT_ID" \
  --source-url "https://..." \
  --upload-url "https://..." \
  --input-args '["-ss", "00:30:00"]' \
  --ffmpeg-args '["-vn", "-c:a", "libmp3lame", "-b:a", "128k"]'
```

The smoke test reads the API key from `RUNPOD_API_KEY` rather than from an argv flag.
It should also support optional `--upload-headers-json`.

The smoke test should:

1. Submit with RunPod `/run`.
2. Poll `/status/{job_id}` every 5 seconds.
3. Print progress/status.
4. Exit `0` on successful completion.
5. Exit non-zero on failure, timeout, or malformed response.
6. Enforce a caller-provided max wait, defaulting to 75 minutes.

## Repository Documentation

README examples, local test fixtures, and smoke-test examples must match this v1 contract.
Older placeholder-based examples using `{input}`, `{output}`, caller-provided `-i`, `output_name`, `upload_method`, or caller-provided timeout are obsolete and must not be kept as active documentation.
