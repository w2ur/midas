from backtester.app import _config_hash
from backtester.schemas import SignalConfig, SignalRunRequest


def _req(**over):
    base = dict(
        kind="signal",
        config=SignalConfig(
            universe="sp500",
            selector="golden-cross",
            manager="equal-weight",
            max_positions=20,
            max_position_pct=10.0,
            min_hold_days=5,
        ),
        start_date="2018-01-01",
        end_date="2024-01-01",
        capital=10000.0,
        currency="EUR",
    )
    base.update(over)
    return SignalRunRequest(**base)


def test_same_config_same_hash():
    assert _config_hash(_req()) == _config_hash(_req())


def test_different_capital_changes_hash():
    assert _config_hash(_req()) != _config_hash(_req(capital=5000.0))


def test_hash_has_sha256_prefix():
    assert _config_hash(_req()).startswith("sha256-")
