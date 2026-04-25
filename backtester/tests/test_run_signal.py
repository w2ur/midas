def test_run_signal_returns_full_response_shape(client):
    payload = {
        "kind": "signal",
        "config": {
            "universe": "classic-60-40",
            "selector": "buy-and-hold",
            "manager": "fixed-60-40",
            "max_positions": 2,
            "max_position_pct": 100.0,
            "min_hold_days": 0,
        },
        "start_date": "2024-05-01",
        "end_date": "2024-10-31",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "equity_curve" in body and len(body["equity_curve"]) > 50
    assert {
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "vs_msci_world_pct",
        "vs_coin_flip_pct",
    } <= set(body["metrics"].keys())
    assert isinstance(body["trades"], list)
    assert body["config_hash"].startswith("sha256-")


def test_run_signal_unknown_universe_returns_400(client):
    payload = {
        "kind": "signal",
        "config": {
            "universe": "not-a-real-universe",
            "selector": "buy-and-hold",
            "manager": "fixed-60-40",
            "max_positions": 5,
            "max_position_pct": 20.0,
            "min_hold_days": 0,
        },
        "start_date": "2024-05-01",
        "end_date": "2024-06-01",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "universe" in response.json()["detail"].lower()
