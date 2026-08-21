"""
Q&A agent for AnchorClaim.

Answers questions about the AnchorClaim product and about blockchain in general.
Connects to any OpenAI-compatible chat-completions endpoint (OpenAI, DeepSeek,
OpenRouter, Groq, Ollama's ``/v1``, …) — the user supplies the base URL, API key
and model name at runtime, so no provider or model is hardcoded.

Configuration precedence (highest first):
  1. values posted by the UI in the request body,
  2. environment variables ``ANCHORCLAIM_QNA_BASE_URL`` / ``ANCHORCLAIM_QNA_API_KEY``
     / ``ANCHORCLAIM_QNA_MODEL``.

If no API key is configured (or the provider cannot be reached), a small
deterministic fallback answers common questions about the project offline so the
widget is never dead.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You are AnchorClaim's support agent. AnchorClaim is a maritime demurrage \
settlement product: an AI agent plus on-chain escrow that audits demurrage claims and settles \
them automatically, replacing weeks of manual reconciliation with a verifiable, second-scale flow.

ABOUT THE PRODUCT
- Pipeline: (1) parse the charter party — extract allowed laytime, daily demurrage rate and \
suspension clauses, each with a confidence score; (2) gather multi-source evidence (AIS track, \
weather, satellite imagery, port community system, Notice of Readiness, public notices) and \
reason over which claimed suspensions (e.g. bad weather) actually happened; (3) calculate \
demurrage — gross in-port hours minus corroborated exclusions minus allowed laytime = excess \
hours, then excess / 24 x daily rate = demurrage owed; (4) cross-check the port log (Statement \
of Facts) against AIS in-port hours — a material delta flags a potential data conflict; \
(5) settle on-chain — write an evidence hash and the amount to an escrow smart contract that \
deducts the penalty to the shipowner and refunds the remainder to the charterer.
- Safety rails: low-confidence parses or amounts above a ceiling pause for human review \
("needs_review"); inconsistent SoF vs AIS records become a "dispute" and never auto-settle.
- Scale view: a fleet overview auto-audits dozens of voyages and flags each one as demurrage \
(red), clear (green) or dispute (yellow), rolling up total exposure, at-risk value and escrow.

BLOCKCHAIN CONTEXT
- Settlement targets an EVM-compatible escrow contract (Solidity). The on-chain step is \
optional; an offline mock settlement is used when no RPC/contract is configured.
- The contract records an evidence hash (attestation) so a settled claim is verifiable.
- "On-chain settlement" means the payment decision plus evidence hash are recorded immutably; \
the escrow logic determines who is paid and who is refunded.

STYLE
- Reply in the same language the user wrote in (Chinese or English).
- Be concise and accurate. Do not invent specific numbers, addresses, contracts or tokens that \
were not provided. For general blockchain questions, explain clearly and, where natural, tie the \
answer back to AnchorClaim in one sentence.
- If asked about configuration/connecting a model, explain that the user sets the API base URL, \
API key and model name in the Q&A settings (gear icon), or via ANCHORCLAIM_QNA_* environment \
variables; any OpenAI-compatible endpoint works (OpenAI, DeepSeek, OpenRouter, Groq, Ollama /v1)."""


