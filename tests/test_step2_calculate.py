from step2_calculate import calculate_demurrage, cross_check_ais, gross_duration_hours

BASE_CONTRACT = {
    "laytime_hours": 48,
    "demurrage_rate_usd_per_day": 24000,
    "suspension_conditions": ["bad weather"],
    "vessel_name": "MV Test",
}
BASE_LOG = {
    "vessel_name": "MV Test",
    "nor_tendered": "2026/08/10 09:00:00",
    "departure_time": "2026/08/14 12:00:00",  # NOR to departure = 99h
}


def test_gross_duration_hours_counts_from_nor_not_berthing():
    log = {**BASE_LOG, "berthing_time": "2026/08/10 15:00:00"}  # berthing 6h after NOR
    assert gross_duration_hours(log) == 99.0


def test_demurrage_deducts_only_matching_suspensions():
    suspensions = [
        {"reason": "bad weather", "start": "2026/08/11 00:00:00", "end": "2026/08/11 06:00:00"},  # 6h, allowed
        {"reason": "strikes", "start": "2026/08/12 00:00:00", "end": "2026/08/12 06:00:00"},  # 6h, not in contract
    ]
    r = calculate_demurrage(BASE_CONTRACT, BASE_LOG, suspensions)
    assert r["excluded_hours"] == 6.0  # only the bad-weather window
    assert r["counted_laytime_hours"] == 93.0  # 99 - 6
    assert r["excess_time_hours"] == 45.0  # 93 - 48
    assert r["total_penalty_usd"] == 45 / 24 * 24000
    assert r["total_despatch_usd"] == 0.0


def test_despatch_when_counted_hours_under_allowance():
    short_log = {**BASE_LOG, "departure_time": "2026/08/11 09:00:00"}  # NOR to departure = 24h
    contract = {**BASE_CONTRACT, "laytime_hours": 48}
    r = calculate_demurrage(contract, short_log, [])
    assert r["excess_time_hours"] == 0.0
    assert r["total_penalty_usd"] == 0.0
    assert r["saved_time_hours"] == 24.0
    # default despatch rate is half the demurrage rate per standard charter-party convention
    assert r["despatch_rate_usd_per_day"] == 12000.0
    assert r["total_despatch_usd"] == 24 / 24 * 12000


def test_gross_hours_override_replaces_submitted_log():
    r = calculate_demurrage(BASE_CONTRACT, BASE_LOG, [], gross_hours_override=200.0)
    assert r["gross_duration_hours"] == 200.0
    assert r["counted_laytime_hours"] == 200.0


def test_cross_check_ais_tolerance():
    assert cross_check_ais(99.0, 100.0)["consistent"] is True
    assert cross_check_ais(99.0, 150.0)["consistent"] is False
