from __future__ import annotations

from etf_dataset.pcf import _extract_szse_document_url, parse_pcf_xml


SSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PCF>
  <FundInstrumentID>513100</FundInstrumentID>
  <TradingDay>20260908</TradingDay>
  <PreTradingDay>20260907</PreTradingDay>
  <CreationRedemptionSwitch>1</CreationRedemptionSwitch>
  <CreationRedemptionUnit>1000000</CreationRedemptionUnit>
  <NAV>1.2345</NAV>
  <NAVperCU>1234500</NAVperCU>
  <PreCashComponent>1000.5</PreCashComponent>
  <EstimatedCashComponent>1100.5</EstimatedCashComponent>
  <MaxCashRatio>0.50</MaxCashRatio>
  <NetCreationLimit>5000000</NetCreationLimit>
  <NetRedemptionLimit>4000000</NetRedemptionLimit>
  <PublishIOPVFlag>1</PublishIOPVFlag>
  <Component>
    <InstrumentID>NDX</InstrumentID>
    <Quantity>10</Quantity>
    <CreationPremiumRate>0.02</CreationPremiumRate>
    <RedemptionDiscountRate>0.01</RedemptionDiscountRate>
  </Component>
</PCF>
"""


SZSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PCF>
  <SecurityID>159941</SecurityID>
  <TradingDay>20260908</TradingDay>
  <PreTradingDay>20260907</PreTradingDay>
  <Creation>Y</Creation>
  <Redemption>N</Redemption>
  <CreationRedemptionUnit>500000</CreationRedemptionUnit>
  <NAV>2.3456</NAV>
  <CashComponent>900.0</CashComponent>
  <EstimateCashComponent>950.0</EstimateCashComponent>
  <MaxCashRatio>0.40</MaxCashRatio>
  <Publish>Y</Publish>
  <Component>
    <UnderlyingSecurityID>NDX</UnderlyingSecurityID>
    <ComponentShare>5</ComponentShare>
    <PremiumRatio>0.03</PremiumRatio>
    <DiscountRatio>0.015</DiscountRatio>
  </Component>
</PCF>
"""


def test_parse_sse_pcf_uses_verified_0830_availability():
    frame = parse_pcf_xml(
        SSE_XML,
        exchange="sh",
        expected_symbol="513100",
        source_url="https://example.test/sse",
        document_url="https://example.test/sse.xml",
    )
    row = frame.iloc[0]

    assert row["date"] == "2026-09-08"
    assert bool(row["creation_allowed"]) is True
    assert bool(row["redemption_allowed"]) is True
    assert row["creation_unit"] == 1_000_000
    assert row["cash_substitution_limit_pct"] == 50.0
    assert row["max_creation_cash_premium_pct"] == 2.0
    assert row["max_redemption_cash_discount_pct"] == 1.0
    assert row["available_at"] == "2026-09-08T08:30:00+08:00"
    assert bool(row["availability_verified"]) is True
    assert bool(row["pit_verified"]) is True
    assert row["source"] == "sse:official_pcf"


def test_parse_szse_pcf_uses_conservative_preopen_availability():
    frame = parse_pcf_xml(
        SZSE_XML,
        exchange="sz",
        expected_symbol="159941",
        source_url="https://example.test/szse",
        document_url="https://example.test/szse.xml",
    )
    row = frame.iloc[0]

    assert row["date"] == "2026-09-08"
    assert bool(row["creation_allowed"]) is True
    assert bool(row["redemption_allowed"]) is False
    assert row["cash_substitution_limit_pct"] == 40.0
    assert row["max_creation_cash_premium_pct"] == 3.0
    assert row["max_redemption_cash_discount_pct"] == 1.5
    assert row["available_at"] == "2026-09-08T09:15:00+08:00"
    assert bool(row["availability_verified"]) is False
    assert bool(row["pit_verified"]) is True
    assert row["source"] == "szse:official_pcf"


def test_extract_szse_document_url_from_report_payload():
    payload = [
        {
            "data": [
                {
                    "jjdm": (
                        "<a href='download?path=%2Ffiles%2Ftext%2F&"
                        "filename=ETF15994120260908'>159941</a>"
                    )
                }
            ]
        }
    ]

    url = _extract_szse_document_url(payload, "159941")
    assert url == "https://reportdocs.static.szse.cn/files/text/ETF15994120260908.xml"
