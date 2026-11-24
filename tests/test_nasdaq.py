from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from nasdaq import alerts
from nasdaq.app import create_app, voice
from nasdaq.market import ET, MINUTE, Market, baseline_dates, price_changes


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    app = create_app(alerts=alerts.Alerts(tmp_path / "alerts.sqlite"), start_monitor=False)
    with TestClient(app) as client:
        yield client


def test_demo_site_covers_chart_ratings_and_manual_call(browser):
    assert browser.get("/").status_code == 200
    snapshot = browser.get("/api/market").json()
    assert snapshot["mode"] == "demo"
    assert len(snapshot["stocks"]) == 10
    assert all(left["volume"] >= right["volume"] for left, right in zip(
        snapshot["stocks"], snapshot["stocks"][1:]
    ))
    stock = snapshot["stocks"][0]
    symbol = stock["symbol"]
    assert set(stock["changes"]) == {"1D", "1W", "1M", "1Y"}
    for unit in stock["changes"]:
        series = browser.get(f"/api/stocks/{symbol}", params={"period": unit}).json()
        assert len(series["points"]) > 1
        assert series["points"][-1][1] == stock["price"]
    assert browser.get(f"/api/stocks/{symbol}/ratings").json()["ratings"]["buy"] > 0

    assert browser.get("/api/alerts").json() == []
    created = browser.post("/api/alerts", json={
        "symbol": symbol, "target": 200.50, "direction": "above",
    })
    assert created.status_code == 201
    alert_id = created.json()["id"]
    path = f"/api/alerts/{alert_id}"
    assert browser.post(path + "/call").status_code == 409
    assert browser.post(path + "/trigger", headers={"Origin": "https://other.test"}).status_code == 403
    with TestClient(browser.app) as other:
        assert other.get("/api/alerts").json() == []
        assert other.post(path + "/trigger").status_code == 404

    manual = browser.post(path + "/trigger").json()
    assert manual["trigger_source"] == "manual"
    assert manual["observed_price"] == 200.50
    assert browser.post(path + "/trigger").json() == manual
    script = browser.post(path + "/call").json()
    assert "müsait misiniz?" in script["greeting"]
    assert "200 virgül 5 dolar" in script["report"]
    assert "deneme amacıyla" in script["report"]
    assert script["question"] == "Başka sorunuz var mı?"
    assert "İyi günler" in script["goodbye"]
    assert browser.delete(path).status_code == 204
    assert browser.get("/api/alerts").json() == []


def test_simulated_call_speaks_only_allowed_cues_with_antalia(browser, monkeypatch):
    spoken = []

    def synthesize(text, *, model):
        spoken.append(text)
        assert model == voice.ANTALIA_MODEL
        return b"RIFF-antalia-test"

    monkeypatch.setattr(voice, "speak", synthesize)
    browser.get("/api/alerts")
    alert = browser.post("/api/alerts", json={
        "symbol": "NVDA", "target": 200, "direction": "above",
    }).json()
    path = f"/api/alerts/{alert['id']}"
    assert browser.get(path + "/speech/greeting").status_code == 409
    browser.post(path + "/trigger")
    script = browser.post(path + "/call").json()
    for cue in ("greeting", "report", "goodbye", "unavailable"):
        response = browser.get(f"{path}/speech/{cue}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content == b"RIFF-antalia-test"
    assert spoken == [
        script["greeting"], script["report"] + " " + script["question"],
        script["goodbye"], script["unavailable"],
    ]
    assert browser.get(path + "/speech/custom").status_code == 422
    with TestClient(browser.app) as other:
        other.get("/api/alerts")
        assert other.get(path + "/speech/greeting").status_code == 404


def test_live_alarm_uses_closed_eligible_bars_and_persists(tmp_path, monkeypatch):
    created = 1000 * MINUTE + 20_000
    monkeypatch.setattr(alerts, "now_ms", lambda: created)
    path = tmp_path / "market.sqlite"
    store = alerts.Alerts(path)
    for direction in ("above", "below"):
        store.create("owner", "market", "TEST", 100, direction)
    store.create("owner", "demo", "TEST", 100, "above")

    class Feed:
        delay_minutes = 15

        def alert_bars(self, symbol, start, end):
            assert symbol == "TEST"
            return [
                dict(t=stamp * MINUTE, h=high, l=low)
                for stamp, high, low in [(1000, 120, 80), (1001, 102, 101), (1002, 98, 97)]
                if start <= stamp * MINUTE <= end
            ]

    store.check(Feed(), 1016 * MINUTE)
    assert all(item["triggered_at"] is None for item in store.list("owner", "market"))
    store.check(Feed(), 1017 * MINUTE)
    above, below = store.list("owner", "market")
    assert above["observed_price"] == 102 and above["trigger_source"] == "market"
    assert below["triggered_at"] is None
    reopened = alerts.Alerts(path)
    reopened.check(Feed(), 1018 * MINUTE)
    above, below = reopened.list("owner", "market")
    assert above["triggered_at"] == 1001 * MINUTE
    assert below["observed_price"] == 97 and below["triggered_at"] == 1002 * MINUTE
    assert reopened.list("owner", "demo")[0]["triggered_at"] is None


def test_provider_ranking_filters_exchange_and_merges_analyst_categories():
    now = datetime.now(ET)
    stamp = int(now.timestamp() * 1000)

    def route(request):
        path = request.url.path
        if path == "/v3/reference/tickers":
            return httpx.Response(200, json={"status": "OK", "results": [
                dict(ticker="FIRST", name="First Corporation", primary_exchange="XNAS", type="CS"),
                dict(ticker="SECOND", name="Second Corporation", primary_exchange="XNAS", type="ADRC"),
                dict(ticker="FUND", name="Other Fund", primary_exchange="XNAS", type="ETF"),
                dict(ticker="NYSE", name="Other Exchange", primary_exchange="XNYS", type="CS"),
            ]})
        if path.endswith("/tickers"):
            return httpx.Response(200, json={"status": "OK", "tickers": [
                dict(ticker=symbol, day=dict(c=100, v=volume), min=dict(t=stamp, c=100))
                for symbol, volume in (("SECOND", 200), ("FIRST", 500), ("FUND", 900), ("NYSE", 999))
            ]})
        if path.endswith("/grades-consensus"):
            return httpx.Response(200, json=[{
                "symbol": "FIRST", "strongBuy": 2, "buy": 9, "hold": 3, "sell": 5, "strongSell": 1,
            }])
        return httpx.Response(200, json={"status": "OK", "results": [
            dict(t=int((now - timedelta(days=1)).timestamp() * 1000), c=80),
        ]})

    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        market = Market(client, "stub-massive", "stub-fmp", 15)
        result = market.ranking()
        assert result["mode"] == "market"
        assert [stock["symbol"] for stock in result["stocks"]] == ["FIRST", "SECOND"]
        assert result["stocks"][0]["changes"]["1D"] == 25
        assert market.ratings("FIRST") == {"buy": 11, "hold": 3, "sell": 6}


def test_calendar_baselines_for_leap_day():
    day = datetime(2024, 2, 29, 16, tzinfo=ET)
    assert baseline_dates(day.date())["1Y"].isoformat() == "2023-02-28"
    days = [day - timedelta(days=8), day - timedelta(days=1)]
    bars = [dict(t=int(when.timestamp() * 1000), c=close) for when, close in zip(days, [80, 100])]
    assert price_changes(120, day.date(), bars) == {"1D": 20, "1W": 50, "1M": None, "1Y": None}
