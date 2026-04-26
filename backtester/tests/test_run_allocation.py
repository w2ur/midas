def test_run_allocation_returns_full_response_shape(client):
    payload = {
        "kind": "allocation",
        "config": {
            "weights": [
                {"ticker": "VOO", "weight": 60.0},
                {"ticker": "BND", "weight": 40.0},
            ],
            "rebalance_cadence": "monthly",
        },
        "start_date": "2024-05-01",
        "end_date": "2024-10-31",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["equity_curve"]) > 50
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


def test_run_allocation_rejects_weights_not_summing_to_100(client):
    payload = {
        "kind": "allocation",
        "config": {
            "weights": [
                {"ticker": "VOO", "weight": 60.0},
                {"ticker": "BND", "weight": 30.0},  # 90% total
            ],
            "rebalance_cadence": "monthly",
        },
        "start_date": "2024-05-01",
        "end_date": "2024-06-01",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "100" in response.json()["detail"]


def test_run_allocation_rejects_unknown_ticker(client):
    payload = {
        "kind": "allocation",
        "config": {
            "weights": [
                {"ticker": "NOTAREALTICKERXYZ", "weight": 100.0},
            ],
            "rebalance_cadence": "monthly",
        },
        "start_date": "2024-05-01",
        "end_date": "2024-06-01",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "NOTAREALTICKERXYZ" in response.json()["detail"]
