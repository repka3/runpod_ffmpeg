import subprocess

import pytest

from src.errors import FFprobeFailed
from src.probe import probe_duration


def test_probe_duration_parses_positive_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="12.345\n", stderr=""),
    )

    assert probe_duration(tmp_path / "input.mp4") == 12.345


@pytest.mark.parametrize("stdout", ["", "0", "N/A"])
def test_probe_duration_rejects_missing_or_invalid(monkeypatch, tmp_path, stdout):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=stdout, stderr=""),
    )

    with pytest.raises(FFprobeFailed, match="duration"):
        probe_duration(tmp_path / "input.mp4")
