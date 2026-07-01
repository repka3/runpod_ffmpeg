# AGENTS.md

Project-specific rules for this RunPod FFmpeg worker.

## API Contract And Failure Semantics

When changing handler behavior, response JSON shape, progress payloads, retry behavior, RunPod status handling, or failure semantics, stop before coding.

State:

- the observed behavior or requested change
- the possible interpretations
- the tradeoffs
- the recommended interpretation
- the exact decision needed from the user

Wait for explicit approval before implementation.

Worker exceptions are internal unless explicitly decided otherwise. Expected worker failures must be caught at the worker/handler boundary and returned as the stable JSON contract:

```json
{
  "phase": "failed",
  "failed_phase": "...",
  "error_prefix": "...",
  "message": "...",
  "duration_seconds": 0.0
}
```

Do not allow expected worker failures to escape to RunPod as platform handler exceptions. Do not return a top-level `error` key in normal worker output.

## Smoke Tests

Smoke tests against RunPod cost real money. Before submitting a smoke job:

- confirm the user explicitly asked to start it
- confirm both source and upload URLs are fresh for that run
- submit exactly one job
- poll only that submitted job
- stop polling if the user says stop

Never reuse old presigned URLs from previous chat history, logs, or failed runs unless the user explicitly confirms that exact reuse.

## Documentation

README is the caller/operator contract. Keep it updated when changing request fields, allowed FFmpeg arguments, response payloads, deployment steps, or smoke-test behavior.

Specs and plans under `docs/` are internal design history. They do not replace README updates for user-facing behavior.
