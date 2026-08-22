"""
Step 2: Calculate demurrage.

Computes the penalty from the parsed contract terms and the port log. Laytime counts
from the Notice of Readiness (NOR tendered), not from berthing — that's when the
charter party clock actually starts, whether or not the vessel is alongside yet.
Suspension events (bad weather, strikes, etc.) are deducted against the contract's
suspension_conditions, so only counted laytime is charged. Returns a structured
receipt for on-chain settlement.
"""

from __future__ import annotations

from datetime import datetime

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TIME_FORMAT)


def gross_duration_hours(ais_port_log: dict) -> float:
    """Laytime runs from NOR tendered to completion (departure), not from berthing —
    a vessel waiting at anchorage after NOR is still on the charterer's clock."""
    nor = _parse(ais_port_log["nor_tendered"])
    departure = _parse(ais_port_log["departure_time"])
    return (departure - nor).total_seconds() / 3600


def calculate_demurrage(contract: dict, ais_port_log: dict, suspension_events: list | None = None,
                         gross_hours_override: float | None = None) -> dict:
    """
    contract: the contract dict parsed in step 1
    ais_port_log: port record containing nor_tendered / departure_time
    suspension_events: [{"reason": "bad weather", "start": "...", "end": "..."}] stoppage windows
    gross_hours_override: use this instead of the submitted log's NOR-to-departure span —
        set when the agent vote decided the submitted log isn't trustworthy, so the
        independently-verified AIS hours are charged instead of the falsified log.
    """
    gross_hours = gross_hours_override if gross_hours_override is not None else gross_duration_hours(ais_port_log)

    # deduct stoppage time per the contract's exclusion conditions
    allowed_conditions = [c.lower() for c in contract.get("suspension_conditions", [])]
    excluded_hours = 0.0
    applied = []
    for ev in (suspension_events or []):
        if ev["reason"].lower() in allowed_conditions:
            dur = (_parse(ev["end"]) - _parse(ev["start"])).total_seconds() / 3600
            excluded_hours += dur
            applied.append({"reason": ev["reason"], "hours": round(dur, 2)})

    counted_hours = gross_hours - excluded_hours

    allowed = contract["laytime_hours"]
    excess_hours = max(counted_hours - allowed, 0)
    excess_days = excess_hours / 24
    daily_rate = contract["demurrage_rate_usd_per_day"]
    total_penalty = excess_days * daily_rate

    return {
        "vessel_name": contract.get("vessel_name", ais_port_log.get("vessel_name")),
        "gross_duration_hours": round(gross_hours, 2),
        "excluded_hours": round(excluded_hours, 2),
        "applied_suspensions": applied,
        "counted_laytime_hours": round(counted_hours, 2),
        "allowed_laytime_hours": allowed,
        "excess_time_hours": round(excess_hours, 2),
        "total_penalty_usd": round(total_penalty, 2),
    }


def cross_check_ais(sof_hours: float, ais_hours: float, tolerance_hours: float = 2.0) -> dict:
    """SoF duration vs AIS in-port hours; a delta over tolerance is flagged as a dispute (anti-fraud)."""
    delta = abs(sof_hours - ais_hours)
    return {"consistent": delta <= tolerance_hours, "delta_hours": round(delta, 2)}


if __name__ == "__main__":
    import json

    contract = {
        "laytime_hours": 48,
        "demurrage_rate_usd_per_day": 25000,
        "suspension_conditions": ["bad weather", "strikes"],
        "vessel_name": "MV Ocean Star",
    }
    ais_port_log = {
        "vessel_name": "MV Ocean Star",
        "arrival_anchorage": "2026/08/10 08:00:00",
        "nor_tendered": "2026/08/10 09:00:00",
        "berthing_time": "2026/08/10 12:00:00",
        "departure_time": "2026/08/14 12:00:00",
    }
    # a 6-hour bad-weather stoppage during cargo operations
    suspension_events = [
        {"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"},
    ]

    receipt = calculate_demurrage(contract, ais_port_log, suspension_events)
    print("Final Calculation Receipt:")
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
