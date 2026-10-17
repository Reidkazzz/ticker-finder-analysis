"""Price history, 52-week range and analyst coverage from Yahoo Finance's public endpoints."""
import datetime as dt
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .net import cookie_opener, web_get, web_json


def _ysym(sym):
    return sym.replace(".", "-")


def price_history(sym):
    """1 year of weekly closes plus the current price, 52-week high/low and the stock splits of the year."""
    try:
        res = web_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{_ysym(sym)}?range=1y&interval=1wk"
                       "&events=split")["chart"]["result"][0]
    except Exception:
        return None
    meta = res.get("meta", {})
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    stamps = res.get("timestamp") or []
    points = [[t, round(c, 4)] for t, c in zip(stamps, closes) if c is not None]
    price = meta.get("regularMarketPrice")
    if not price:
        return None
    change, volume = meta.get("regularMarketChangePercent"), meta.get("regularMarketVolume")
    # A session still trading (a run started in market hours) has no close yet: its price is live, so it gets no session
    # day, change or volume, and no page calls it a close. The quote's trading period is that session's, or the next
    # one's once the last has closed (before its start).
    t = meta.get("regularMarketTime")
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end, now = period.get("start"), period.get("end"), time.time()
    closed = bool(t) and (not (start and end) or t >= end or now >= end or now < start)
    if not closed:
        t, change, volume = None, None, None
    return {
        "price": price,
        "high52": meta.get("fiftyTwoWeekHigh"),
        "low52": meta.get("fiftyTwoWeekLow"),
        "weekly": points,
        "splits": _splits(res),
        # The price's own session, from the same quote: when it closed (Unix time), its change on the close before it
        # (Yahoo gives a percentage) and the shares traded in it.
        "price_time": t,
        "day_change": change / 100 if isinstance(change, (int, float)) else None,
        "volume": int(volume) if isinstance(volume, (int, float)) and volume >= 0 else None,
    }


def session_day(p):
    """The day (YYYY-MM-DD) of a price's session, from its close time. A US session closes at 4pm New York time,
    8pm or 9pm UTC, so the UTC date is the trading day."""
    t = (p or {}).get("price_time")
    try:
        return dt.datetime.fromtimestamp(int(t), dt.timezone.utc).date().isoformat() if t else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def price_day(prices):
    """The trading day most prices are from: the day the pages name as the prices' close."""
    days = Counter(d for d in map(session_day, prices.values()) if d)
    return days.most_common(1)[0][0] if days else None


def day_move(p, day):
    """A price's change on the close before it and its volume, or (None, None) where its session isn't `day`, so a page
    never shows another day's change beside the day it names (a stock that didn't trade that day)."""
    if not p or not day or session_day(p) != day:
        return None, None
    return p.get("day_change"), p.get("volume")


def _splits(res):
    """[(date, shares after per share before)] of the chart's stock splits (a 2-for-1 split is 2.0), oldest first."""
    out = []
    for e in ((res.get("events") or {}).get("splits") or {}).values():
        try:
            ratio = float(e["numerator"]) / float(e["denominator"])
            day = dt.datetime.fromtimestamp(int(e["date"]), dt.timezone.utc).date().isoformat()
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError, OSError):
            continue
        if ratio > 0:
            out.append((day, ratio))
    return sorted(out)


def price_histories(symbols, workers=6):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(symbols, pool.map(price_history, symbols)))


class AnalystCounter:
    """Number of analysts with a published estimate, via Yahoo's quoteSummary (needs a session crumb)."""

    def __init__(self):
        self.opener = cookie_opener()
        self.crumb = None
        try:
            try:
                web_get("https://fc.yahoo.com", opener=self.opener)
            except Exception:
                pass  # this endpoint returns 404 but still sets the session cookie
            self.crumb = web_get("https://query2.finance.yahoo.com/v1/test/getcrumb", opener=self.opener).decode().strip()
        except Exception:
            self.crumb = None

    def count(self, sym):
        if not self.crumb:
            return None
        try:
            r = web_json(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{_ysym(sym)}?modules=financialData&crumb={self.crumb}",
                opener=self.opener,
            )
            fd = r["quoteSummary"]["result"][0].get("financialData") or {}
            n = fd.get("numberOfAnalystOpinions") or {}
            return int(n.get("raw", 0))
        except Exception:
            return None

    def counts(self, symbols, workers=4):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(zip(symbols, pool.map(self.count, symbols)))
