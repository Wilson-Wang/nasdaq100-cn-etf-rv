import pandas as pd

from etf_dataset.health import build_source_health


def test_repeated_degradation_is_alerted_but_single_fallback_is_not_critical():
    runs = pd.DataFrame(
        [
            {"resource": "factor:FX", "symbol": "*", "finished_at_utc": "2026-09-06T10:00:00Z", "status": "DEGRADED"},
            {"resource": "factor:FX", "symbol": "*", "finished_at_utc": "2026-09-07T10:00:00Z", "status": "DEGRADED"},
            {"resource": "factor:FX", "symbol": "*", "finished_at_utc": "2026-09-08T10:00:00Z", "status": "DEGRADED"},
        ]
    )
    manifest = {"quality": {"by_symbol": {"A": {"formal_signal_ready": True}}}, "failures": [], "warnings": []}
    report = build_source_health(runs, manifest)
    assert report["status"] == "WARN"
    assert any(item["code"] == "REPEATED_SOURCE_DEGRADATION" for item in report["alerts"])


def test_model_readiness_block_is_error():
    report = build_source_health(
        pd.DataFrame(),
        {"quality": {"by_symbol": {"A": {"formal_signal_ready": False}}}, "failures": [], "warnings": []},
    )
    assert report["status"] == "ERROR"
