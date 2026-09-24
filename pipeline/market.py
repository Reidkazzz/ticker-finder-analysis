"""Price history, 52-week range and analyst coverage from Yahoo Finance's public endpoints."""
from concurrent.futures import ThreadPoolExecutor

from .net import cookie_opener, web_get, web_json


def _ysym(sym):
    return sym.replace(".", "-")


def price_history(sym):
    """1 year of weekly closes plus the current price and 52-week high/low."""
    try:
        res = web_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{_ysym(sym)}?range=1y&interval=1wk")["chart"]["result"][0]
    except Exception:
        return None
    meta = res.get("meta", {})
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    stamps = res.get("timestamp") or []
    points = [[t, round(c, 4)] for t, c in zip(stamps, closes) if c is not None]
    price = meta.get("regularMarketPrice")
    if not price:
        return None
    return {
        "price": price,
        "high52": meta.get("fiftyTwoWeekHigh"),
        "low52": meta.get("fiftyTwoWeekLow"),
        "weekly": points,
    }


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
