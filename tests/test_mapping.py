import pandas as pd

from app.main import json_safe_value
from app.mapping import normalize_excel_row


def test_json_safe_value_handles_pandas_nat_and_timestamps():
    raw = {
        "router_ip": "10.0.0.10",
        "update_time": pd.Timestamp("2026-09-21 09:00:00"),
        "notes": pd.NaT,
        "fuel_percent": float("nan"),
    }

    result = json_safe_value(raw)

    assert result["router_ip"] == "10.0.0.10"
    assert result["update_time"] == "2026-09-21T09:00:00"
    assert result["notes"] is None
    assert result["fuel_percent"] is None


def test_normalize_excel_row_maps_common_generator_columns():
    raw = {
        "Site Name": "Site A",
        "Router IP": "10.0.0.10",
        "Site Type": "Hospital",
        "Region": "North",
        "REG": "REG-01",
        "IP": "192.168.1.15",
        "Fuel %": 66.5,
        "Mains Voltage": 220.2,
        "Load Amperes": 14.6,
        "Update Time": "2026-09-21 12:00",
        "Engine State": "Running",
        "Controller Mode": "Auto",
        "Running Hours": 1234.5,
        "Total Fuel Consumption": 432.1,
        "Num Starts": 7,
        "Alarms": "None",
    }

    result = normalize_excel_row(raw)

    assert result["site_name"] == "Site A"
    assert result["router_ip"] == "10.0.0.10"
    assert result["site_type"] == "Hospital"
    assert result["region"] == "North"
    assert result["reg"] == "REG-01"
    assert result["ip"] == "192.168.1.15"
    assert result["fuel_percent"] == 66.5
    assert result["mains_voltage"] == 220.2
    assert result["load_amperes"] == 14.6
    assert result["engine_state"] == "Running"
    assert result["controller_mode"] == "Auto"
    assert result["running_hours"] == 1234.5
    assert result["total_fuel_consumption"] == 432.1
    assert result["num_starts"] == 7
    assert result["alarms"] == "None"


def test_normalize_excel_row_strips_percent_symbols_from_numeric_fields():
    raw = {
        "Fuel (%)": "52%",
        "Mains (V)": "220V",
        "Load (A)": "1.3A",
        "running hours": "201.5",
    }

    result = normalize_excel_row(raw)

    assert result["fuel_percent"] == 52.0
    assert result["mains_voltage"] == 220.0
    assert result["load_amperes"] == 1.3
    assert result["running_hours"] == 201.5
