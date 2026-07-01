# RunPod FFmpeg Worker Implementation Plan

Source spec: `docs/specs/01-2026-07-01-runpod-ffmpeg-worker.md`

## Objective

Build the v1 RunPod Serverless FFmpeg worker exactly to the approved spec:

- one HTTPS source URL per job
- one HTTPS presigned PUT upload URL per job
- local download, local `ffprobe`, local `ffmpeg`, then upload
- allowlisted transform arguments only
- RunPod async progress updates
- minimal success response
- stage-specific internal exceptions converted to a stable failed payload
- deployable through RunPod GitHub source build

No implementation step may expand the public request contract beyond the spec without a new spec change.

## Success Criteria

- The worker can be deployed by RunPod from GitHub using the root `Dockerfile`.
- A valid async RunPod `/run` job downloads, probes, converts/compresses/clips, uploads, and returns:

```json
{
  "phase": "done",
  "duration_seconds": 123.4
}
```

- Invalid requests fail before transfer or ffmpeg with stable error prefixes.
- Progress updates follow the spec shape and expose current ffmpeg percent when available.
- Unit tests cover worker-owned validation, command construction, progress parsing, and failure formatting.
- A smoke-test script can validate a deployed endpoint with real bucket URLs.

## Phase 0 - Repository Cleanup

Goal: remove or replace draft artifacts that contradict the spec.

Tasks:

- Inspect existing files for placeholder-based contract references.
- Remove or stub stale README content that documents the obsolete placeholder contract.
- Replace stale `test_input.json` with a v1-compatible local/example payload, or remove it if the local runner no longer needs it.
- Remove old tests that target the abandoned `{input}` / `{output}` placeholder contract.
- Keep `.gitignore`, `.dockerignore`, dependency files, Dockerfile, and docs only if they match the planned implementation.
- Defer full README examples until Phase 8, after validation rules exist and examples can be checked against them.

Verification:

- `rg '\\{input\\}|\\{output\\}|output_name|upload_method|timeout_seconds|input_name' README.md test_input.json tests src` returns no obsolete active contract references.

## Phase 1 - Project Skeleton And Runtime

Goal: create the minimal RunPod worker package and Docker runtime.

Tasks:

- Create Python package under `src/`.
- Add `src/handler.py` with `runpod.serverless.start({"handler": handler})`.
- Add worker modules for validation, transfers, probing, ffmpeg execution, progress, and errors.
- Keep dependencies minimal: `runpod`, `requests`, and test dependencies.
- Ensure Docker image installs `ffmpeg`, which includes `ffprobe`.
- Ensure Docker command starts `python -u -m src.handler`.

Verification:

- `python -c "import src.handler"` does not fail from missing modules.
- Dockerfile remains compatible with RunPod GitHub source build.
- The runtime entry remains `python -u -m src.handler`.

## Phase 2 - Request Validation

Goal: reject bad jobs before network or process execution.

Tasks:

- Validate `job.input` object exists.
- Validate required fields: `source_url`, `upload_url`, `ffmpeg_args`.
- Validate optional fields: `input_args`, `upload_headers`.
- Enforce HTTPS-only URLs.
- Derive local filenames as `input.<source_ext>` and `output.<upload_ext>` from URL paths only.
- Enforce allowed input/output extensions.
- Validate upload headers:
  - string keys and values
  - case-insensitive rejection of `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`
  - no control characters
- Validate `input_args` allows only `-ss <timestamp>`.
- Validate `ffmpeg_args` using the allowlist, arity rules, and value constraints from the spec.
- Reject requests that include both `-t` and `-to`.
- Reject raw shell-like structure, stray positional tokens, unsupported flags, stream-index-qualified flags, `-f`, and `copy` codecs.
- Validate filter strings with the simple filter allowlist and protocol/file-reading rejection rules.

Verification:

