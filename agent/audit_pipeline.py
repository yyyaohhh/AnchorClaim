"""
Full audit pipeline: parse contract -> gather evidence -> reason -> calculate demurrage
-> settle on-chain. Exposes audit_voyage(), returning a structured result consumed by
both the API and the CLI.

Stages:
- step1  parses the charter party into structured JSON
- step2b gathers six independent evidence sources, then has three AI agents (OpenAI,
         Anthropic, Gemini — one vendor each) vote on which of the charterer's claimed
         suspensions actually happened, and whether the submitted port log itself is
         trustworthy
- step2  computes demurrage, deducting only the suspensions step2b's vote corroborated,
         and charging the independently-verified AIS hours instead of the submitted
         log whenever the vote flags it as untrustworthy
- step3  settles the result on-chain

Every voyage auto-settles — there is no human-in-the-loop hold. A distrusted port log
doesn't block settlement; the agent vote resolves it by falling back to the AIS figure
and the case still settles autonomously.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from step1_parse_contract import parse_contract
from step2_calculate import calculate_demurrage, gross_duration_hours
from step2b_reason_evidence import reason_evidence
from step3_settle_onchain import settle_on_chain
from evidence_sources import gather_evidence


def audit_voyage(voyage_id, voyage, do_settle=True, contract_override=None):
    """Run the full audit for one voyage. Returns a structured result dict.

    contract_override: optional raw contract text supplied at run time (lets the
    UI re-audit an edited contract and watch the numbers change live).
    """
    steps = []

    # --- Step 1: parse contract (LLM) ---
    contract_text = contract_override if contract_override else voyage["contract_text"]
    contract = parse_contract(contract_text)
    steps.append({"name": "parse_contract", "output": contract})

    # --- Multi-source evidence: AIS, weather, satellite, PCS, NOR, news signals ---
    # AIS/SoF is one of the six sources here, not a separate hardcoded check.
    sof_hours = gross_duration_hours(voyage["ais_port_log"])
    evidence = gather_evidence(voyage, sof_hours)
    steps.append({"name": "evidence", "output": evidence})

    # --- Step 2b: reason over the evidence to decide which claimed suspensions ---
    # actually happened, and whether the AIS/SoF timeline itself holds up.
    reasoning = reason_evidence(contract, voyage, evidence["sources"])
    steps.append({"name": "reason_evidence", "output": reasoning})
    corroborated_events = [
        {"reason": d["reason"], "start": d["start"], "end": d["end"]}
        for d in reasoning["suspension_decisions"] if d["corroborated"]
    ]

    # --- Resolve the AIS/SoF check: if the vote distrusts the submitted log, charge
    # the independently-verified AIS hours instead — no hold, the vote settles it. ---
    ais_hours = voyage.get("ais_hours", sof_hours)
    check = {
        "consistent": reasoning["ais_sof_consistent"],
        "delta_hours": round(abs(sof_hours - ais_hours), 2),
        "sof_hours": round(sof_hours, 2),
        "ais_hours": ais_hours,
        "note": reasoning["ais_sof_note"],
    }
    steps.append({"name": "cross_check", "output": check})
    trusted_hours = sof_hours if check["consistent"] else ais_hours

    # --- Step 2: calculate demurrage (deducting only corroborated suspensions, and
    # charging the trusted hours if the submitted log was voted untrustworthy) ---
    receipt = calculate_demurrage(contract, voyage["ais_port_log"], corroborated_events,
                                   gross_hours_override=trusted_hours)
    steps.append({"name": "calculate", "output": receipt})

    result = {
        "voyage_id": voyage_id,
        "vessel": voyage["vessel"],
        "imo": voyage["imo"],
        "port": voyage["port"],
        "contract": contract,
        "receipt": receipt,
        "check": check,
        "evidence": evidence,
        "reasoning": reasoning,
        "escrow": voyage["escrow"],
    }

    # --- Verdict — always resolved autonomously, never held for a human ---
    penalty = receipt["total_penalty_usd"]
    if penalty <= 0:
        result["verdict"] = {"type": "none", "amount_usd": 0, "settled": True}
        return result

    # capped at deposit
    capped = min(penalty, voyage["escrow"]["deposit"])
    base_verdict = {
        "excess_hours": receipt["excess_time_hours"],
        "amount_usd": penalty,
        "penalty": capped,
        "refund_to_charterer": voyage["escrow"]["deposit"] - capped,
        "freight_to_owner": voyage["escrow"]["freight"],
    }
    if not check["consistent"]:
        base_verdict["note"] = (f"Submitted port log ({check['sof_hours']}h) was voted untrustworthy; "
                                 f"settled using the AIS-verified {check['ais_hours']}h instead")

    result["verdict"] = {**base_verdict, "type": "demurrage", "settled": True}

    # --- Step 3: settle on-chain ---
    if do_settle:
        evidence = {"contract": contract, "ais_port_log": voyage["ais_port_log"], "receipt": receipt}
        tx = settle_on_chain(voyage_id, receipt, evidence)
        result["settlement"] = tx
        steps.append({"name": "settle", "output": tx})

    result["steps"] = steps
    return result


if __name__ == "__main__":
    from samples import SAMPLE_VOYAGES
    for vid, v in SAMPLE_VOYAGES.items():
        r = audit_voyage(vid, v)
        print("=" * 60)
        print(f"{r['vessel']} ({vid})")
        print(json.dumps(r["verdict"], indent=2, ensure_ascii=False))
