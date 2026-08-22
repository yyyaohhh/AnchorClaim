"""
One-time deployment: MockUSDC + AnchorClaimEscrow to Base Sepolia.

This is not part of the live demo (which runs in mock settlement mode). It exists
so the project has a real, verifiable on-chain deployment to point to: a deployed
contract address and at least one real transaction hash, rather than claiming
"on-chain escrow" with nothing to show for it.

Requires a Python environment with web3 + py-solc-x installed (the main app's
Python 3.9 environment cannot install a modern web3 — its aiohttp dependency
needs Python 3.10+). Use a separate venv:

    python3.12 -m venv .deploy-venv
    .deploy-venv/bin/pip install web3 py-solc-x python-dotenv

Also requires Node (for OpenZeppelin contract sources) and a .env with:
    RPC_URL       - defaults to https://sepolia.base.org
    ATTESTOR_KEY  - a Base Sepolia private key funded with a little testnet ETH

Run:
    cd scripts && npm install @openzeppelin/contracts@5
    .deploy-venv/bin/python scripts/deploy_testnet.py
"""

import json
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

NODE_MODULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node_modules")
MINT_AMOUNT_USDC = 5_000_000  # 5,000,000 mUSDC (6 decimals), enough to fund every sample voyage many times over


def compile_contracts():
    import solcx
    solcx.install_solc("0.8.24")
    return solcx.compile_files(
        [os.path.join(ROOT, "contracts/AnchorClaimEscrow.sol"),
         os.path.join(ROOT, "contracts/MockUSDC.sol")],
        output_values=["abi", "bin"],
        solc_version="0.8.24",
        allow_paths=NODE_MODULES,
        import_remappings=[f"@openzeppelin={NODE_MODULES}/@openzeppelin"],
        optimize=True,
    )


def deploy(w3, acct, abi, bytecode, *ctor_args):
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(*ctor_args).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 3_000_000,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt.contractAddress, tx_hash.hex()


def main():
    rpc = os.getenv("RPC_URL", "https://sepolia.base.org")
    key = os.getenv("ATTESTOR_KEY")
    if not key:
        sys.exit("ATTESTOR_KEY not set — put a funded Base Sepolia private key in .env first.")

    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(key)
    balance = w3.eth.get_balance(acct.address)
    print(f"Deployer: {acct.address}  balance: {w3.from_wei(balance, 'ether')} ETH")
    if balance == 0:
        sys.exit("Deployer wallet has no testnet ETH — fund it via a Base Sepolia faucet first.")

    print("Compiling...")
    compiled = compile_contracts()
    usdc_out = next(v for k, v in compiled.items() if k.endswith(":MockUSDC"))
    escrow_out = next(v for k, v in compiled.items() if k.endswith(":AnchorClaimEscrow"))

    print("Deploying MockUSDC...")
    usdc_addr, usdc_tx = deploy(w3, acct, usdc_out["abi"], usdc_out["bin"])
    print(f"  MockUSDC: {usdc_addr}  tx: {usdc_tx}")

    print("Deploying AnchorClaimEscrow...")
    escrow_addr, escrow_tx = deploy(w3, acct, escrow_out["abi"], escrow_out["bin"], usdc_addr, acct.address)
    print(f"  AnchorClaimEscrow: {escrow_addr}  tx: {escrow_tx}")

    print(f"Minting {MINT_AMOUNT_USDC:,} mUSDC to the deployer...")
    usdc = w3.eth.contract(address=usdc_addr, abi=usdc_out["abi"])
    tx = usdc.functions.mint(acct.address, MINT_AMOUNT_USDC * 1_000_000).build_transaction({
        "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "gas": 100_000,
    })
    signed = acct.sign_transaction(tx)
    mint_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(mint_hash)
    print(f"  mint tx: {mint_hash.hex()}")

    summary = {
        "chain": "Base Sepolia",
        "deployer": acct.address,
        "mock_usdc": {"address": usdc_addr, "deploy_tx": usdc_tx},
        "anchor_claim_escrow": {"address": escrow_addr, "deploy_tx": escrow_tx, "attestor": acct.address},
        "mint_tx": mint_hash.hex(),
        "explorer": f"https://sepolia.basescan.org/address/{escrow_addr}",
    }
    out_path = os.path.join(ROOT, "scripts", "deployment.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSaved {out_path}")
    print(json.dumps(summary, indent=2))
    print("\nAdd these to .env to make settle_on_chain() do real transactions:")
    print(f"  ESCROW_ADDR={escrow_addr}")


if __name__ == "__main__":
    main()
