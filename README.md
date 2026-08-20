# AnchorClaim — Demurrage Audit & On-chain Settlement

An AI agent plus on-chain escrow that audits maritime demurrage claims and settles them
automatically. The Voyage Sentinel agent parses the charter party, cross-checks port
records against AIS data, computes the demurrage owed, and settles it through an escrow
smart contract — replacing weeks of manual reconciliation with a verifiable,
second-scale flow.

Built for NTU InnovateX Hackathon 2026 (Track 1: Payments & Financial Infrastructure;
Track 2: AI Agents & Real-World Use Cases).

## Pipeline

```
parse contract  ->  calculate demurrage  ->  settle on-chain
   (step 1)             (step 2)                (step 3)
```

1. **Parse** — a local LLM extracts laytime, rate and suspension clauses from the
   charter party into structured JSON.
2. **Calculate** — deducts contractual suspension time (bad weather, strikes, etc.),
   cross-checks the port log against AIS in-port hours, and computes the penalty.
3. **Settle** — writes an evidence hash and demurrage amount to the escrow contract,
   which deducts the penalty and releases the balance. Inconsistent records block
   auto-settlement and route to manual review.

## Features

- **Persistent audit cache** — analyzing a voyage runs the LLM parse and settlement
  pipeline, so results are cached to disk (keyed by a hash of the voyage inputs) and
  served instantly on repeat views. The cache survives server restarts; editing a
  voyage's contract invalidates its entry automatically. A vessel already analyzed is
  marked in the fleet list, and a "Clear cache" control or `DELETE /api/cache` resets it.
  Force a fresh run with `Re-run` in the UI or `/api/audit/<id>?refresh=1`.
- **Live contract editing** — edit any clause in the charter party in the UI and
  re-audit; the agent re-parses the text and the numbers update, showing the result is
  computed from the contract, not hardcoded.
- **Confidence scores & human-review gate** — each parsed field carries a confidence
  score; low-confidence parses or settlements above a ceiling are paused as
  `needs_review` (human-in-the-loop) instead of auto-settling.
- **Explainable settlement** — every demurrage figure expands into a step-by-step
  calculation breakdown (gross hours → exclusions → counted → excess → rate → total).
- **Fleet search** — filter the fleet by vessel name, IMO number or port as you type.

## Run the UI

```bash
pip install -r requirements.txt      # minimum: flask
python backend/server.py
# open http://localhost:8788
```

Every value in the UI is computed live by the engine in `agent/` via `/api/audit/<id>`.
It runs with no extra setup — Ollama and the blockchain are optional, with mock
fallbacks so the full pipeline works out of the box.

## Run the CLI (no browser)

```bash
python agent/run_pipeline.py         # single voyage, end to end
python agent/audit_pipeline.py       # all three sample voyages
```

## Enable real parsing and on-chain settlement (optional)

```bash
# real contract parsing
ollama pull qwen2.5                  # then set MODEL in agent/step1_parse_contract.py

# real on-chain settlement
cp .env.example .env                 # fill RPC_URL / ESCROW_ADDR / ATTESTOR_KEY
```

## Layout

```
agent/
  step1_parse_contract.py   # parse the charter party (Ollama, with regex fallback)
  step2_calculate.py        # demurrage with suspension deduction + AIS cross-check
  step3_settle_onchain.py   # on-chain settlement via web3
  audit_pipeline.py         # full pipeline -> structured result
  samples.py                # sample voyages
backend/
  server.py                 # Flask API, also serves the UI
frontend/
  index.html                # web console
contracts/
  AnchorClaimEscrow.sol     # settlement smart contract
```

## Sample voyages

- **MV Ocean Star** — 42h over allowed laytime after a 6h weather exclusion -> $43,750 demurrage.
- **MV Nord Wind** — suspension time deducted brings counted laytime within allowance -> no demurrage.
- **MV Delta Spirit** — port log 40h vs AIS 79h -> dispute, settlement blocked pending review.

## Requirements

- Python 3.9+
- `flask` (API + UI). Optional: `ollama` (real parsing), `web3` (real settlement).
