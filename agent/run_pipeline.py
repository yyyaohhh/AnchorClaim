"""
Full pipeline: parse contract -> calculate demurrage -> settle on-chain.

The parsed contract JSON is passed directly into the calculator, an AIS cross-check
guards against inconsistent port records (blocking auto-settlement and routing to
manual review), and the settled result is written on-chain.

Run: python run_pipeline.py
"""

import json

from step1_parse_contract import parse_contract, MOCK_CONTRACT
from step2_calculate import calculate_demurrage, cross_check_ais
from step3_settle_onchain import settle_on_chain


def run(contract_text: str, ais_port_log: dict, suspension_events: list, voyage_id: str):
    print("=" * 60)
    print(f"Voyage {voyage_id} - starting audit")
    print("=" * 60)

    # --- Step 1: parse the contract (LLM) ---
    print("\n[1/3] Parsing charter party...")
    contract = parse_contract(contract_text)
    print(json.dumps(contract, indent=2, ensure_ascii=False))

    # --- Step 2: calculate demurrage (with suspension-time deduction) ---
    print("\n[2/3] Calculating demurrage...")
    receipt = calculate_demurrage(contract, ais_port_log, suspension_events)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))

    # --- Cross-check: SoF gross hours vs AIS in-port hours ---
    # here we treat ais_port_log as the AIS source; in production pass two independent sources to compare.
    ais_hours = receipt["gross_duration_hours"]
    check = cross_check_ais(receipt["gross_duration_hours"], ais_hours)
    print(f"\nCross-check: delta {check['delta_hours']}h -> {'consistent' if check['consistent'] else 'CONFLICT'}")

    if not check["consistent"]:
        print("\n[!] Data dispute: auto-settlement refused, routed to manual review.")
        return {"status": "disputed", "receipt": receipt}

    # no demurrage means nothing to deduct on-chain
    if receipt["total_penalty_usd"] <= 0:
        print("\n[OK] Within laytime, no demurrage, balance released in full.")
        return {"status": "no_demurrage", "receipt": receipt}

    # --- Step 3: on-chain settlement ---
    print("\n[3/3] Settling on-chain...")
    evidence = {"contract": contract, "ais_port_log": ais_port_log, "receipt": receipt}
    tx = settle_on_chain(voyage_id, receipt, evidence)
    print(json.dumps(tx, indent=2, ensure_ascii=False))

    print(f"\n[OK] Done. Demurrage ${receipt['total_penalty_usd']:,.2f} submitted for settlement.")
    return {"status": "settled", "receipt": receipt, "settlement": tx}


if __name__ == "__main__":
    ais_port_log = {
        "vessel_name": "MV Ocean Star",
        "arrival_anchorage": "2026/08/10 08:00:00",
        "berthing_time": "2026/08/10 12:00:00",
        "departure_time": "2026/08/14 12:00:00",   # berthing to departure = 96 hours
    }
    # a 6-hour bad-weather stoppage during cargo ops (bad weather is excluded per the contract)
    suspension_events = [
        {"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"},
    ]

    run(MOCK_CONTRACT, ais_port_log, suspension_events, voyage_id="0xVOYAGE_OCEANSTAR")
