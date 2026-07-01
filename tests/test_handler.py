from src.handler import handler


def test_handler_converts_leaked_exception_to_failed_phase(monkeypatch):
    progress = []

    def fake_process_job(_job, _progress_update):
        raise RuntimeError("raw secret bug")

    monkeypatch.setattr("src.handler.process_job", fake_process_job)
    monkeypatch.setattr("src.handler.runpod.serverless.progress_update", lambda _job, payload: progress.append(payload))

    result = handler({"id": "job-1", "input": {}})

    assert result["phase"] == "failed"
    assert "error" not in result
    assert result["failed_phase"] == "handler"
    assert result["error_prefix"] == "WORKER_FAILED"
    assert "RuntimeError in handler" in result["message"]
    assert "raw secret bug" not in result["message"]
    assert progress[-1] == result
