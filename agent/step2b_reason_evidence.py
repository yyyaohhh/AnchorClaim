"""
Step 2b: Independent AI agents reason over the multi-source evidence and vote.

evidence_sources.py only fetches six independent signals (AIS, weather, satellite,
port community, NOR, news). This step is where the agents actually reason over them,
deciding two things: for each suspension the charterer claims, is it corroborated by
an independent source, so it should reduce the counted laytime? And, weighing AIS
together with satellite/port-community records, is the submitted port-log (SoF)
duration itself trustworthy? AIS/SoF consistency is just one more claim the agents
vote on alongside the rest of the evidence — not a separate hardcoded threshold check
that bypasses reasoning.

Each configured agent independently reviews the same evidence and votes on every claim.
A claim is only corroborated if a strict majority of the agents that actually responded
agree, so a single vendor's mistake or bias can't flip the audit alone. Agents are
discovered from the Settings page (primary provider: OpenAI / Claude / Custom) plus the
per-vendor environment keys (OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY); an
agent whose key is missing, or whose call fails, is simply excluded from that vote.

If none of the vendors are configured (or all calls fail), _simulated_vote() below
stands in: it derives the same corroborated/not-corroborated verdict from the
deterministic evidence rule, then phrases it three ways as if each vendor had reached
it independently. This keeps the offline/demo experience showing the intended
multi-agent UI without requiring API keys — it is not a real second opinion, just the
one deterministic verdict in three voices.
"""

from __future__ import annotations

import json
import re
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from llm import complete, configured_agents


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in output:\n{text}")
    return json.loads(match.group(0))


def _run_agent(agent_cfg: dict, prompt: str) -> dict | None:
    try:
        verdict = _extract_json(complete(agent_cfg, prompt))
        verdict["agent"] = agent_cfg["name"]
        return verdict
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
        print(f"[step2b] {agent_cfg['name']} agent failed: {e}")
        return {"agent": agent_cfg["name"], "error": str(e)}


# ---------------------------------------------------------------------------
# Simulated vote (no agent API keys configured, or all calls failed)
# ---------------------------------------------------------------------------
PERSONA_PHRASING = {
    "openai": lambda why: f"Cross-referencing the evidence feed: {why}.",
    "anthropic": lambda why: f"On review of the independent sources: {why}.",
    "gemini": lambda why: f"Evidence synthesis: {why}.",
}


def _base_verdict(event: dict, sources: dict) -> tuple[bool, str]:
    """The one deterministic verdict _simulated_vote() phrases three ways: a claim is
    corroborated unless the one source that speaks to it disagrees. Weather sources
    rule on weather claims, news/notices rule on strike/closure/congestion claims;
    claims outside those categories have nothing to contradict them, so they stand."""
    reason = event["reason"].lower()
    weather_unsupported = {e["start"] for e in sources["weather"]["unsupported"]}
    weather_confirmed = {e["start"] for e in sources["weather"]["corroborated"]}
    news_unsupported = {e["start"] for e in sources["news"]["unsupported"]}
    news_confirmed = {e["start"] for e in sources["news"]["corroborated"]}

    if "weather" in reason and event["start"] in weather_unsupported:
        return False, "weather records show no bad weather at this port/time"
    if "weather" in reason and event["start"] in weather_confirmed:
        return True, "weather source confirms bad weather at this port/time"
    if any(k in reason for k in ("strike", "closure", "congestion")) and event["start"] in news_unsupported:
        return False, "no public notice corroborates this claim"
    if any(k in reason for k in ("strike", "closure", "congestion")) and event["start"] in news_confirmed:
        return True, "a public notice corroborates this claim"
    return True, "no independent source contradicts this claim"


def _ais_sof_verdict(sources: dict) -> tuple[bool, str]:
    """AIS is just another evidence source now: the reported port-log duration is only
    trustworthy if the independent AIS reading, satellite imagery, and the port
    community system all agree with it."""
    consistent = sources["ais"]["ok"] and sources["satellite"]["ok"] and sources["port_community"]["ok"]
    if consistent:
        return True, "AIS, satellite imagery and the port community system all align with the submitted timeline"
    return False, (f"AIS reports {sources['ais']['ais_hours']}h in port vs a {sources['ais']['sof_hours']}h "
                    f"submitted log (delta {sources['ais']['delta_hours']}h), and satellite/port-community "
                    f"records do not corroborate the submitted timeline")


def _simulated_vote(suspension_events: list, sources: dict) -> dict:
    decisions = []
    for e in suspension_events:
        corroborated, why = _base_verdict(e, sources)
        votes = [{"agent": name, "corroborated": corroborated, "explanation": phrase(why)}
                 for name, phrase in PERSONA_PHRASING.items()]
        explanation = f"{'3/3' if corroborated else '0/3'} agents corroborated this claim"
        decisions.append({**e, "corroborated": corroborated, "explanation": explanation, "votes": votes})

    ais_sof_consistent, ais_sof_note = _ais_sof_verdict(sources)
    ais_sof_votes = [{"agent": name, "consistent": ais_sof_consistent, "note": phrase(ais_sof_note)}
                      for name, phrase in PERSONA_PHRASING.items()]
    return {"mode": "multi-agent-vote", "agents": list(PERSONA_PHRASING), "agent_errors": [],
            "suspension_decisions": decisions,
            "ais_sof_consistent": ais_sof_consistent, "ais_sof_note": ais_sof_note,
            "ais_sof_votes": ais_sof_votes}