- Unit tests for valid requests.
- Unit tests for each major invalid request category raising `INVALID_INPUT`.
- Unit tests for URL extension derivation and redacted URL formatting.
- Unit tests for `-t` / `-to` mutual exclusion.

## Phase 3 - Download And Upload

Goal: implement bounded, logged HTTP transfer stages.

Tasks:

- Stream source download with HTTP GET.
- Follow at most 5 download redirects, requiring every hop to remain HTTPS.
- Enforce 5 GB input limit by `Content-Length` precheck when available and by counting streamed bytes.
- Use connect timeout 10 seconds and read stall timeout 60 seconds.
- Accept only download status `200` or `206`.
- Wrap local file write errors during download, including `ENOSPC`, as `DOWNLOAD_FAILED`.
- Check generated output file size before upload and fail with `LIMIT_EXCEEDED` when it exceeds 5 GB.
- Stream output upload with HTTP PUT.
- Do not follow upload redirects.
- Set worker-owned `Content-Length` from local output size.
- Apply caller-provided safe upload headers.
- Never log upload header values.
- Accept only upload status `200`, `201`, or `204`.
- Redact URL query strings and fragments from all logs/errors.
- Use connect timeout 10 seconds for upload.
- Treat upload stalls as `UPLOAD_FAILED`. If the HTTP client cannot enforce a true per-chunk write timeout for streaming PUT, document the chosen fallback behavior in code comments and rely on RunPod `executionTimeout` as the outer bound.
- Wrap local file read errors during upload, including filesystem errors from reading the generated output, as `UPLOAD_FAILED`.

Verification:

- Unit tests for size counting and early abort.
- Unit tests for output over 5 GB failing before upload.
- Unit tests for transfer status handling.
- Unit tests for redirect policy.
- Unit tests for download/write and upload/read filesystem errors mapping to the active stage prefix.
- Unit tests ensure redacted logs/errors do not include presigned query strings or upload header values.

## Phase 4 - FFprobe

Goal: get a reliable positive media duration before ffmpeg.

Tasks:

- Run `ffprobe` against the local input file.
- Parse positive numeric media duration in seconds.
- Log duration in `HH:MM:SS` format.
- Fail with `FFPROBE_FAILED` when probe execution fails or duration is missing, zero, or invalid.

Verification:

- Unit tests for duration parsing.
- Unit tests for missing/zero/non-numeric duration failure.
- Unit tests for `HH:MM:SS` formatting.

## Phase 5 - FFmpeg Command Construction

Goal: build a deterministic command owned by the worker.

Tasks:

- Build command shape:

```bash
ffmpeg -hide_banner -nostdin -y <input_args> -i input.ext <progress flags> <ffmpeg_args> output.ext
```

- Ensure no shell execution is used.
- Add ffmpeg progress output flags in a position that does not alter caller media options.
- Do not expose or set `-threads` in v1.
- Ensure local temp paths are used only for local input/output.

Verification:

- Unit tests for command ordering.
- Unit tests prove caller cannot inject `-i`, output paths, operational flags, or unsupported options.

## Phase 6 - FFmpeg Execution And Progress

Goal: run ffmpeg with live progress and fixed timeout.

Tasks:

- Run ffmpeg with `subprocess.Popen`.
- Enforce fixed 1-hour ffmpeg timeout.
- Parse progress continuously.
- Prefer `out_time_us`; fallback to `out_time`.
- Convert progress time to seconds.
- Calculate expected output media duration from `ffprobe`, `-ss`, `-t`, and `-to`.
- Use the validated parsed clip arguments from Phase 2 as structured inputs to progress calculation; do not re-parse raw arg strings in the progress loop.
- Clamp percent to `0-100`.
- Keep percent monotonic.
- Accept a progress callback injected by the handler. FFmpeg execution should not own the RunPod job object directly.
- Forward progress through that callback at most once every 2 seconds when percent changes.
- Log ffmpeg progress only at 10% buckets.
- Send final `running_ffmpeg` progress update with `100.0` before `uploading` after successful ffmpeg completion.
- Include capped stderr tail, max 4000 chars, on ffmpeg failure.
- Progress payloads must include:
  - `phase`
  - `percent`
  - `progress_available`
  - `duration_seconds`

