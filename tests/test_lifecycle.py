import lifecycle


def test_sign_and_funding_status_roundtrip(tmp_path):
    lifecycle.configure(str(tmp_path))
    voyage_id = "0xTEST_SIGN_ROUNDTRIP"

    status = lifecycle.funding_status(voyage_id)
    assert status["funded"] is False
    assert status["signed"] == {"charterer": False, "shipowner": False}

    lifecycle.sign(voyage_id, "charterer")
    status = lifecycle.funding_status(voyage_id)
    assert status["funded"] is False
    assert status["signed"]["charterer"] is True

    status = lifecycle.sign(voyage_id, "shipowner")
    assert status["funded"] is True
    assert status["signatures"]["charterer"]["signature"].startswith("0x")


def test_sign_rejects_unknown_party(tmp_path):
    lifecycle.configure(str(tmp_path))
    try:
        lifecycle.sign("0xTEST_BAD_PARTY", "captain")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_funding_state_persists_across_a_fresh_configure_call(tmp_path):
    """Simulates a server restart: configure() is called again pointing at the
    same directory, and a previously-signed voyage should still show funded
    instead of resetting - this is the whole point of moving off in-memory-only
    state, so a restart mid-demo doesn't wipe collected signatures."""
    cache_dir = str(tmp_path)
    voyage_id = "0xTEST_PERSISTENCE"

    lifecycle.configure(cache_dir)
    lifecycle.sign(voyage_id, "charterer")
    lifecycle.sign(voyage_id, "shipowner")

    lifecycle._FUNDING_STATE.clear()  # simulate the in-memory state a restart would wipe
    lifecycle.configure(cache_dir)
    assert lifecycle.funding_status(voyage_id)["funded"] is True


def test_reset_funding_clears_both_memory_and_disk(tmp_path):
    cache_dir = str(tmp_path)
    voyage_id = "0xTEST_RESET"
    lifecycle.configure(cache_dir)
    lifecycle.sign(voyage_id, "charterer")

    lifecycle.reset_funding(voyage_id)
    assert lifecycle.funding_status(voyage_id)["funded"] is False

    lifecycle._FUNDING_STATE.clear()
    lifecycle.configure(cache_dir)
    assert lifecycle.funding_status(voyage_id)["signed"]["charterer"] is False


def test_monitoring_log_spans_nor_to_departure():
    voyage = {"ais_port_log": {
        "nor_tendered": "2026/08/10 09:00:00",
        "departure_time": "2026/08/14 12:00:00",
    }}
    log = lifecycle.monitoring_log(voyage)
    assert len(log) == 5
    assert log[0]["day"] == 1
    assert "ready for final audit" in log[-1]["status"]
    assert all("monitoring" in e["status"] for e in log[:-1])


def test_contract_template_mirrors_the_escrow_fund_signature():
    voyage = {"escrow": {"freight": 500000, "deposit": 100000}}
    contract = {"laytime_hours": 48}
    tpl = lifecycle.contract_template("0xVOYAGE_X", voyage, contract)
    assert tpl["function"] == "fund"
    assert tpl["args"]["freight"] == 500000
    assert tpl["args"]["deposit"] == 100000
    assert tpl["args"]["laytimeHours"] == 48
