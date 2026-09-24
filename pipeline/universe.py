"""The list of US-listed common stocks, with price, market cap, sector and industry."""
import re

from .net import sec_json, web_json

# Share classes that are not ordinary common stock.
_EXCLUDE_NAME = re.compile(
    r"\b(warrants?|units?|rights?|preferred|depositary|notes due|debentures|trust preferred|"
    r"subordinated|perpetual|acquisition corp|capital trust)\b|%",
    re.I,
)


def _num(s):
    if s in (None, "", "NA", "N/A"):
        return None
    try:
        return float(str(s).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _clean_name(name):
    name = re.sub(r"\s+(Common Stock|Class [A-C] Common Stock|Ordinary Shares|Common Shares)\b.*$", "", name, flags=re.I)
    return name.strip().rstrip(",")


def load_universe():
    """Returns {symbol: {...}} for NYSE, Nasdaq and NYSE American common stocks."""
    rows = web_json(
        "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true",
        headers={"Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"},
    )["data"]["rows"]

    sec = sec_json("https://www.sec.gov/files/company_tickers_exchange.json")
    fields = sec["fields"]
    sec_by_ticker = {}
    for row in sec["data"]:
        rec = dict(zip(fields, row))
        if rec["exchange"] in ("NYSE", "Nasdaq", "CBOE") or (rec["exchange"] or "").startswith("NYSE"):
            sec_by_ticker[rec["ticker"].upper().replace("-", ".")] = rec

    universe = {}
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper().replace("/", ".")
        if not sym or "^" in sym or _EXCLUDE_NAME.search(r.get("name") or ""):
            continue
        sec_rec = sec_by_ticker.get(sym)
        if not sec_rec:
            continue  # not an SEC-registered listing we can pull filings for
        price = _num(r.get("lastsale"))
        mcap = _num(r.get("marketCap"))
        if not price or price <= 0:
            continue
        universe[sym] = {
            "symbol": sym,
            "name": _clean_name(r.get("name") or sec_rec["name"]),
            "cik": int(sec_rec["cik"]),
            "exchange": sec_rec["exchange"],
            "price": price,
            "mcap": mcap if mcap and mcap > 0 else None,
            "country": (r.get("country") or "").strip(),
            "sector": (r.get("sector") or "").strip() or "Other",
            "industry": (r.get("industry") or "").strip() or "Other",
        }
    return universe
