"""
Full audit pipeline: parse contract -> calculate demurrage -> settle on-chain.
Exposes audit_voyage(), returning a structured result consumed by both the API and the CLI.

Stages:
- step1 parses the charter party into structured JSON
- step2 computes demurrage from that JSON and deducts contractual suspension time
- step3 settles the result on-chain
"""

from __future__ import annotations

import json

from step1_parse_contract import parse_contract
from step2_calculate import calculate_demurrage, cross_check_ais
from step3_settle_onchain import settle_on_chain


# Thresholds for the human-review gate.
CONFIDENCE_THRESHOLD = 0.6      # any parsed field below this needs a human to confirm
AUTO_SETTLE_CEILING_USD = 250000  # settlements above this always route to human approval


def _review_flags(contract, penalty_usd):
    """Decide whether this audit must pause for human review, and why."""
    reasons = []
    conf = contract.get("confidence", {})
    low = [f for f, c in conf.items() if c < CONFIDENCE_THRESHOLD]
    if low:
        reasons.append("low parser confidence on: " + ", ".join(low))
    if penalty_usd > AUTO_SETTLE_CEILING_USD:
        reasons.append(f"amount ${penalty_usd:,.0f} exceeds auto-settle ceiling ${AUTO_SETTLE_CEILING_USD:,.0f}")
    return reasons


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

    # --- Step 2: calculate demurrage (with suspension deduction) ---
    receipt = calculate_demurrage(contract, voyage["ais_port_log"], voyage.get("suspension_events"))
    steps.append({"name": "calculate", "output": receipt})

    # --- Cross-check: port-log (SoF) gross hours vs AIS in-port hours ---
    ais_hours = voyage.get("ais_hours", receipt["gross_duration_hours"])
    check = cross_check_ais(receipt["gross_duration_hours"], ais_hours)
    check["sof_hours"] = receipt["gross_duration_hours"]
    check["ais_hours"] = ais_hours
    steps.append({"name": "cross_check", "output": check})

    result = {
        "voyage_id": voyage_id,
        "vessel": voyage["vessel"],
        "imo": voyage["imo"],
        "port": voyage["port"],
        "contract": contract,
        "receipt": receipt,
        "check": check,
        "escrow": voyage["escrow"],
    }

    # --- Verdict ---
    if not check["consistent"]:
        result["verdict"] = {
            "type": "dispute",
            "reason": f"Port log {receipt['gross_duration_hours']}h vs AIS {ais_hours}h differ by {check['delta_hours']}h (over tolerance)",
            "settled": False,
        }
        return result

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

    # --- Human-in-the-loop gate: low confidence or large amount -> pause for review ---
    flags = _review_flags(contract, penalty)
    if flags:
        result["verdict"] = {**base_verdict, "type": "needs_review", "settled": False, "review_reasons": flags}
        return result

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
