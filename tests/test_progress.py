from src.progress import FFmpegProgress, expected_output_duration, format_hhmmss, parse_progress_time


def test_parses_out_time_us_as_microseconds():
    assert parse_progress_time("out_time_us", "12345678") == 12.345678


def test_parses_out_time_fallback():
    assert parse_progress_time("out_time", "01:02:03.500000") == 3723.5


def test_expected_output_duration_with_clip_t():
    assert expected_output_duration(3600, 600, 120, None) == 120


def test_expected_output_duration_with_to_and_ss():
    assert expected_output_duration(3600, 600, None, 900) == 300


def test_expected_output_duration_with_only_ss():
    assert expected_output_duration(3600, 600, None, None) == 3000


def test_format_hhmmss():
    assert format_hhmmss(3661.9) == "01:01:01"


def test_progress_is_monotonic_and_rate_limited():
    sent = []
    current = [100.0]

    def now():
        return current[0]

    tracker = FFmpegProgress(expected_seconds=100, callback=sent.append, started_at=90, now=now)

    tracker.update(50)
    tracker.update(40)
    current[0] = 101.0
    tracker.update(60)
    current[0] = 103.1
    tracker.update(60)

    assert [payload["percent"] for payload in sent] == [50.0, 60.0]
    assert sent[0]["duration_seconds"] == 10.0


def test_force_progress_emits_final_percent():
    sent = []
    tracker = FFmpegProgress(expected_seconds=100, callback=sent.append, started_at=0, now=lambda: 10)

    tracker.update(20)
    tracker.update(100, force=True)

    assert [payload["percent"] for payload in sent] == [20.0, 100.0]
