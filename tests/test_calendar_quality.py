from etf_dataset.calendar_quality import effective_sessions, session_lag


def test_preclose_and_postclose_use_correct_exchange_session():
    preclose_eod, preclose_pcf = effective_sessions(
        "2026-09-08", "2026-09-08T02:00:00+00:00"
    )
    assert preclose_eod == "2026-09-07"
    assert preclose_pcf == "2026-09-08"

    postclose_eod, postclose_pcf = effective_sessions(
        "2026-09-08", "2026-09-08T08:00:00+00:00"
    )
    assert postclose_eod == "2026-09-08"
    assert postclose_pcf == "2026-09-08"


def test_before_pcf_cutoff_uses_prior_session():
    eod, pcf = effective_sessions("2026-09-08", "2026-09-08T00:30:00+00:00")
    assert eod == "2026-09-07"
    assert pcf == "2026-09-07"


def test_exchange_holiday_does_not_count_as_stale_session():
    # 2026-10-01 is National Day and not an XSHG trading session.
    eod, pcf = effective_sessions("2026-10-01", "2026-10-01T08:00:00+00:00")
    assert eod < "2026-10-01"
    assert pcf == eod
    assert session_lag(eod, "2026-10-01") == 0
