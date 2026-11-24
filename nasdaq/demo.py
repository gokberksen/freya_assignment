from datetime import date, datetime, time, timedelta
from math import sin
from random import Random

from .market import ET, baseline_dates, chart_points, price_changes

SESSION = date(2026, 9, 18)
SAMPLE = (
    ("NVDA", "NVIDIA Corporation", 178.42, 148_276_530, (54, 9, 2)),
    ("TSLA", "Tesla, Inc.", 352.68, 117_058_420, (26, 16, 14)),
    ("INTC", "Intel Corporation", 24.76, 92_184_610, (9, 29, 8)),
    ("AMD", "Advanced Micro Devices", 164.12, 77_346_120, (39, 8, 3)),
    ("PLTR", "Palantir Technologies", 152.31, 65_208_430, (15, 12, 5)),
    ("AAPL", "Apple Inc.", 232.84, 54_617_240, (33, 13, 4)),
    ("SOFI", "SoFi Technologies", 25.18, 43_720_180, (15, 9, 3)),
    ("AMZN", "Amazon.com, Inc.", 220.16, 40_358_160, (52, 5, 1)),
    ("META", "Meta Platforms", 719.54, 34_269_450, (51, 8, 2)),
    ("MSFT", "Microsoft Corporation", 507.23, 29_815_710, (47, 6, 1)),
)


def stamp(day, hour=16, minute=0):
    return int(datetime.combine(day, time(hour, minute), ET).timestamp() * 1000)


def history(symbol, price):
    random = Random(symbol)
    day = SESSION - timedelta(days=375)
    trend = random.uniform(-0.17, 0.28)
    values = []
    while day < SESSION:
        if day.weekday() < 5:
            fraction = (SESSION - day).days / 375
            factor = 1 - trend * fraction + 0.042 * sin(fraction * 28 + len(symbol)) * fraction
            factor += random.uniform(-0.023, 0.023)
            values.append(dict(t=stamp(day), c=round(price * factor, 2)))
        day += timedelta(days=1)
    return values


def ranking():
    return dict(
        mode="demo", session=SESSION.isoformat(), source="Örnek veri", delay_minutes=None,
        stocks=[
            dict(symbol=symbol, name=name, price=price, volume=volume,
                 as_of=stamp(SESSION), session=SESSION.isoformat(),
                 changes=price_changes(price, SESSION, history(symbol, price)))
            for symbol, name, price, volume, _ in SAMPLE
        ],
    )


def ratings(symbol):
    return dict(zip(("buy", "hold", "sell"),
                    next(item[-1] for item in SAMPLE if item[0] == symbol)))


def chart(stock, period):
    if period != "1D":
        bars = history(stock["symbol"], stock["price"])
    else:
        random = Random(stock["symbol"])
        noise = random.uniform(-0.002, 0.002)
        start = datetime.combine(SESSION, time(9, 30), ET)
        bars = [
            dict(t=int((start + timedelta(minutes=index * 5)).timestamp() * 1000),
                 c=round(stock["price"] * (1 + noise - 0.021 * (1 - index / 78)
                         + 0.008 * sin(index / 5.5) + random.uniform(-0.004, 0.004)), 2))
            for index in range(78)
        ]
    return chart_points(bars, stock, period)
