import os

import pytest

from evidence_sources import gather_evidence
from step2b_reason_evidence import reason_evidence

CONTRACT = {"suspension_conditions": ["bad weather", "port congestion"]}


@pytest.fixture(autouse=True)
def no_agent_keys(monkeypatch):
    """Force the deterministic fallback so these tests don't depend on real
    network calls or on whichever API keys happen to be set in the shell."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _voyage(port, suspensions, ais_hours, sof_hours):
    return {
        "port": port,
        "imo": "0000000",
        "ais_port_log": {
            "nor_tendered": "2026/08/10 09:00:00",
            "berthing_time": "2026/08/10 12:00:00",
            "departure_time": "2026/08/14 12:00:00",
        },
        "suspension_events": suspensions,
        "ais_hours": ais_hours,
        "_sof_hours": sof_hours,
    }


def test_corroborated_claim_is_deducted():
    voyage = _voyage("Port of Singapore",
                      [{"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"}],
                      ais_hours=99.0, sof_hours=99.0)
    sources = gather_evidence(voyage, voyage["_sof_hours"])
    result = reason_evidence(CONTRACT, voyage, sources["sources"])
    assert result["mode"] == "multi-agent-vote"
    assert len(result["suspension_decisions"]) == 1
    assert result["suspension_decisions"][0]["corroborated"] is True
    assert result["ais_sof_consistent"] is True


def test_uncorroborated_claim_is_not_deducted():
    """A claim with no supporting evidence should not reduce counted laytime,
    even though it's an allowed exclusion reason under the contract."""
    voyage = _voyage("Port of Tokyo",  # not in the mock weather table
                      [{"reason": "bad weather", "start": "2026/09/01 00:00:00", "end": "2026/09/01 06:00:00"}],
                      ais_hours=99.0, sof_hours=99.0)
    sources = gather_evidence(voyage, voyage["_sof_hours"])
    result = reason_evidence(CONTRACT, voyage, sources["sources"])
    assert result["suspension_decisions"][0]["corroborated"] is False


def test_ais_sof_inconsistency_is_flagged():
    voyage = _voyage("Port of Tokyo", [], ais_hours=79.0, sof_hours=42.0)
    sources = gather_evidence(voyage, voyage["_sof_hours"])
    result = reason_evidence(CONTRACT, voyage, sources["sources"])
    assert result["ais_sof_consistent"] is False
    assert len(result["ais_sof_votes"]) == 3
    assert all(v["consistent"] is False for v in result["ais_sof_votes"])


def test_each_agent_has_an_individual_vote_not_just_an_aggregate():
    voyage = _voyage("Port of Singapore",
                      [{"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"}],
                      ais_hours=99.0, sof_hours=99.0)
    sources = gather_evidence(voyage, voyage["_sof_hours"])
    result = reason_evidence(CONTRACT, voyage, sources["sources"])
    votes = result["suspension_decisions"][0]["votes"]
    assert {v["agent"] for v in votes} == {"openai", "anthropic", "gemini"}
