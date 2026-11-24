import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from freya import voice

from .alerts import Alerts
from .market import Market, MarketError

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "freya_nasdaq"
COOKIE_RE = re.compile(r"[a-f0-9]{32}")
logger = logging.getLogger(__name__)

Period = Literal["1D", "1W", "1M", "1Y"]
Cue = Literal["greeting", "report", "goodbye", "unavailable"]
CALL_LINES = {
    "greeting": "Merhaba, ben Freya’nın piyasa asistanıyım. Şu an konuşmaya müsait misiniz?",
    "question": "Başka sorunuz var mı?",
    "goodbye": "Teşekkür ederim. İyi günler dilerim.",
    "unavailable": "Anladım. İyi günler dilerim.",
}


class AlertInput(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    target: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    direction: Literal["above", "below"]


def spoken_price(value):
    amount = f"{value:.4f}".rstrip("0")
    whole, _, fraction = amount.partition(".")
    if not fraction:
        return f"{whole} dolar"
    return f"{whole} virgül {' '.join(fraction)} dolar"


def call_script(alert, grades):
    symbol = " ".join(alert["symbol"])
    move = "yükselme" if alert["direction"] == "above" else "düşüş"
    target = spoken_price(alert["target"])
    if alert["trigger_source"] == "manual":
        report = f"{symbol} için {target} seviyesindeki {move} alarmı deneme amacıyla tetiklendi."
    else:
        price = spoken_price(alert["observed_price"])
        extreme = "en yüksek" if alert["direction"] == "above" else "en düşük"
        report = (f"{symbol} için {target} seviyesindeki {move} alarmınız tetiklendi. "
                  f"Tetiklenen dakikadaki {extreme} fiyat {price}.")
    if grades and sum(grades.values()):
        report += (f" Analist dağılımında {grades['buy']} al, {grades['hold']} tut "
                   f"ve {grades['sell']} sat görüşü var.")
    return dict(CALL_LINES, report=report)


def create_app(market=None, alerts=None, start_monitor=True):
    public_host = os.getenv("NASDAQ_PUBLIC_HOST", "").strip()
    check_interval = max(10, int(os.getenv("NASDAQ_POLL_SECONDS", "60")))

    @asynccontextmanager
    async def lifespan(app):
        client = httpx.Client(timeout=8)
        app.state.market = market if market is not None else Market(
            client, os.getenv("MASSIVE_API_KEY"), os.getenv("FMP_API_KEY"),
            max(0, int(os.getenv("NASDAQ_DATA_DELAY_MINUTES", "15"))),
        )
        app.state.alerts = alerts if alerts is not None else Alerts(
            os.getenv("NASDAQ_DB", ROOT / ".runtime/nasdaq.sqlite")
        )
        stop = asyncio.Event()

        async def monitor():
            while not stop.is_set():
                try:
                    await run_in_threadpool(app.state.alerts.check, app.state.market)
                except Exception:
                    logger.exception("Alarm kontrolü başarısız.")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=check_interval)
                except TimeoutError:
                    pass

        task = (asyncio.create_task(monitor())
                if start_monitor and not app.state.market.demo else None)
        try:
            yield
        finally:
            stop.set()
            if task:
                await task
            client.close()

    app = FastAPI(title="Freya Piyasa", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
        "localhost", "127.0.0.1", "testserver", *([public_host] if public_host else [])
    ])

    @lru_cache(maxsize=24)
    def antalia_audio(text):
        return voice.speak(text, model=voice.ANTALIA_MODEL)

    @app.middleware("http")
    async def browser_boundary(request, call_next):
        if request.method in {"POST", "DELETE"}:
            origin = request.headers.get("origin")
            expected = f"https://{public_host}" if public_host else str(request.base_url).rstrip("/")
            if origin and origin != expected:
                return JSONResponse({"detail": "Bu işlem aynı siteden yapılmalıdır."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(MarketError)
    async def market_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def owner(request, response=None):
        key = request.cookies.get(COOKIE, "")
        if COOKIE_RE.fullmatch(key):
            return key
        if response is None:
            raise HTTPException(403, "Alarm oturumu açılmadı. Sayfayı yenileyin.")
        key = uuid4().hex
        response.set_cookie(COOKIE, key, max_age=365 * 24 * 3600, httponly=True,
                            secure=bool(public_host) or request.url.scheme == "https", samesite="strict")
        return key

    def mode():
        return "demo" if app.state.market.demo else "market"

    def find_stock(symbol):
        stock = next((item for item in app.state.market.ranking()["stocks"]
                      if item["symbol"] == symbol), None)
        if stock is None:
            raise HTTPException(404, "Hisse güncel ilk 10 listesinde yok.")
        return stock

    def require_alert(value):
        if value is None:
            raise HTTPException(404, "Alarm bulunamadı.")
        return value

    def triggered_alert(request, alert_id):
        alert = require_alert(app.state.alerts.get(owner(request), mode(), alert_id))
        if not alert["triggered_at"]:
            raise HTTPException(409, "Çağrıyı başlatmak için alarmın tetiklenmesi gerekiyor.")
        return alert

    @app.get("/api/market")
    def ranking():
        return app.state.market.ranking()

    @app.get("/api/stocks/{symbol}")
    def detail(symbol: str, period: Period = "1D"):
        stock = find_stock(symbol)
        return dict(
            stock=stock,
            period=period,
            points=app.state.market.chart(stock, period),
        )

    @app.get("/api/stocks/{symbol}/ratings")
    def ratings(symbol: str):
        find_stock(symbol)
        return dict(ratings=app.state.market.ratings(symbol))

    @app.get("/api/alerts")
    def list_alerts(request: Request, response: Response):
        return app.state.alerts.list(owner(request, response), mode())

    @app.post("/api/alerts", status_code=201)
    def create_alert(data: AlertInput, request: Request):
        account = owner(request)
        find_stock(data.symbol)
        alert = app.state.alerts.create(account, mode(), data.symbol, data.target, data.direction)
        if alert is None:
            raise HTTPException(409, "Bu tarayıcıda en fazla 30 alarm saklanabilir.")
        return alert

    @app.delete("/api/alerts/{alert_id}", status_code=204)
    def delete_alert(alert_id: str, request: Request):
        if not app.state.alerts.delete(owner(request), mode(), alert_id):
            raise HTTPException(404, "Alarm bulunamadı.")

    @app.post("/api/alerts/{alert_id}/trigger")
    def trigger(alert_id: str, request: Request):
        return require_alert(app.state.alerts.trigger_manual(owner(request), mode(), alert_id))

    @app.post("/api/alerts/{alert_id}/call")
    def call(alert_id: str, request: Request):
        alert = triggered_alert(request, alert_id)
        return call_script(alert, app.state.market.ratings(alert["symbol"]))

    @app.get("/api/alerts/{alert_id}/speech/{cue}")
    def speech(alert_id: str, cue: Cue, request: Request):
        alert = triggered_alert(request, alert_id)
        if cue == "report":
            script = call_script(alert, app.state.market.ratings(alert["symbol"]))
            text = f"{script['report']} {script['question']}"
        else:
            text = CALL_LINES[cue]
        try:
            audio = antalia_audio(text)
        except Exception as exc:
            logger.exception("Antalia seslendirmesi üretilemedi.")
            raise HTTPException(503, "Antalia sesi hazırlanamadı.") from exc
        return Response(audio, media_type="audio/wav")

    app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="web")
    return app


app = create_app()
