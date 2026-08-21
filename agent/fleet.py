"""
Fleet-scale overview.

The single-voyage demo drills into a handful of sample voyages one at a time. A
production deployment runs the whole book at once, so this module generates a
deterministic fleet of dozens of voyages and summarises the result — total
exposure, disputes, and a per-vessel traffic-light (demurrage / normal /
dispute) for the fleet-overview screen.

The arithmetic reuses the exact engine used for single voyages (step2_calculate
:: calculate_demurrage + cross_check_ais), so the numbers are not decorative:
each vessel's exposure is the same formula the audit would produce. Only the
LLM parse and the multi-source evidence fetch are skipped here (they are the
slow, optional layers) — the fleet view is a fast, offline, fully deterministic
roll-up of the book.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from step2_calculate import calculate_demurrage, cross_check_ais

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"

# Asia-Pacific trade lanes (the three map ports plus the wider network).
PORTS = [
    "Port of Singapore", "Port of Hong Kong", "Port of Tokyo",
    "Port of Shanghai", "Port of Busan", "Port of Kaohsiung",
    "Port of Manila", "Port of Osaka", "Port of Shenzhen", "Port Klang",
]

PREFIXES = [
    "Ocean", "Nord", "Pacific", "Golden", "Silver", "Iron", "Jade",
    "Crimson", "Azure", "Celestial", "Summer", "Winter", "Coral", "Titan",
    "Meridian", "Aurora", "Zenith", "Harbor", "Trade", "Compass",
]
SUFFIXES = [
    "Star", "Wind", "Wave", "Spirit", "Dawn", "Bridge", "Quest", "Falcon",
    "Harbor", "Runner", "Trader", "Fortune", "Sky", "Glory", "Mariner",
    "Voyager", "Crown", "Eagle", "Rise", "Light",
]

LADEN_PORTS = "Port of Shanghai"


def _fmt(dt: datetime) -> str:
    return dt.strftime(TIME_FORMAT)


def _build_fleet(n: int = 42, seed: int = 2026) -> list[dict]:
    """Generate a deterministic, realistic-looking fleet with a known mix of outcomes.

    Outcome targets: ~57% demurrage, ~31% clear, and the remainder disputes,
    shuffled deterministically so the fleet is stable across requests.
    """
    rng = random.Random(seed)

    targets = ["demurrage"] * int(round(n * 0.57))
    targets += ["none"] * int(round(n * 0.31))
    targets += ["dispute"] * (n - len(targets))
    rng.shuffle(targets)

    base = datetime(2026, 8, 2, 0, 0, 0)
    used = set()
    fleet = []
    for i, target in enumerate(targets):
        while True:
            name = "MV " + rng.choice(PREFIXES) + " " + rng.choice(SUFFIXES)
            if name not in used:
                used.add(name)
                break

        imo = 9120000 + (i * 131) % 79000
        port = PORTS[i % len(PORTS)]
        laytime = rng.choice([36, 48, 60, 72, 96, 120])
        rate = rng.choice([18000, 22000, 24000, 28000, 32000, 36000, 40000])
        freight = rng.choice([320000, 420000, 520000, 620000, 720000, 850000])
        deposit = int(freight * rng.choice([0.18, 0.20, 0.22, 0.25]))

        # A corroborated suspension window reduces counted laytime for some voyages.
        excluded = 0.0
        if target in ("demurrage", "none") and rng.random() < 0.55:
            excluded = float(rng.randint(4, 18))

        excess = 0.0
        if target == "demurrage":
            excess = float(rng.randint(8, 60))
        elif target == "dispute":
            excess = float(rng.randint(0, 24))  # some disputes also carry exposure

        gross = laytime + excess + excluded

        berth = base + timedelta(days=i, hours=rng.randint(2, 9))
        depart = berth + timedelta(hours=gross)
        arrival = berth - timedelta(hours=rng.randint(3, 12))

        suspensions = []
        if excluded > 0:
            reason = rng.choice(["bad weather", "port congestion", "strike"])
            s = berth + timedelta(hours=4)
            suspensions = [{
                "reason": reason,
                "start": _fmt(s),
                "end": _fmt(s + timedelta(hours=excluded)),
            }]

        ais_hours = gross
        if target == "dispute":
            ais_hours = gross + rng.randint(10, 40)  # AIS disagrees with the SoF

        contract = {
            "laytime_hours": laytime,
            "demurrage_rate_usd_per_day": rate,
            "suspension_conditions": ["bad weather", "strike", "port congestion", "port closure"],
            "vessel_name": name,
            "confidence": {
                "laytime_hours": 0.97,
                "demurrage_rate_usd_per_day": 0.97,
                "suspension_conditions": 0.9,
                "vessel_name": 0.98,
            },
        }
        ais_port_log = {
            "vessel_name": name,
            "arrival_anchorage": _fmt(arrival),
            "nor_tendered": _fmt(berth),
            "berthing_time": _fmt(berth),
            "departure_time": _fmt(depart),
        }

        fleet.append({
            "id": f"0xVOYAGE_FLEET_{i + 1:03d}",
            "vessel": name,
            "imo": str(imo),
            "port": port,
            "target": target,
            "contract": contract,
            "ais_port_log": ais_port_log,
            "suspension_events": suspensions,
            "escrow": {"freight": freight, "deposit": deposit},
            "ais_hours": ais_hours,
        })

    return fleet


def fleet_overview() -> dict:
    """Run the engine's arithmetic over the whole fleet and return a roll-up."""
    fleet = _build_fleet()

    voyages = []
    status_counts = {"demurrage": 0, "none": 0, "dispute": 0}
    by_port: dict[str, dict] = {}
    total_exposure = 0.0
    total_at_risk = 0.0
    total_escrow = 0
    total_freight = 0
    recovered = 0
    cleared = 0

    for v in fleet:
        receipt = calculate_demurrage(v["contract"], v["ais_port_log"], v["suspension_events"])
        sof = receipt["gross_duration_hours"]
        check = cross_check_ais(sof, v["ais_hours"])
        penalty = receipt["total_penalty_usd"]
        deposit = v["escrow"]["deposit"]
        freight = v["escrow"]["freight"]
        excess = receipt["excess_time_hours"]

        if not check["consistent"]:
            status = "dispute"
            amount = 0.0
            at_risk = round(min(penalty, deposit), 2) if penalty > 0 else 0.0
        elif excess > 0:
            status = "demurrage"
            amount = round(min(penalty, deposit), 2)
            at_risk = 0.0
            recovered += 1
        else:
            status = "none"
            amount = 0.0
            at_risk = 0.0
            cleared += 1

        status_counts[status] += 1
        total_exposure += amount
        total_at_risk += at_risk
        total_escrow += deposit
        total_freight += freight
        by_port.setdefault(v["port"], {"count": 0, "exposure": 0.0})
        by_port[v["port"]]["count"] += 1
        by_port[v["port"]]["exposure"] += amount

        voyages.append({
            "id": v["id"],
            "vessel": v["vessel"],
            "imo": v["imo"],
            "port": v["port"],
            "status": status,
            "amount_usd": amount,
            "excess_hours": round(excess, 2),
            "at_risk_usd": at_risk,
        })

    order = {"dispute": 0, "demurrage": 1, "none": 2}
    voyages.sort(key=lambda x: (order[x["status"]], -x["amount_usd"], x["vessel"]))

    return {
        "total_voyages": len(voyages),
        "total_exposure_usd": round(total_exposure, 2),
        "total_at_risk_usd": round(total_at_risk, 2),
        "total_escrow_usd": total_escrow,
        "total_freight_usd": total_freight,
        "recovered": recovered,
        "cleared": cleared,
        "disputes": status_counts["dispute"],
        "status_counts": status_counts,
        "by_port": [
            {"port": p, "count": d["count"], "exposure": round(d["exposure"], 2)}
            for p, d in sorted(by_port.items(), key=lambda kv: -kv[1]["count"])
        ],
        "voyages": voyages,
    }


if __name__ == "__main__":
    import json
    overview = fleet_overview()
    print(json.dumps({k: v for k, v in overview.items() if k != "voyages"}, indent=2))
    print("voyages:", len(overview["voyages"]))