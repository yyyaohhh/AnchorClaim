"""
Regression tests locking in the three sample voyages' known-correct outcomes.
If one of these changes, it should be a deliberate decision (e.g. adjusting a
sample's numbers), not an accidental side effect of touching the pipeline.
"""
import pytest

from audit_pipeline import audit_voyage
from samples import SAMPLE_VOYAGES


@pytest.fixture(autouse=True)
def no_agent_keys(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_ocean_star_is_demurrage():
    r = audit_voyage("0xVOYAGE_OCEANSTAR", SAMPLE_VOYAGES["0xVOYAGE_OCEANSTAR"], do_settle=False)
    assert r["verdict"]["type"] == "demurrage"
    assert r["verdict"]["amount_usd"] == 46875.0
    assert r["verdict"]["settled"] is True
    assert r["check"]["consistent"] is True


def test_nord_wind_is_despatch():
    r = audit_voyage("0xVOYAGE_NORDWIND", SAMPLE_VOYAGES["0xVOYAGE_NORDWIND"], do_settle=False)
    assert r["verdict"]["type"] == "despatch"
    assert r["verdict"]["amount_usd"] == 15000.0
    assert r["verdict"]["settled"] is True


def test_delta_spirit_is_demurrage_with_a_clean_ais_sof_match():
    """Delta Spirit's sample data has no suspension claims and AIS agrees with
    the submitted log (both a signed, dispute-free demurrage case)."""
    r = audit_voyage("0xVOYAGE_DELTASPIRIT", SAMPLE_VOYAGES["0xVOYAGE_DELTASPIRIT"], do_settle=False)
    assert r["verdict"]["type"] == "demurrage"
    assert r["verdict"]["amount_usd"] == 22500.0
    assert r["verdict"]["settled"] is True
    assert r["check"]["consistent"] is True
    assert "note" not in r["verdict"]


def test_ais_override_resolves_a_falsified_log_without_holding_settlement():
    """When the submitted log and AIS disagree far beyond tolerance, there is
    no on-hold/dispute state: the agent vote resolves it by charging the
    independently-verified AIS hours instead, and it still auto-settles.
    (Covered here with a synthetic voyage since the current sample set no
    longer includes a disputed one.)"""
    voyage = dict(SAMPLE_VOYAGES["0xVOYAGE_DELTASPIRIT"])
    voyage["ais_hours"] = 79.0  # AIS disagrees sharply with the ~66h submitted log
    r = audit_voyage("0xVOYAGE_DELTASPIRIT_FRAUD_TEST", voyage, do_settle=False)
    assert r["check"]["consistent"] is False
    assert r["verdict"]["settled"] is True  # never held for a human
    assert "AIS-verified" in r["verdict"]["note"]
    assert r["receipt"]["gross_duration_hours"] == 79.0  # charged on AIS hours, not the submitted log


def test_all_sample_voyages_run_without_error():
    for vid, voyage in SAMPLE_VOYAGES.items():
        r = audit_voyage(vid, voyage, do_settle=False)
        assert r["verdict"]["settled"] is True  # nothing is ever held for a human
