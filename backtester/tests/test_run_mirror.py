def test_run_mirror_agent_returns_full_response_shape(client):
    payload = {
        "kind": "mirror",
        "config": {"source": "agent:yolo-sapiens-eur"},
        "start_date": "2026-04-17",
        "end_date": "2026-04-30",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["equity_curve"]) >= 1
    assert {
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "vs_msci_world_pct",
        "vs_coin_flip_pct",
    } <= set(body["metrics"].keys())
    assert body["config_hash"].startswith("sha256-")


def test_run_mirror_unknown_agent_returns_400(client):
    payload = {
        "kind": "mirror",
        "config": {"source": "agent:not-a-real-agent"},
        "start_date": "2026-04-17",
        "end_date": "2026-04-30",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "agent" in response.json()["detail"].lower()


def test_run_mirror_unknown_source_kind_returns_400(client):
    payload = {
        "kind": "mirror",
        "config": {"source": "pelosi"},
        "start_date": "2026-04-17",
        "end_date": "2026-04-30",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert "agent" in response.json()["detail"].lower()


def test_run_mirror_window_with_no_data_returns_400(client):
    payload = {
        "kind": "mirror",
        "config": {"source": "agent:yolo-sapiens-eur"},
        "start_date": "2024-01-01",
        "end_date": "2024-06-01",
        "capital": 10000,
        "currency": "EUR",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 400
    assert (
        "window" in response.json()["detail"].lower()
        or "no portfolio" in response.json()["detail"].lower()
    )
