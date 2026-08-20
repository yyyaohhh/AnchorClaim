"""
Step 3: On-chain settlement.

Submits the computed demurrage amount, together with an evidence hash, to the escrow
smart contract (AnchorClaimEscrow.settle). The contract deducts the penalty from the
deposit to the owner and refunds the remaining balance to the charterer.

Calls the contract via web3.py. When web3 is not installed or the RPC is not configured,
it runs in mock mode and prints the transaction it would submit, so the pipeline still
runs. For a real deployment see ../contracts/AnchorClaimEscrow.sol; settlement is
submitted by the attestor (the agent's signing key).
"""

import hashlib
import json
import os

try:
    from web3 import Web3
except ImportError:
    Web3 = None

# minimal ABI for the contract settle function
ESCROW_ABI = json.loads("""[
  {"inputs":[
    {"internalType":"bytes32","name":"id","type":"bytes32"},
    {"internalType":"uint64","name":"actualHours","type":"uint64"},
    {"internalType":"uint256","name":"demurrage","type":"uint256"},
    {"internalType":"bytes32","name":"evidenceHash","type":"bytes32"}],
   "name":"settle","outputs":[],"stateMutability":"nonpayable","type":"function"}
]""")


def _evidence_hash(evidence: dict) -> str:
    """Hash the audit evidence (contract + SoF + AIS) with sha256 for an on-chain, auditable record."""
    blob = json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "0x" + hashlib.sha256(blob).hexdigest()


def settle_on_chain(voyage_id: str, receipt: dict, evidence: dict) -> dict:
    """
    voyage_id: voyage id (keccak-hashed to bytes32)
    receipt: the result from step 2
    evidence: raw data used to build the evidence hash
    """
    rpc = os.getenv("RPC_URL", "https://sepolia.base.org")
    escrow_addr = os.getenv("ESCROW_ADDR")
    attestor_key = os.getenv("ATTESTOR_KEY")

    ev_hash = _evidence_hash(evidence)
    actual_hours = int(round(receipt["counted_laytime_hours"]))
    # USDC has 6 decimals
    demurrage_units = int(round(receipt["total_penalty_usd"] * 1_000_000))

    # missing web3 / config -> mock mode
    if Web3 is None or not escrow_addr or not attestor_key:
        print("[step3] mock settlement (web3/RPC/contract not configured). Tx params below:")
        return {
            "mode": "mock",
            "voyage_id": voyage_id,
            "actual_hours": actual_hours,
            "demurrage_units": demurrage_units,
            "evidence_hash": ev_hash,
            "chain": "Base Sepolia (mock)",
        }

    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(attestor_key)
    contract = w3.eth.contract(address=Web3.to_checksum_address(escrow_addr), abi=ESCROW_ABI)

    voyage_bytes32 = Web3.keccak(text=voyage_id)
    ev_bytes32 = bytes.fromhex(ev_hash[2:])

    tx = contract.functions.settle(
        voyage_bytes32, actual_hours, demurrage_units, ev_bytes32
    ).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 200000,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    receipt_chain = w3.eth.wait_for_transaction_receipt(tx_hash)

    return {
        "mode": "live",
        "tx_hash": receipt_chain.transactionHash.hex(),
        "evidence_hash": ev_hash,
        "demurrage_usd": receipt["total_penalty_usd"],
        "chain": "Base Sepolia",
    }


if __name__ == "__main__":
    demo_receipt = {"counted_laytime_hours": 90, "total_penalty_usd": 43750.0}
    demo_evidence = {"contract": "MV Ocean Star 48h", "ais": "berth->depart 96h"}
    out = settle_on_chain("0xVOYAGE_OCEANSTAR", demo_receipt, demo_evidence)
    print(json.dumps(out, indent=2, ensure_ascii=False))
