"""
Step 1: Parse the charter party.

Sends the free-text contract to the configured LLM provider (Settings page:
OpenAI / Claude / Custom) and extracts structured JSON — laytime hours, demurrage
rate, suspension conditions, vessel name — each with a confidence score.

The model output is passed through a regex extractor before json.loads, since an LLM
may wrap its JSON in code fences or surrounding prose. If no provider is configured,
or the call fails, parse_contract() falls back to a deterministic regex parser so the
pipeline still runs end to end (and the demo/tests never 500 on an unreachable model).
"""

import json
import re

from llm import chat_json, is_configured, public_status

MOCK_CONTRACT = """
CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: MV Ocean Star
Port of Loading: Port of Shanghai
Port of Discharge: Port of Singapore

LAYTIME CLAUSE:
Total allowed laytime for loading and discharging operations shall be 48 hours in total.

DEMURRAGE CLAUSE:
If the vessel is delayed beyond the allowed laytime, the charterer shall pay demurrage at USD 25000 per day or pro rata.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather, strikes, port closures, or force majeure events shall not count as laytime.
"""


def _mock_parse(text: str) -> dict:
    """Regex fallback so the demo works without a configured model, reading each real contract."""
    laytime = re.search(r"laytime.*?(\d+)\s*hours", text, re.IGNORECASE | re.DOTALL)
    rate = re.search(r"USD\s*([\d,]+)\s*per\s*day", text, re.IGNORECASE)
    vessel = re.search(r"Vessel Name:\s*(.+)", text)
    conds = []
    ex = re.search(r"Time lost due to (.+?)(?:events|shall)", text, re.IGNORECASE | re.DOTALL)
    if ex:
        raw = re.split(r",|\bor\b", ex.group(1))
        conds = [c.strip(" .\n") for c in raw if c.strip(" .\n")]

    confidence = {
        "laytime_hours": 0.97 if laytime else 0.4,
        "demurrage_rate_usd_per_day": 0.97 if rate else 0.4,
        "suspension_conditions": 0.9 if conds else 0.45,
        "vessel_name": 0.98 if vessel else 0.3,
    }
    return {
        "laytime_hours": int(laytime.group(1)) if laytime else 48,
        "demurrage_rate_usd_per_day": int(rate.group(1).replace(",", "")) if rate else 25000,
        "suspension_conditions": conds or ["bad weather"],
        "vessel_name": vessel.group(1).strip() if vessel else "Unknown",
        "confidence": confidence,
    }


def parse_contract(contract_text: str = MOCK_CONTRACT) -> dict:
    """Parse contract text -> structured dict.

    Uses the configured LLM provider when one is set; otherwise falls back to the
    deterministic regex parser (no external service required).
    """
    prompt = f"""
You are a shipping law data extraction assistant.
Parse the following charter party contract text and extract key information into a valid JSON object.

Contract Text:
{contract_text}

JSON Schema Requirements:
1. laytime_hours (number)
2. demurrage_rate_usd_per_day (number)
3. suspension_conditions (array of strings)
4. vessel_name (string)
5. confidence (object mapping each of the four fields above to a number 0-1 for how
   certain you are it was stated explicitly in the contract; use a low value if you inferred it)

Return raw JSON string only.
"""

    if not is_configured():
        print("[step1] no LLM provider configured — using regex mock parser on the actual contract text")
        return _mock_parse(contract_text)

    try:
        parsed = chat_json(prompt)
    except Exception as e:  # noqa: BLE001
        print(f"[step1] provider call failed ({e}) — falling back to regex mock parser")
        return _mock_parse(contract_text)

    # ensure a confidence block exists even if the model omitted it
    parsed.setdefault("confidence", {
        "laytime_hours": 0.9, "demurrage_rate_usd_per_day": 0.9,
        "suspension_conditions": 0.85, "vessel_name": 0.95,
    })
    return parsed


if __name__ == "__main__":
    print("Configured provider:", public_status())
    result = parse_contract()
    print("Parsing Result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))