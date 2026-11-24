import calendar
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Lock
from time import monotonic, time
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
MINUTE = 60_000
PERIODS = ("1D", "1W", "1M", "1Y")
TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")
STOCK_TYPES = {"CS", "ADRC", "ADRS", "ADRP"}
logger = logging.getLogger(__name__)


class MarketError(Exception):
    pass


def market_date(ms):
    return datetime.fromtimestamp(ms / 1000, ET).date()


def baseline_dates(day):
    previous_month = day.replace(day=1) - timedelta(days=1)
    return {
        "1D": day - timedelta(days=1),
        "1W": day - timedelta(days=7),
        "1M": previous_month.replace(day=min(day.day, previous_month.day)),
        "1Y": date(day.year - 1, day.month, min(
            day.day, calendar.monthrange(day.year - 1, day.month)[1]
        )),
    }


def price_changes(price, day, history):
    closes = [(market_date(bar["t"]), bar["c"]) for bar in reversed(history)]
    changes = {}
    for period, target in baseline_dates(day).items():
        match = next(((when, close) for when, close in closes if when <= target), None)
        changes[period] = (
            round((price / match[1] - 1) * 100, 2)
            if match and match[1] > 0 and (target - match[0]).days <= 7 else None
        )
    return changes


def chart_points(bars, stock, period):
    day = date.fromisoformat(stock["session"])
    start = day if period == "1D" else baseline_dates(day)[period]
    points = [
        [bar["t"], bar["c"]] for bar in bars
        if start <= market_date(bar["t"]) <= day and bar["t"] < stock["as_of"]
        and (period == "1D" or market_date(bar["t"]) < day)
    ]
    return points + [[stock["as_of"], stock["price"]]]


class TTLCache:
    def __init__(self):
        self.values = {}
        self.lock = Lock()

    def get(self, key, lifetime, load):
        with self.lock:
            current = self.values.get(key)
            if current and monotonic() - current[0] < lifetime:
                return current[1]
        value = load()
        with self.lock:
            if len(self.values) > 300:
                self.values = {
                    name: record for name, record in self.values.items()
                    if monotonic() - record[0] < 300
                }
            self.values[key] = (monotonic(), value)
        return value


