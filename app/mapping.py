import re
from typing import Any


FIELD_ALIASES = {
    "site_name": ["site name", "site_name", "site", "name"],
    "router_ip": ["router ip", "router_ip", "routerip", "gateway ip"],
    "site_type": ["site type", "site_type", "type"],
    "region": ["region", "zone", "location"],
    "reg": ["reg", "region code", "reg_code"],
    "ip": ["ip", "device ip", "generator ip"],
    "alarms": ["alarms", "alarm", "status alarm"],
    "fuel_percent": ["fuel %", "fuel_percent", "fuelpercentage", "fuel pct", "fuel"],
    "mains_voltage": ["mains voltage", "mains_voltage", "mainsvoltage", "mains v"],
    "load_amperes": ["load amperes", "load_amperes", "load amps", "load current", "load a"],
    "update_time": ["update time", "update_time", "update", "timestamp", "time"],
    "engine_state": ["engine state", "engine_state", "state"],
    "controller_mode": ["controller mode", "controller_mode", "mode"],
    "running_hours": ["running hours", "running_hours", "hours", "runtime hours"],
    "total_fuel_consumption": ["total fuel consumption", "total_fuel_consumption", "fuel consumption", "consumption"],
    "num_starts": ["num starts", "num_starts", "starts", "number of starts"],
}


def _normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _coerce_number(value: Any) -> Any:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)):
        return value

    text = str(value).strip()
    if text.lower() in {"na", "n/a", "none", "null", "-"}:
        return None

    text = text.replace(",", "").replace("%", "").replace("V", "").replace("A", "").replace("h", "").strip()
    if text == "":
        return None

    try:
        return float(text)
    except ValueError:
        return value


def normalize_excel_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    for raw_key, raw_value in row.items():
        key = _normalize_key(raw_key)
        mapped_key = None

        for target, aliases in FIELD_ALIASES.items():
            if key in aliases or key == target.replace("_", " "):
                mapped_key = target
                break

        if mapped_key is None:
            continue

        value = raw_value
        if mapped_key in {
            "fuel_percent",
            "mains_voltage",
            "load_amperes",
            "running_hours",
            "total_fuel_consumption",
        }:
            value = _coerce_number(value)
        elif mapped_key == "num_starts":
            num = _coerce_number(value)
            value = int(num) if isinstance(num, float) and num.is_integer() else num

        normalized[mapped_key] = value

    return normalized
