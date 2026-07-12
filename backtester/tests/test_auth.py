def test_catalog_open_when_secret_unset(client, monkeypatch):
    monkeypatch.delenv("BACKTESTER_SECRET", raising=False)
    assert client.get("/catalog").status_code == 200


def test_catalog_rejects_missing_secret_when_configured(client, monkeypatch):
    monkeypatch.setenv("BACKTESTER_SECRET", "s3cret")
    assert client.get("/catalog").status_code == 401


def test_catalog_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("BACKTESTER_SECRET", "s3cret")
    assert (
        client.get("/catalog", headers={"X-Backtester-Secret": "nope"}).status_code
        == 401
    )


def test_catalog_accepts_correct_secret(client, monkeypatch):
    monkeypatch.setenv("BACKTESTER_SECRET", "s3cret")
    r = client.get("/catalog", headers={"X-Backtester-Secret": "s3cret"})
    assert r.status_code == 200


def test_health_is_always_exempt(client, monkeypatch):
    monkeypatch.setenv("BACKTESTER_SECRET", "s3cret")
    assert client.get("/health").status_code == 200
