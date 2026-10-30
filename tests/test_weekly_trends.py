from datetime import date

import pytest

from app import main


@pytest.mark.parametrize("metric", ["running_hours", "num_starts"])
@pytest.mark.parametrize("reference", [None, date(2026, 10, 30)])
def test_weekly_wow_uses_reference_weekday_despite_weekend_captures(monkeypatch, metric, reference):
    # Friday pairs are complete. Saturday is partial and Sunday has no readings.
    readings = {
        "2026-10-16": 100.0,
        "2026-10-18": 110.0,
        "2026-10-23": 150.0,
        "2026-10-24": 155.0,
        "2026-10-25": None,
        "2026-10-30": 170.0,
    }
    records = [
        (1, 1, "Test site", "Ancash", "generator", 1, day, value)
        for day, value in readings.items()
    ]
    monkeypatch.setattr(main, "get_cached_analytics_history", lambda *args: records)

    class EmptyResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeDB:
        def execute(self, *args):
            return EmptyResult()

    result = main.generator_trends(
        metric=metric, granularity="week", reference_date=reference, db=FakeDB()
    )
    region = next(item for item in result["sites"] if item["name"] == "Ancash")
    week43 = result["labels"].index("Week 43 (2026)")
    week44 = result["labels"].index("Week 44 (2026)")
    assert region["values"][week43] == 50.0
    assert region["values"][week44] == 20.0
    assert result["total"][week43] == 50.0
    assert result["site_values"][0]["value"] == 20.0