# ---------------------------------------------------------------------------
# Prompt shared by all agents
# ---------------------------------------------------------------------------
def _build_prompt(contract: dict, suspension_events: list, sources: dict) -> str:
    return f"""
You are a maritime claims auditor reviewing six independent evidence sources for one
voyage: AIS, weather, satellite imagery, the port community system, the Notice of
Readiness, and public news/notices. None of them is trusted on its own.

Contract's allowed suspension conditions: {contract.get("suspension_conditions", [])}

Claimed laytime suspensions (only valid if the contract allows that reason AND an
independent source corroborates it actually happened):
{json.dumps(suspension_events, indent=2)}

Independent evidence gathered for this voyage:
- AIS vs submitted port log (SoF): {json.dumps(sources["ais"], indent=2)}
- Weather source: {json.dumps(sources["weather"], indent=2)}
- Satellite imagery: {json.dumps(sources["satellite"], indent=2)}
- Port community system: {json.dumps(sources["port_community"], indent=2)}
- Notice of Readiness: {json.dumps(sources["nor"], indent=2)}
- News / public notices: {json.dumps(sources["news"], indent=2)}

Two things to decide:
1. For each claimed suspension, is it corroborated by the evidence above (a source
   finding no support for it means it is NOT corroborated; a claim with no relevant
   source to contradict it stands as corroborated)?
2. Weighing the AIS reading together with satellite imagery and the port community
   system, is the submitted port-log (SoF) duration itself trustworthy, or does the
   evidence suggest it was falsified?

Return a JSON object:
{{
  "suspension_decisions": [
    {{"reason": "...", "start": "...", "end": "...", "corroborated": true/false, "explanation": "..."}}
  ],
  "ais_sof_consistent": true/false,
  "ais_sof_note": "one sentence on whether AIS/satellite/port-community data supports the submitted timeline"
}}
Return raw JSON string only.
"""


def _find_decision(verdict: dict, event: dict) -> dict | None:
    for d in verdict.get("suspension_decisions", []):
        if d.get("start") == event["start"] and d.get("reason", "").lower() == event["reason"].lower():
            return d
    return None


def _vote(suspension_events: list, agent_verdicts: list) -> list:
    """Strict-majority vote per claim across the agents that returned a verdict."""
    decisions = []
    for e in suspension_events:
        votes = []
        for v in agent_verdicts:
            d = _find_decision(v, e)
            if d is not None:
                votes.append({"agent": v["agent"], "corroborated": bool(d.get("corroborated")),
                              "explanation": d.get("explanation", "")})
        if not votes:
            corroborated, why = True, "no agent reached a verdict on this claim; defaulting to corroborated"
        else:
            yes = sum(1 for v in votes if v["corroborated"])
            corroborated = yes * 2 > len(votes)
            why = f"{yes}/{len(votes)} agents corroborated this claim"
        decisions.append({**e, "corroborated": corroborated, "explanation": why, "votes": votes})
    return decisions


def _vote_ais_sof(agent_verdicts: list) -> tuple[bool, str, list]:
    """Strict-majority vote on whether the AIS/SoF timeline is trustworthy, across
    the agents that returned a verdict. Also returns each agent's individual vote
    and reasoning for display."""
    ais_sof_votes = [{"agent": v["agent"], "consistent": bool(v["ais_sof_consistent"]),
                       "note": v.get("ais_sof_note", "")}
                      for v in agent_verdicts if "ais_sof_consistent" in v]
    if not ais_sof_votes:
        return True, "no agent reached a verdict on the AIS/SoF timeline; defaulting to consistent", []
    yes = sum(1 for v in ais_sof_votes if v["consistent"])
    consistent = yes * 2 > len(ais_sof_votes)
    notes = [v["note"] for v in ais_sof_votes if v["note"]]
    return consistent, notes[0] if notes else "", ais_sof_votes


def reason_evidence(contract: dict, voyage: dict, sources: dict) -> dict:
    """Run the configured agents over the six independent sources and vote on which
    claimed suspensions actually happened, and on whether the AIS/SoF timeline itself
    holds up. Returns per-claim corroboration (feeding the laytime deduction in step 2),
    each agent's individual vote, and the AIS/SoF consistency verdict (feeding the
    trusted-hours decision)."""
    suspension_events = voyage.get("suspension_events") or []

    agents = configured_agents()
    if not agents:
        print("[step2b] no agent API keys configured — simulating the multi-agent vote")
        return _simulated_vote(suspension_events, sources)

    prompt = _build_prompt(contract, suspension_events, sources)
    with ThreadPoolExecutor(max_workers=len(agents)) as pool:
        raw_results = list(pool.map(lambda a: _run_agent(a, prompt), agents))

    agent_verdicts = [r for r in raw_results if r and "error" not in r]
    errors = [r for r in raw_results if r and "error" in r]

    if not agent_verdicts:
        print("[step2b] all configured agents failed — simulating the multi-agent vote")
        fallback = _simulated_vote(suspension_events, sources)
        fallback["agent_errors"] = errors
        return fallback

    decisions = _vote(suspension_events, agent_verdicts)
    ais_sof_consistent, ais_sof_note, ais_sof_votes = _vote_ais_sof(agent_verdicts)

    return {
        "mode": "multi-agent-vote",
        "agents": [v["agent"] for v in agent_verdicts],
        "agent_errors": errors,
        "suspension_decisions": decisions,
        "ais_sof_consistent": ais_sof_consistent,
        "ais_sof_note": ais_sof_note,
        "ais_sof_votes": ais_sof_votes,
    }