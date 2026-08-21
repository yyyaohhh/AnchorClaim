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
- **Multi-source cross-validation** — the audit corroborates the port log against five
  independent sources (weather, satellite imagery, port community system, Notice of
  Readiness, and public notices) in addition to AIS. The agent reasons over these
  sources to decide which claimed suspensions (e.g. a "bad weather" stoppage) actually
  happened — only corroborated suspensions reduce counted laytime, so an unsupported
  claim no longer discounts the demurrage owed. Runs on a local LLM when available,
  with a deterministic fallback so the pipeline still works offline. Each source is
  real-API-ready with an offline mock fallback.
- **Fleet search** — filter the fleet by vessel name, IMO number or port as you type.
- **Q&A agent** — a chat assistant that answers questions about the project and about
  blockchain. It connects to any OpenAI-compatible model; you supply the API key and model
  name (see [QnA Agent](#qna-agent-project--blockchain-assistant)).

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

## QnA Agent (project & blockchain assistant)

A floating **"Ask the agent"** chat is available on every screen. It answers questions about
AnchorClaim (demurrage, AIS cross-checking, evidence, escrow settlement, disputes, the fleet
roll-up) and about blockchain concepts.

It connects to **any OpenAI-compatible** chat-completions endpoint — the provider and model are
up to you. Open the chat, click the gear (⚙) and set the three fields below, or tap a **preset
chip** (OpenAI · DeepSeek · OpenRouter · Ollama) to auto-fill the base URL and model in one click.

| Field | Example |
|---|---|
| API base URL | `https://api.openai.com/v1` · `https://api.deepseek.com` · `https://openrouter.ai/api/v1` · `http://localhost:11434/v1` |
| API key | `sk-...` (leave blank for a local Ollama; pass a dummy value if the provider ignores it) |
| Model | `gpt-4o-mini` · `deepseek-chat` · `qwen2.5` |

```bash
# OpenAI
base URL:  https://api.openai.com/v1
model:     gpt-4o-mini

# DeepSeek
base URL:  https://api.deepseek.com
model:     deepseek-chat

# Local Ollama (OpenAI-compatible, no key needed)
base URL:  http://localhost:11434/v1
model:     qwen2.5        # after: ollama pull qwen2.5
```

The three settings are stored in your browser (localStorage) only — they are never written to the
server. You can also provision them as environment variables instead of typing them in the UI;
environment values are used when the matching UI field is empty:

```bash
export ANCHORCLAIM_QNA_BASE_URL="https://api.deepseek.com"
export ANCHORCLAIM_QNA_API_KEY="sk-..."
export ANCHORCLAIM_QNA_MODEL="deepseek-chat"
python backend/server.py
```

With nothing configured, the agent answers from a small built-in offline knowledge base (labelled
"offline"), so the widget works out of the box. The endpoint is `POST /api/qna` with a
`{messages: [{role, content}, ...], base_url?, api_key?, model?}` body.

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
  evidence_sources.py       # AIS, weather, satellite, PCS, NOR, news signals
  step2b_reason_evidence.py # reason over the sources to decide corroborated suspensions
  step2_calculate.py        # demurrage with suspension deduction + AIS cross-check
  step3_settle_onchain.py   # on-chain settlement via web3
  audit_pipeline.py         # full pipeline -> structured result
  qna_agent.py              # Q&A agent (any OpenAI-compatible endpoint + offline fallback)
  fleet.py                  # fleet-scale overview generation
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