class Market:
    def __init__(self, client, massive_key, fmp_key, delay_minutes):
        self.client = client
        self.massive_key = massive_key
        self.fmp_key = fmp_key
        self.delay_minutes = delay_minutes
        self.demo = not bool(massive_key)
        self.cache = TTLCache()
        self.snapshot_lock = Lock()

    def massive(self, path, **params):
        try:
            response = self.client.get(
                f"https://api.massive.com{path}", params=params or None,
                headers={"Authorization": f"Bearer {self.massive_key}"},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") not in {"OK", "DELAYED"}:
                raise ValueError
            return data
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise MarketError("Piyasa verisi sağlayıcısına ulaşılamıyor.") from exc

    def universe(self):
        def load():
            path = "/v3/reference/tickers"
            params = dict(exchange="XNAS", market="stocks", active="true", limit=1000)
            stocks = {}
            for _ in range(25):
                page = self.massive(path, **params)
                stocks.update({
                    row["ticker"]: row["name"] for row in page.get("results", [])
                    if row.get("primary_exchange") == "XNAS" and row.get("type") in STOCK_TYPES
                })
                if not page.get("next_url"):
                    if not stocks:
                        break
                    return stocks
                url = httpx.URL(page["next_url"])
                if url.scheme != "https" or url.host != "api.massive.com" or url.path != path:
                    break
                params = {"cursor": url.params["cursor"]}
            raise MarketError("Nasdaq hisse listesi tamamlanamadı.")
        return self.cache.get("universe", 12 * 3600, load)

    def bars(self, symbol, start, end, unit="day", size=1, adjusted=True):
        if not TICKER.fullmatch(symbol):
            raise MarketError("Hisse kodu geçersiz.")
        payload = self.massive(
            f"/v2/aggs/ticker/{symbol}/range/{size}/{unit}/{start}/{end}",
            adjusted=str(adjusted).lower(), sort="asc", limit=50000,
        )
        return payload.get("results") or []

    def history(self, symbol, day):
        return self.cache.get(("history", symbol, day), 1800, lambda: self.bars(
            symbol, baseline_dates(day)["1Y"] - timedelta(days=8), day - timedelta(days=1)
        ))

    def _closed_session(self, before):
        def load():
            for offset in range(1, 9):
                day = before - timedelta(days=offset)
                if day.weekday() >= 5:
                    continue
                data = self.massive(
                    f"/v2/aggs/grouped/locale/us/market/stocks/{day}", adjusted="false"
                )
                if data.get("results"):
                    return day, data["results"]
            raise MarketError("Son işlem seansının hacim verisi bulunamadı.")
        return self.cache.get(("closed", before), 900, load)

    def _ranking(self):
        symbols = self.universe()
        data = self.massive("/v2/snapshot/locale/us/markets/stocks/tickers")
        today = datetime.now(ET).date()
        rows = []
        for item in data.get("tickers", []):
            symbol = item.get("ticker")
            daily = item.get("day") or {}
            minute = item.get("min") or {}
            trade = item.get("lastTrade") or {}
            trade_time = int(trade.get("t", 0) / 1_000_000)
            minute_time = minute.get("t") or 0
            stamp = max(trade_time, minute_time)
            if symbol in symbols and daily.get("v", 0) > 0 and stamp and market_date(stamp) == today:
                price = (trade.get("p") if trade_time >= minute_time else minute.get("c")) or daily.get("c")
                if price and price > 0:
                    rows.append((symbol, price, daily["v"], stamp))
        day = today
        if not rows:
            day, closed = self._closed_session(today)
            stamp = int(datetime(day.year, day.month, day.day, 16, tzinfo=ET).timestamp() * 1000)
            rows = [
                (bar["T"], bar["c"], bar["v"], stamp) for bar in closed
                if bar["T"] in symbols and bar.get("v", 0) > 0 and bar.get("c", 0) > 0
            ]
        if not rows:
            raise MarketError("Nasdaq hisseleri için hacim verisi bulunamadı.")
        leaders = sorted(rows, key=lambda row: (-row[2], row[0]))[:10]

        def build(row):
            symbol, price, volume, stamp = row
            try:
                changes = price_changes(price, day, self.history(symbol, day))
            except MarketError:
                logger.warning("Geçmiş fiyatlar alınamadı (%s).", symbol)
                changes = dict.fromkeys(PERIODS)
            return dict(symbol=symbol, name=symbols[symbol], price=price, volume=volume,
                        as_of=stamp, session=day.isoformat(), changes=changes)

        with ThreadPoolExecutor(max_workers=5) as pool:
            stocks = list(pool.map(build, leaders))
        return dict(mode="market", session=day.isoformat(), source="Massive",
                    delay_minutes=self.delay_minutes, stocks=stocks)

    def ranking(self):
        if self.demo:
            from .demo import ranking
            return ranking()
        with self.snapshot_lock:
            return self.cache.get("ranking", 45, self._ranking)

    def ratings(self, symbol):
        if self.demo:
            from .demo import ratings
            return ratings(symbol)
        if not self.fmp_key:
            return None

        def load():
            response = self.client.get(
                "https://financialmodelingprep.com/stable/grades-consensus",
                params={"symbol": symbol, "apikey": self.fmp_key},
            )
            response.raise_for_status()
            rows = response.json()
            row = next((item for item in rows if item.get("symbol") == symbol), None)
            if row is None:
                return None
            counts = [int(row[key]) for key in ("strongBuy", "buy", "hold", "sell", "strongSell")]
            if min(counts) < 0:
                return None
            return dict(buy=counts[0] + counts[1], hold=counts[2], sell=counts[3] + counts[4])

        try:
            return self.cache.get(("ratings", symbol), 3600, load)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            logger.warning("Analist dağılımı alınamadı (%s).", symbol)
            return None

    def chart(self, stock, period):
        if self.demo:
            from .demo import chart
            return chart(stock, period)
        day = date.fromisoformat(stock["session"])
        if period == "1D":
            bars = self.cache.get(("intraday", stock["symbol"], day), 45, lambda: self.bars(
                stock["symbol"], day, min(stock["as_of"], int(time() * 1000)), "minute", 5
            ))
        else:
            bars = self.history(stock["symbol"], day)
        return chart_points(bars, stock, period)

    def alert_bars(self, symbol, start, end):
        bars = self.bars(symbol, start, end, "minute", adjusted=False)
        return [
            bar for bar in bars if start <= bar["t"] <= end
            and math.isfinite(bar["h"]) and math.isfinite(bar["l"]) and bar["l"] > 0
        ]
