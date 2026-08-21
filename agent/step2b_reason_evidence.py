"""
Step 2b: Reason over the multi-source evidence.

evidence_sources.py only fetches six independent signals (AIS, weather, satellite,
port community, NOR, news). This step is where the agent actually reasons over them:
for each suspension the charterer claims, is it corroborated by an independent source,
so it should reduce the counted laytime? And does the AIS/SoF gap look like a genuine
data conflict?

Uses the same local Ollama LLM as step 1 when available; falls back to a deterministic
rule ("corroborated unless a source that speaks to this claim contradicts it") so the
pipeline still runs end to end offline.
"""

import json
import re

try:
    import ollama
except ImportError:
    ollama = None  # fall back to deterministic reasoning when ollama is absent

MODEL = "qwen2.5"


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in output:\n{text}")
    return json.loads(match.group(0))


def _deterministic_reason(suspension_events: list, sources: dict) -> dict:
    """A claim is corroborated unless the one source that speaks to it disagrees.
    Weather sources rule on weather claims, news/notices rule on strike/closure/
    congestion claims; claims outside those categories have nothing to contradict
    them, so they stand."""
    weather_unsupported = {e["start"] for e in sources["weather"]["unsupported"]}
    weather_confirmed = {e["start"] for e in sources["weather"]["corroborated"]}
    news_unsupported = {e["start"] for e in sources["news"]["unsupported"]}
    news_confirmed = {e["start"] for e in sources["news"]["corroborated"]}

    decisions = []
    for e in suspension_events:
        reason = e["reason"].lower()
        if "weather" in reason and e["start"] in weather_unsupported:
            corroborated, why = False, "weather records show no bad weather at this port/time"
        elif "weather" in reason and e["start"] in weather_confirmed:
            corroborated, why = True, "weather source confirms bad weather at this port/time"
        elif any(k in reason for k in ("strike", "closure", "congestion")) and e["start"] in news_unsupported:
            corroborated, why = False, "no public notice corroborates this claim"
        elif any(k in reason for k in ("strike", "closure", "congestion")) and e["start"] in news_confirmed:
            corroborated, why = True, "a public notice corroborates this claim"
        else:
            corroborated, why = True, "no independent source contradicts this claim"
        decisions.append({**e, "corroborated": corroborated, "explanation": why})

    dispute_supported = sources["satellite"]["ok"] and sources["port_community"]["ok"]
    ais_sof_note = (
        "satellite imagery and the port community system both align with the submitted timeline"
        if dispute_supported else
        "satellite imagery or the port community system fail to confirm the submitted timeline"
    )
    return {"mode": "deterministic", "suspension_decisions": decisions, "ais_sof_note": ais_sof_note}


def reason_evidence(contract: dict, voyage: dict, sources: dict) -> dict:
    """Reason over the six independent sources to decide which claimed suspensions
    actually happened. Returns per-claim corroboration (feeds the laytime deduction
    in step 2) plus a note on whether the AIS/SoF gap looks genuine."""
    suspension_events = voyage.get("suspension_events") or []
    if not suspension_events:
        return {"mode": "n/a", "suspension_decisions": [], "ais_sof_note": ""}

    if ollama is None:
        print("[step2b] ollama not found — using deterministic evidence reasoning")
        return _deterministic_reason(suspension_events, sources)

    prompt = f"""
You are a maritime claims auditor. A charterer claims the following laytime
suspensions, which are only valid if the contract allows that reason AND an
independent source corroborates it actually happened.

Contract's allowed suspension conditions: {contract.get("suspension_conditions", [])}

Claimed suspensions:
{json.dumps(suspension_events, indent=2)}

Independent evidence gathered for this voyage:
- Weather source: {json.dumps(sources["weather"], indent=2)}
- Satellite imagery: {json.dumps(sources["satellite"], indent=2)}
- Port community system: {json.dumps(sources["port_community"], indent=2)}
- Notice of Readiness: {json.dumps(sources["nor"], indent=2)}
- News / public notices: {json.dumps(sources["news"], indent=2)}

For each claimed suspension, decide if it is corroborated by the evidence above
(a source finding no support for it means it is NOT corroborated; a claim with no
relevant source to contradict it stands as corroborated). Also note whether the
satellite and port-community records support the submitted port-log timeline.

Return a JSON object:
{{
  "suspension_decisions": [
    {{"reason": "...", "start": "...", "end": "...", "corroborated": true/false, "explanation": "..."}}
  ],
  "ais_sof_note": "one sentence on whether satellite/port-community data supports the submitted timeline"
}}
Return raw JSON string only.
"""

    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        format="json",
    )
    parsed = _extract_json(response["message"]["content"])
    parsed["mode"] = "llm"
    return parsed
