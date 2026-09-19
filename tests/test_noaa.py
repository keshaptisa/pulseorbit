from datetime import UTC, datetime

from app import data
from app.data import load_daypre_replay, parse_historical_event_report, parse_noaa_forecast, parse_noaa_intervals


def test_parse_official_three_day_forecast():
    text = """:Product: 3-Day Forecast
:Issued: 2026 Sep 19 0030 UTC
NOAA Kp index breakdown Sep 19-Sep 21 2026
             Sep 19       Sep 20       Sep 21
00-03UT       5.00         2.00         1.67
S1 or greater    40%      10%      1%
R3 or greater    15%       5%      1%
"""
    events = parse_noaa_forecast({"body": text}, datetime(2026, 9, 21, tzinfo=UTC))
    assert any(item["family"] == "G" and item["scale"] == "G1" for item in events)
    assert any(item["family"] == "G" and item["value"] == 2.0 and item["scale"] is None for item in events)
    assert any(item["family"] == "S" and item["probability"] == 40 for item in events)
    assert any(item["family"] == "R" and item["probability"] == 15 for item in events)
    assert all(item["forecast"] for item in events)


def test_parse_noaa_dated_alert():
    source = {"body": [{"product_id": "XM5S", "issue_datetime": "2026-08-20", "message": "Begin Time: 2026 Aug 20 1132 UTC\nEnd Time: 2026 Aug 20 1154 UTC\nNoaa Scale: R2 - Moderate"}]}
    events = parse_noaa_intervals(source)
    assert len(events) == 1
    assert events[0]["start"] == datetime(2026, 8, 20, 11, 32, tzinfo=UTC)
    assert events[0]["scale"] == "R2"


def test_duplicate_alert_records_are_collapsed():
    record = {"product_id": "SAME", "issue_datetime": "2026-08-20T12:00:00Z", "message": "Begin Time: 2026 Aug 20 1132 UTC\nEnd Time: 2026 Aug 20 1154 UTC\nNoaa Scale: S2"}
    assert len(parse_noaa_intervals({"body": [record, dict(record)]})) == 1


def test_later_cancellation_removes_active_product():
    alert = {"product_id": "SAME", "issue_datetime": "2026-08-20T12:00:00Z", "message": "Begin Time: 2026 Aug 20 1132 UTC\nEnd Time: 2026 Aug 20 1554 UTC\nNoaa Scale: S2"}
    cancellation = {"product_id": "SAME", "issue_datetime": "2026-08-20T13:00:00Z", "message": "CANCELATION: product withdrawn"}
    assert parse_noaa_intervals({"body": [alert, cancellation]}) == []


def test_alert_evidence_keeps_raw_record_hash():
    record = {"product_id": "HASH", "issue_datetime": "2026-08-20T12:00:00Z", "message": "Begin Time: 2026 Aug 20 1132 UTC\nEnd Time: 2026 Aug 20 1154 UTC\nNoaa Scale: R2"}
    event = parse_noaa_intervals({"body": [record]})[0]
    assert len(event["record_sha256"]) == 64


def test_replay_excludes_later_publication():
    source = {"body": [
        {"product_id": "OLD", "issue_datetime": "2024-05-10T11:00:00+00:00", "message": "Begin Time: 2024 May 10 1132 UTC\nEnd Time: 2024 May 10 1154 UTC\nNoaa Scale: R2"},
        {"product_id": "FUTURE", "issue_datetime": "2024-05-10T13:00:00+00:00", "message": "Begin Time: 2024 May 10 1132 UTC\nEnd Time: 2024 May 10 1154 UTC\nNoaa Scale: R5"},
    ]}
    events = parse_noaa_intervals(source, datetime(2024, 5, 10, 12, tzinfo=UTC))
    assert [event["product_id"] for event in events] == ["OLD"]


def test_parse_ncei_daily_event_report():
    source = {"body": "7130       0558   0758      0841  G16  5   XRA  1-8A      C5.8    4.1E-02   3654"}
    events = parse_historical_event_report(source, datetime(2024, 5, 1, tzinfo=UTC))
    assert len(events) == 1
    assert events[0]["start"].hour == 5


def test_daypre_replay_selects_latest_file_before_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DAYPRE_DIR", tmp_path)
    (tmp_path / "20240509daypre.txt").write_text(":Issued: 2024 May 09 2212 UTC\n:Prediction_dates: 2024 May 10   2024 May 11   2024 May 12\nProton 50 20 10\n", encoding="utf-8")
    (tmp_path / "20240510daypre.txt").write_text(":Issued: 2024 May 10 2200 UTC\n:Prediction_dates: 2024 May 11   2024 May 12   2024 May 13\nProton 99 99 99\n", encoding="utf-8")
    cutoff = datetime(2024, 5, 10, 12, tzinfo=UTC)
    events, meta = load_daypre_replay(cutoff, datetime(2024, 5, 11, tzinfo=UTC))
    assert meta["issued"] == datetime(2024, 5, 9, 22, 12, tzinfo=UTC)
    assert events[0]["probability"] == 50


def test_forecast_columns_are_dated_by_table_header():
    text = """:Product: 3-Day Forecast
:Issued: 2026 Sep 19 0030 UTC
NOAA Kp index breakdown Sep 19-Sep 21 2026
             Sep 19       Sep 20       Sep 21
00-03UT       5.00         2.00         1.67
S1 or greater    40%      10%      1%
R3 or greater    15%       5%      1%
"""
    events = parse_noaa_forecast({"body": text}, datetime(2026, 9, 22, tzinfo=UTC))
    kp = {item["start"]: item["value"] for item in events if item["family"] == "G"}
    assert kp[datetime(2026, 9, 19, tzinfo=UTC)] == 5.00
    assert kp[datetime(2026, 9, 20, tzinfo=UTC)] == 2.00
    assert kp[datetime(2026, 9, 21, tzinfo=UTC)] == 1.67
    probabilities = {(i["family"], i["start"]): i["probability"] for i in events if i.get("probability") is not None}
    assert probabilities[("S", datetime(2026, 9, 20, tzinfo=UTC))] == 10
    assert probabilities[("R", datetime(2026, 9, 21, tzinfo=UTC))] == 1


def test_forecast_days_roll_over_into_the_next_year():
    text = (
        ":Issued: 2026 Dec 31 1230 UTC\n"
        "NOAA Kp index breakdown Dec 31-Jan 02 2026\n"
        "             Dec 31       Jan 01       Jan 02\n"
        "00-03UT        1.67         2.00         2.33\n"
        "S1 or greater    1%     1%     1%\n"
        "R3 or greater    1%     1%     1%\n"
    )
    events = parse_noaa_forecast({"body": text}, datetime(2027, 1, 5, tzinfo=UTC))
    days = sorted({e["start"].date().isoformat() for e in events if e["family"] == "G"})
    assert days == ["2026-12-31", "2027-01-01", "2027-01-02"]
