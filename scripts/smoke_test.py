#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests


API_BASE = "https://api.runpod.ai/v2"


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("RUNPOD_API_KEY is required", file=sys.stderr, flush=True)
        return 2

    try:
        input_args = json.loads(args.input_args) if args.input_args else []
        ffmpeg_args = json.loads(args.ffmpeg_args)
        upload_headers = json.loads(args.upload_headers_json) if args.upload_headers_json else {}
    except json.JSONDecodeError as exc:
        print(f"invalid JSON argument: {exc}", file=sys.stderr, flush=True)
        return 2

    payload = {
        "input": {
            "source_url": args.source_url,
            "upload_url": args.upload_url,
            "ffmpeg_args": ffmpeg_args,
        }
    }
    if input_args:
        payload["input"]["input_args"] = input_args
    if upload_headers:
        payload["input"]["upload_headers"] = upload_headers

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    run_url = f"{API_BASE}/{args.endpoint_id}/run"
    try:
        run_response = requests.post(run_url, headers=headers, json=payload, timeout=(10, 60))
        run_response.raise_for_status()
        run_data = run_response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"submit failed: {exc}", file=sys.stderr, flush=True)
        return 1

    job_id = run_data.get("id")
    if not isinstance(job_id, str) or not job_id:
        print(f"malformed submit response: {run_data}", file=sys.stderr, flush=True)
        return 1

    print(f"submitted job {job_id}", flush=True)
    deadline = time.monotonic() + args.max_wait_seconds
    status_url = f"{API_BASE}/{args.endpoint_id}/status/{job_id}"
    while time.monotonic() < deadline:
        try:
            status_response = requests.get(status_url, headers=headers, timeout=(10, 60))
            status_response.raise_for_status()
            status_data = status_response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"status failed: {exc}", file=sys.stderr, flush=True)
            return 1

        status = status_data.get("status")
        progress = status_data.get("output", status_data.get("progress"))
        print(f"status={status} progress={json.dumps(progress, sort_keys=True)}", flush=True)
        if status == "COMPLETED":
            print(json.dumps(status_data.get("output"), indent=2, sort_keys=True), flush=True)
            return 0
        if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            print(json.dumps(status_data, indent=2, sort_keys=True), file=sys.stderr, flush=True)
            return 1
        time.sleep(args.poll_interval_seconds)

    print(f"timed out waiting for job {job_id}", file=sys.stderr, flush=True)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test a deployed RunPod FFmpeg worker.")
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--upload-url", required=True)
    parser.add_argument("--input-args", default="")
    parser.add_argument("--ffmpeg-args", required=True)
    parser.add_argument("--upload-headers-json", default="")
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-wait-seconds", type=float, default=75 * 60)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
