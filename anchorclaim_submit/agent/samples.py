"""
Three sample voyages for the demo UI.
Each has: raw contract text (parsed by step 1), an AIS/port log, and suspension events.
Designed to show three outcomes: demurrage, no-demurrage-after-exclusions, and dispute.
"""

SAMPLE_VOYAGES = {
    "0xVOYAGE_OCEANSTAR": {
        "vessel": "MV Ocean Star",
        "imo": "9321483",
        "port": "Port of Rotterdam",
        "contract_text": """
CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: MV Ocean Star
Port of Loading: Port of Shanghai
Port of Discharge: Port of Rotterdam

LAYTIME CLAUSE:
Total allowed laytime for loading and discharging operations shall be 48 hours in total.

DEMURRAGE CLAUSE:
If the vessel is delayed beyond the allowed laytime, the charterer shall pay demurrage at USD 25000 per day or pro rata.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather, strikes, port closures, or force majeure events shall not count as laytime.
""",
        "ais_port_log": {
            "vessel_name": "MV Ocean Star",
            "arrival_anchorage": "2026/08/10 08:00:00",
            "berthing_time": "2026/08/10 12:00:00",
            "departure_time": "2026/08/14 12:00:00",  # 96h gross
        },
        "suspension_events": [
            {"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"},
        ],
        "escrow": {"freight": 500000, "deposit": 100000},
        # AIS-derived in-port hours (would come from the collector); matches SoF here
        "ais_hours": 96.0,
    },

    "0xVOYAGE_NORDWIND": {
        "vessel": "MV Nord Wind",
        "imo": "9445678",
        "port": "Port of Hamburg",
        "contract_text": """
CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: MV Nord Wind
Port of Loading: Port of Singapore
Port of Discharge: Port of Hamburg

LAYTIME CLAUSE:
Total allowed laytime shall be 72 hours in total.

DEMURRAGE CLAUSE:
Demurrage at USD 24000 per day or pro rata for time exceeding allowed laytime.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather, strikes, or port congestion shall not count as laytime.
""",
        "ais_port_log": {
            "vessel_name": "MV Nord Wind",
            "arrival_anchorage": "2026/08/07 00:00:00",
            "berthing_time": "2026/08/07 00:00:00",
            "departure_time": "2026/08/11 00:00:00",  # 96h gross
        },
        "suspension_events": [
            {"reason": "bad weather", "start": "2026/08/08 00:00:00", "end": "2026/08/09 06:00:00"},  # 30h
            {"reason": "port congestion", "start": "2026/08/09 12:00:00", "end": "2026/08/10 12:00:00"},  # 24h
        ],
        "escrow": {"freight": 620000, "deposit": 150000},
        "ais_hours": 96.0,
    },

    "0xVOYAGE_DELTASPIRIT": {
        "vessel": "MV Delta Spirit",
        "imo": "9550011",
        "port": "Port of Antwerp",
        "contract_text": """
CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: MV Delta Spirit
Port of Loading: Port of Busan
Port of Discharge: Port of Antwerp

LAYTIME CLAUSE:
Total allowed laytime shall be 48 hours in total.

DEMURRAGE CLAUSE:
Demurrage at USD 30000 per day or pro rata.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather shall not count as laytime.
""",
        # Port log claims only 40h, but AIS shows the vessel was berthed 79h -> dispute
        "ais_port_log": {
            "vessel_name": "MV Delta Spirit",
            "arrival_anchorage": "2026/08/02 00:00:00",
            "berthing_time": "2026/08/02 02:00:00",
            "departure_time": "2026/08/03 18:00:00",  # 40h gross (suspiciously low)
        },
        "suspension_events": [],
        "escrow": {"freight": 480000, "deposit": 120000},
        "ais_hours": 79.0,  # AIS disagrees with port log -> flagged
    },
}
