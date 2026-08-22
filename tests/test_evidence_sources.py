from evidence_sources import (
    gather_evidence, verify_ais, verify_nor, verify_port_community, verify_satellite, verify_weather,
)


def test_verify_ais_flags_large_gap_as_not_ok():
    assert verify_ais(42.0, 42.5)["ok"] is True
    result = verify_ais(42.0, 79.0)
    assert result["ok"] is False
    assert result["delta_hours"] == 37.0


def test_verify_weather_matches_known_mock_table():
    events = [{"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"}]
    corroborated = verify_weather("Port of Singapore", events)
    assert corroborated["ok"] is True
    assert len(corroborated["corroborated"]) == 1

    unsupported = verify_weather("Port of Tokyo", events)
    assert unsupported["ok"] is False
    assert len(unsupported["unsupported"]) == 1


def test_verify_satellite_mock_keys_off_ais_sof_delta():
    confirmed = verify_satellite("123", "Port of Singapore", "b", "d", sof_hours=42.0, ais_hours=42.5)
    assert confirmed["ok"] is True
    not_confirmed = verify_satellite("123", "Port of Singapore", "b", "d", sof_hours=42.0, ais_hours=79.0)
    assert not_confirmed["ok"] is False


def test_verify_port_community_mock_reflects_ais_ground_truth_not_the_submitted_log():
    """Regression: the mock used to echo the submitted log back at itself (delta
    always 0), which could never catch a falsified log. It must now derive the
    'official' record from the independent AIS reading instead."""
    log = {"berthing_time": "2026/08/02 02:00:00", "departure_time": "2026/08/04 18:00:00"}
    result = verify_port_community("Port of Tokyo", log, sof_hours=66.0, ais_hours=66.0)
    assert result["delta_hours"] == 0.0
    assert result["ok"] is True

    falsified = verify_port_community("Port of Tokyo", log, sof_hours=42.0, ais_hours=79.0)
    assert falsified["delta_hours"] == 37.0
    assert falsified["ok"] is False


def test_verify_nor_requires_timestamp_after_arrival():
    ok_log = {"nor_tendered": "2026/08/10 09:00:00", "arrival_anchorage": "2026/08/10 08:00:00"}
    assert verify_nor(ok_log)["ok"] is True

    missing = verify_nor({})
    assert missing["present"] is False
    assert missing["ok"] is False


def test_gather_evidence_integration_for_a_clean_voyage():
    voyage = {
        "port": "Port of Singapore",
        "imo": "9321483",
        "ais_port_log": {
            "nor_tendered": "2026/08/10 09:00:00",
            "berthing_time": "2026/08/10 12:00:00",
            "departure_time": "2026/08/14 12:00:00",
        },
        "suspension_events": [
            {"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"},
        ],
        "ais_hours": 99.0,
    }
    ev = gather_evidence(voyage, sof_hours=99.0)
    assert ev["sources_ok"] == ev["sources_total"] == 6
    assert ev["unsupported_claims"] == []
    assert "ais" in ev["sources"]