def _normalize_base(base_url: str | None) -> str:
    """Accept a provider root, a /v1 root, or a full /chat/completions URL."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base


def _offline_reply(q: str) -> str:
    """Deterministic, markdown-light answers for common questions (no LLM needed)."""
    t = q.lower()
    has = lambda *words: any(w in t for w in words)  # noqa: E731

    if has("demurrage", "滞期", "滞期费"):
        return ("**Demurrage** is the compensation a charterer pays the shipowner when a vessel "
                "is kept in port beyond the laytime allowed by the charter party. AnchorClaim "
                "computes it from the contract, not a spreadsheet: gross in-port time − "
                "corroborated exclusions − allowed laytime = **excess hours**, then "
                "`excess / 24 × daily rate` = demurrage owed. Run the demo on **MV Ocean Star** to "
                "see a $43,750 example in the command center.")

    if has("ais", "cross-check", "cross check", "statement of facts", "sof"):
        return ("AnchorClaim cross-checks the port's **Statement of Facts** against **AIS** in-port "
                "hours. If they differ beyond tolerance (2h), the voyage is flagged as a **dispute** "
                "and auto-settlement is blocked — e.g. the demo's Delta Spirit shows 40h SoF vs 79h "
                "AIS. This is the fraud-resistance layer: money never moves on a mismatched record.")

    if has("escrow", "settle", "settlement", "on-chain", "onchain", "托管", "结算"):
        return ("Settlement writes an **evidence hash** plus the amount to an EVM-compatible "
                "**escrow contract**. The contract deducts the demurrage to the shipowner and "
                "refunds the remainder to the charterer. Inconsistent records block the settlement "
                "and route to manual review, so a falsified log can't be auto-paid.")

    if has("fraud", "dispute", "争议", "伪造", "篡改"):
        return ("Two fraud defenses: (1) **AIS cross-check** — the port log must match the actual "
                "in-port hours, else the voyage becomes a dispute; (2) **human-in-the-loop** — "
                "low-confidence parses or large amounts pause for review. Disputed voyages never "
                "auto-settle.")

    if has("pipeline", "how does it work", "how it works", "流程", "怎么做", "工作原理", "workflow"):
        return ("The pipeline is `parse → evidence → calculate → cross-check → settle`:\n"
                "1. **Parse** the charter party (laytime, rate, suspension clauses).\n"
                "2. **Gather + reason** over six evidence sources to confirm which suspensions happened.\n"
                "3. **Calculate** demurrage from counted vs allowed laytime.\n"
                "4. **Cross-check** the port log against AIS.\n"
                "5. **Settle** on-chain via the escrow contract.\n"
                "Open the **command center** and click *Run audit* to watch it step by step.")

    if has("blockchain", "smart contract", "智能合约", "以太坊", "ethereum", "chain", "evm", "web3"):
        return ("AnchorClaim's settlement step targets an **EVM-compatible escrow contract**. "
                "The blockchain is used for **immutable, verifiable settlement**: the agent records "
                "an evidence hash + the amount, and the escrow logic pays the owner / refunds the "
                "charterer. It runs with a **mock settlement** out of the box (no RPC needed), and "
                "connects to a real chain via `RPC_URL` / `ESCROW_ADDR` / `ATTESTOR_KEY`.")

    if has("confidence", "review", "human", "人工", "置信", "审核", "复核"):
        return ("Every parsed field carries a **confidence score**. If any field is below threshold "
                "or the amount exceeds the auto-settle ceiling, the result is **needs_review** — a "
                "human must approve before funds move. This keeps the automation honest on edge cases.")

    if has("fleet", "scale", "总览", "船队", "规模"):
        return ("The **fleet overview** runs the same engine over a whole book of ~42 voyages and "
                "flags each one red (demurrage), green (clear) or yellow (dispute), rolling up total "
                "exposure, at-risk value and escrow. Click any vessel chip to drill into its full "
                "audit in the command center.")

    if has("model", "api key", "api 密钥", "configure", "配置", "connect", "deepseek", "openai", "gpt"):
        return ("To connect a real model: open the **Q&A settings** (gear icon in the chat), then set "
                "**API base URL**, **API key** and **model name**. Any OpenAI-compatible endpoint "
                "works — e.g. OpenAI (`https://api.openai.com/v1`, `gpt-4o-mini`), DeepSeek "
                "(`https://api.deepseek.com`, `deepseek-chat`), or a local Ollama "
                "(`http://localhost:11434/v1`, `qwen2.5`). You can also use the "
                "`ANCHORCLAIM_QNA_*` environment variables. Without a key, I answer from a small "
                "offline knowledge base, like right now.")

    return ("I'm running in **offline mode** (no model configured), so I can only answer from my "
            "built-in notes. Try asking about *demurrage*, *the pipeline*, *AIS cross-check*, "
            "*escrow / on-chain settlement*, *disputes*, or *the fleet overview*. To unlock full "
            "conversation, set an API key + model in the Q&A settings (gear icon).")


def ask_question(question: str, history=None, base_url=None, api_key=None, model=None) -> str:
    """Return an answer string for one question.

    history: optional list of prior {role, content} turns (role is "user" or "assistant").
    """
    history = [h for h in (history or []) if h.get("content")]
    supplied_base = base_url or os.environ.get("ANCHORCLAIM_QNA_BASE_URL")
    base = _normalize_base(supplied_base)
    key = api_key or os.environ.get("ANCHORCLAIM_QNA_API_KEY")
    model_name = (model or os.environ.get("ANCHORCLAIM_QNA_MODEL") or DEFAULT_MODEL).strip()

    # Offline fallback only when nothing is configured at all. A custom base URL
    # without a key is a valid config (e.g. a local Ollama /v1 endpoint).
    if not key and not supplied_base:
        return _offline_reply(question)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(
        {"role": h["role"] if h.get("role") in ("user", "assistant") else "user", "content": h["content"]}
        for h in history[-20:]
    )
    messages.append({"role": "user", "content": question})

    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.4,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reply = data["choices"][0]["message"]["content"].strip()
        return reply or _offline_reply(question)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8"))
            detail = str(body.get("error", "") or "")
        except Exception:  # noqa: BLE001
            detail = ""
        msg = f"Provider returned HTTP {e.code}."
        if detail:
            msg += f" {detail}"
        return msg + "\n\nCheck the base URL, API key and model name in the Q&A settings (gear icon)."
    except urllib.error.URLError as e:
        return (f"Could not reach {base} ({e.reason}). "
                "Check the base URL / network, or clear the API key to use the offline fallback.")


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "How does AnchorClaim work?"
    print(ask_question(q))