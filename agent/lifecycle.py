"""
Voyage lifecycle: human verification -> smart-contract funding (two wallet
signatures) -> daily monitoring until the voyage completes -> final audit.

This models the steps that happen *before* the audit engine ever runs:
  1. The AI-extracted contract terms (step1_parse_contract) get a human's
     sign-off before they're used to populate the escrow contract's fund()
     call — an LLM extraction should not go straight on-chain unconfirmed.
  2. Both parties (charterer and shipowner) sign to fund the escrow.
  3. Once funded, the contract "checks in" once per day until the voyage
     completes, at which point the real audit (evidence -> agent vote ->
     calculate -> settle) can run.

Everything here is mocked: no real wallet connection, no real chain calls.
Real settlement is separately gated behind RPC/key config in
step3_settle_onchain.py — this module only prepares what a human would
confirm and sign before that point.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"

MOCK_WALLETS = {
    "charterer": "0xCA47f1B2e9D4a6C8f0B3e5D7a9C1b3E5f7A9c1E3",
    "shipowner": "0x0AB9d3E5f7A9c1E3b5D7f9A1c3E5b7D9f1A3c5E7",
}

# In-memory funding state per voyage_id. A demo-scale, single-process store;
# resets when the server restarts, same as the on-disk audit cache resets
# when you clear it.
_FUNDING_STATE: dict[str, dict] = {}


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TIME_FORMAT)


def contract_template(voyage_id: str, voyage: dict, contract: dict) -> dict:
    """The escrow contract's fund() call a human confirms before either party
    signs. Mirrors AnchorClaimEscrow.sol's real function signature."""
    return {
        "function": "fund",
        "args": {
            "id": voyage_id,
            "shipowner": MOCK_WALLETS["shipowner"],
            "freight": voyage["escrow"]["freight"],
            "deposit": voyage["escrow"]["deposit"],
            "laytimeHours": contract.get("laytime_hours"),
        },
    }


def funding_status(voyage_id: str) -> dict:
    state = _FUNDING_STATE.get(voyage_id, {})
    signed = {party: party in state for party in ("charterer", "shipowner")}
    return {"signed": signed, "funded": all(signed.values()), "signatures": state}


def sign(voyage_id: str, party: str) -> dict:
    if party not in MOCK_WALLETS:
        raise ValueError("party must be 'charterer' or 'shipowner'")
    state = _FUNDING_STATE.setdefault(voyage_id, {})
    payload = f"{voyage_id}:{party}:{datetime.utcnow().isoformat()}"
    signature = "0x" + hashlib.sha256(payload.encode()).hexdigest()[:40]
    state[party] = {"wallet": MOCK_WALLETS[party], "signature": signature}
    return funding_status(voyage_id)


def reset_funding(voyage_id: str) -> None:
    _FUNDING_STATE.pop(voyage_id, None)


def monitoring_log(voyage: dict) -> list[dict]:
    """One entry per day from NOR tendered to departure — the daily status
    check the contract runs while waiting for the voyage to complete."""
    nor = _parse(voyage["ais_port_log"]["nor_tendered"])
    departure = _parse(voyage["ais_port_log"]["departure_time"])
    entries = []
    day = nor
    i = 0
    while day < departure:
        i += 1
        next_day = min(day + timedelta(days=1), departure)
        done = next_day >= departure
        entries.append({
            "day": i,
            "date": day.strftime("%Y-%m-%d"),
            "status": "vessel departed — ready for final audit" if done else "in laytime — monitoring",
        })
        day = next_day
    if not entries:
        entries.append({"day": 1, "date": nor.strftime("%Y-%m-%d"),
                         "status": "vessel departed same day — ready for final audit"})
    return entries