Verification:

- Unit tests for `out_time_us` parsing.
- Unit tests for `out_time` fallback parsing.
- Unit tests for clipping denominator with `-ss`, `-t`, and `-to`.
- Unit tests for monotonic percent.
- Unit tests for timeout and non-zero exit formatting as `FFMPEG_FAILED`.

## Phase 7 - Handler Orchestration

Goal: connect stages into the RunPod job lifecycle.

Tasks:

- Use a per-job temporary directory.
- Emit progress phases using the spec payload shape:
  - `downloading`
  - `probing`
  - `running_ffmpeg`
  - `uploading`
  - `done`
- For non-ffmpeg phases, send `percent: null` and `progress_available: false`.
- For ffmpeg progress, send numeric `percent` and `progress_available: true`.
- Track elapsed wall-clock time from job start.
- Return only final success payload:

```json
{
  "phase": "done",
  "duration_seconds": 123.4
}
```

- Convert all exceptions at the handler boundary to a stable `phase: "failed"` payload.
- Do not return a top-level `error` key, because the RunPod SDK treats that as a failed job result.
- Ensure cleanup is best effort and cannot fail a confirmed successful upload.

Verification:

- Unit test successful orchestration with mocked transfer/probe/ffmpeg/upload.
- Unit tests each stage failure maps to a `phase: "failed"` payload with the expected error prefix.

## Phase 8 - Documentation And Examples

Goal: make the repository deployable and operable without stale contract confusion.

Tasks:

- Rewrite README around RunPod GitHub source build as the primary deployment path.
- Include request examples for:
  - video/audio to MP3
  - WAV/PCM transcription prep
  - meeting video compression
  - clipped segment creation
- Document `/run` plus `/status` polling.
- Document recommended RunPod `executionTimeout`, `ttl`, URL expiry, and temp disk requirement.
- Document progress payloads and final success response.
- Document stable failure prefixes.
- Keep any local example JSON aligned with v1.

Verification:

- README examples pass request validation tests, either directly or through fixture tests.
- README and local fixtures contain no active obsolete placeholder-contract examples.

## Phase 9 - Smoke Test Script

Goal: validate a deployed RunPod endpoint against real bucket URLs.

Tasks:

- Add `scripts/smoke_test.py`.
- Read API key from `RUNPOD_API_KEY`.
- Accept endpoint ID, source URL, upload URL, optional `input_args`, `ffmpeg_args`, optional upload headers JSON, poll interval, and max wait.
- Default poll interval is 5 seconds.
- Default max wait is 75 minutes.
- Submit to RunPod `/run`.
- Poll `/status/{job_id}`.
- Print progress and final status.
- Exit `0` on successful completion.
- Exit non-zero on RunPod failure, timeout, malformed response, or missing API key.

Verification:

- Unit test argument parsing where practical.
- Manual deployed smoke test with real presigned source/upload URLs.

## Phase 10 - Final Verification

Goal: prove local code and docs are internally consistent before deployment.

Tasks:

- Run unit tests.
- Run static contract searches for obsolete placeholder contract terms.
- Build Docker image locally if environment permits.
- Optionally run a local API server with RunPod local testing mode.
- Run deployed smoke test after GitHub source deploy is configured.

Verification:

```bash
pytest
rg '\\{input\\}|\\{output\\}|output_name|upload_method|timeout_seconds|input_name' README.md src tests test_input.json
docker build --platform linux/amd64 -t runpod-ffmpeg-worker:local .
```

The obsolete-contract search should return no matches in active code, fixtures, or README. Docs may mention obsolete terms only as historical warnings.

Docker build may be skipped locally if bandwidth or disk constraints make it impractical; RunPod GitHub source build remains the primary build path.
