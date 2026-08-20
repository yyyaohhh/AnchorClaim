"""
Step 1: Parse the charter party.

Uses a local Ollama LLM to extract the free-text contract into structured JSON
(laytime hours, demurrage rate, suspension conditions, vessel name).

The model output is passed through a regex extractor before json.loads, since an LLM
may wrap its JSON in code fences or surrounding prose. The request also sets
format="json" to constrain the model to a JSON-only response. If ollama is not
installed, parse_contract() falls back to a deterministic regex parser so the
pipeline still runs end to end.
"""

import json
import re

try:
    import ollama
except ImportError:
    ollama = None  # fall back to mock when ollama is absent so the pipeline still runs

MODEL = "qwen2.5"  # set this to whatever `ollama list` shows locally

MOCK_CONTRACT = """
CHARTER PARTY AGREEMENT SUMMARY
Vessel Name: MV Ocean Star
Port of Loading: Port of Shanghai
Port of Discharge: Port of Rotterdam

LAYTIME CLAUSE:
Total allowed laytime for loading and discharging operations shall be 48 hours in total.

DEMURRAGE CLAUSE:
If the vessel is delayed beyond the allowed laytime, the charterer shall pay demurrage at USD 25000 per day or pro rata.

EXCEPTIONS AND SUSPENSION:
Time lost due to bad weather, strikes, port closures, or force majeure events shall not count as laytime.
"""


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the LLM output. Tolerates ```json fences and stray text."""
    # strip markdown code fences
    text = re.sub(r"```(?:json)?", "", text).strip()
    # grab the first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in output:\n{text}")
    return json.loads(match.group(0))


def _mock_parse(text: str) -> dict:
    """Regex fallback so the demo works without Ollama, reading each real contract."""
    laytime = re.search(r"laytime.*?(\d+)\s*hours", text, re.IGNORECASE | re.DOTALL)
    rate = re.search(r"USD\s*([\d,]+)\s*per\s*day", text, re.IGNORECASE)
    vessel = re.search(r"Vessel Name:\s*(.+)", text)
    # suspension conditions: pull the words after the exceptions clause
    conds = []
    ex = re.search(r"Time lost due to (.+?)(?:events|shall)", text, re.IGNORECASE | re.DOTALL)
    if ex:
        raw = re.split(r",|\bor\b", ex.group(1))
        conds = [c.strip(" .\n") for c in raw if c.strip(" .\n")]
    return {
        "laytime_hours": int(laytime.group(1)) if laytime else 48,
        "demurrage_rate_usd_per_day": int(rate.group(1).replace(",", "")) if rate else 25000,
        "suspension_conditions": conds or ["bad weather"],
        "vessel_name": vessel.group(1).strip() if vessel else "Unknown",
    }


def parse_contract(contract_text: str = MOCK_CONTRACT) -> dict:
    """Parse contract text -> structured dict. Returns a mock result when ollama is absent."""
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

Return raw JSON string only.
"""

    if ollama is None:
        print("[step1] ollama not found — using regex mock parser on the actual contract text")
        return _mock_parse(contract_text)

    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        format="json",  # ask Ollama to emit JSON directly, reducing noise
    )
    raw = response["message"]["content"]
    return _extract_json(raw)


if __name__ == "__main__":
    result = parse_contract()
    print("Parsing Result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
