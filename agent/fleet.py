"""
Fleet-scale overview + a fully-auditable generated fleet.

The single-voyage demo drills into a handful of sample voyages one at a time. A
production deployment runs the whole book at once, so this module generates a
deterministic fleet of dozens of voyages and summarises the result — total
exposure, disputes, and a per-vessel traffic-light (demurrage / normal /
dispute) for the fleet-overview screen.

Each generated voyage carries a real `contract_text` plus the exact structured
fields the audit engine consumes, so any fleet voyage can also be drilled into
from the command center and run through the FULL pipeline. Because the fleet
voyages have no suspension claims, the deterministic roll-up and the full
pipeline produce identical numbers (the same `calculate_demurrage` +
`cross_check_ais` math), so the overview total always reconciles with the
single-voyage drill-down.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from step2_calculate import calculate_demurrage, cross_check_ais

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"

# Port name -> [lat, lng] (real coordinates, used by both the map and the drill-down).
PORTS = {
    "Port of Singapore": [1.29, 103.82],
    "Port of Hong Kong": [22.30, 114.17],
    "Port of Tokyo": [35.68, 139.68],
    "Port of Shanghai": [31.23, 121.47],
    "Port of Busan": [35.18, 129.08],
    "Port of Kaohsiung": [22.62, 120.31],
    "Port of Manila": [14.60, 120.98],
    "Port of Osaka": [34.69, 135.50],
    "Port of Shenzhen": [22.54, 114.06],
    "Port Klang": [3.03, 101.45],
}

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


def _fmt(dt: datetime) -> str:
    return dt.strftime(TIME_FORMAT)


def _contract_text(vessel: str, port: str, laytime: int, rate: int) -> str:
    """Render a raw charter-party that the parser will read back identically."""
    return f"""CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: {vessel}
Port of Loading: Port of Shanghai
Port of Discharge: {port}

LAYTIME CLAUSE:
Total allowed laytime for loading and discharging operations shall be {laytime} hours in total.

DEMURRAGE CLAUSE:
If the vessel is delayed beyond the allowed laytime, the charterer shall pay demurrage at USD {rate} per day or pro rata.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather, strikes, port closures, or force majeure events shall not count as laytime.
"""


def build_fleet(n: int = 42, seed: int = 2026) -> list[dict]:
    """Generate a deterministic, realistic-looking fleet with a known mix of outcomes.

    Outcome targets: ~57% demurrage, ~31% clear, the remainder disputes, shuffled
    deterministically so the fleet is stable across requests.
    """
    rng = random.Random(seed)

    targets = ["demurrage"] * int(round(n * 0.57))
    targets += ["none"] * int(round(n * 0.31))
    targets += ["dispute"] * (n - len(targets))
    rng.shuffle(targets)

    base = datetime(2026, 8, 2, 0, 0, 0)
    used = set()
    port_names = list(PORTS.keys())
    fleet = []
    for i, target in enumerate(targets):
        while True:
            name = "MV " + rng.choice(PREFIXES) + " " + rng.choice(SUFFIXES)
            if name not in used:
                used.add(name)
                break

        imo = 9120000 + (i * 131) % 79000
        port = port_names[i % len(port_names)]
        laytime = rng.choice([36, 48, 60, 72, 96, 120])
        rate = rng.choice([18000, 22000, 24000, 28000, 32000, 36000, 40000])
        freight = rng.choice([320000, 420000, 520000, 620000, 720000, 850000])
        deposit = int(freight * rng.choice([0.18, 0.20, 0.22, 0.25]))

        excess = 0.0
        if target == "demurrage":
            excess = float(rng.randint(8, 60))
        elif target == "dispute":
            excess = float(rng.randint(0, 24))  # some disputes also carry exposure
        # Keep the accrued penalty within the escrow deposit so the roll-up total
        # reconciles exactly with the single-voyage drill-down.
        max_excess = int(deposit * 24.0 / rate)
        excess = min(excess, float(max_excess))

        gross = laytime + excess

        berth = base + timedelta(days=i, hours=rng.randint(2, 9))
        depart = berth + timedelta(hours=gross)
        arrival = berth - timedelta(hours=rng.randint(3, 12))

        ais_hours = gross
        if target == "dispute":
            ais_hours = gross + rng.randint(10, 40)  # AIS disagrees with the SoF

        lat, lng = PORTS[port]
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
            "lat": lat,
            "lng": lng,
            "contract_text": _contract_text(name, port, laytime, rate),
            "contract": contract,
            "ais_port_log": ais_port_log,
            "suspension_events": [],
            "escrow": {"freight": freight, "deposit": deposit},
            "ais_hours": ais_hours,
        })

    return fleet


def fleet_overview() -> dict:
    """Run the engine's arithmetic over the whole fleet and return a roll-up."""
    fleet = build_fleet()

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
            "lat": v["lat"],
            "lng": v["lng"],
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