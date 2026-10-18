"""The list of US-listed common stocks, with price, market cap, sector and industry."""
import datetime as dt
import json
import os
import re

from .net import sec_json, web_json

# Share classes that are not ordinary common stock. Nasdaq abbreviates some preferreds ('Dep Shr Srs A Pfd')
# and misspells depositary shares, and these listings share the issuer's CIK with its common stock.
_EXCLUDE_NAME = re.compile(
    r"\b(warrants?|units?|rights?|preferred|pfd (shs?|ser|series)|srs [a-z] pfd|deposit[ao]ry|dep sh[rs]|zones|"
    r"notes due|debentures|trust preferred|subordinated|perpetual|acquisition corp|capital trust)\b|%|\(adw\)",
    re.I,
)
# A partnership's ordinary equity is its units ("Common Units representing Limited Partner Interests", "L.P. Units",
# "Class A Units", "Beneficial Unit Certificates"), which the word "units" above would drop along with a blank-check
# company's units (a share and a warrant sold together). Such a name is kept when nothing else in it marks a listing
# that isn't common equity (a preferred unit, a note).
_PARTNERSHIP_UNITS = re.compile(
    r"\b(common units?|limited partner(ship)? (units?|interests?)|beneficial unit|class [a-c] units?)\b|\bL\.?P\.?\s.*\bunits?\b",
    re.I,
)


def _excluded(name):
    if not _EXCLUDE_NAME.search(name):
        return False
    return not _PARTNERSHIP_UNITS.search(name) or bool(_EXCLUDE_NAME.search(re.sub(r"\bunits?\b", "", name, flags=re.I)))


def _num(s):
    if s in (None, "", "NA", "N/A"):
        return None
    try:
        return float(str(s).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _clean_name(name):
    # A partnership's units are trimmed the same way ("Common Units representing Limited Partner Interests", "Units").
    name = re.sub(r"\s+(Common Stock|Class [A-C] Common Stock|Ordinary Shares|Common Shares|Common Units?|Class [A-C] Units?|"
                  r"Limited Partnership Units|Beneficial Unit Certificates|Units)\b.*$", "", name, flags=re.I)
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
        name = r.get("name") or ""
        if not sym or "^" in sym or _excluded(name):
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
        if _EXCLUDE_NAME.search(name):
            # Kept by the partnership exception (_excluded). Its report and screen are like any other's, but it stays
            # out of other companies' peers, ranks and industry figures (outside_listing), so letting partnerships in
            # moved no other company's value: in September 2026 data their 27 listings otherwise changed 192 reports,
            # 37 verdicts among them, as tax-free partnerships joined corporations' peers.
            universe[sym]["listing"] = "partnership"
    # Companies quoted over the counter that file with the SEC (the SEC's list marks them "OTC"), whose price, market
    # value, sector and industry the build fills in or drops them (price_otc, sector_otc, value_otc).
    picks = otc_picks([dict(zip(fields, row)) for row in sec["data"]], {u["cik"] for u in universe.values()}) if OTC_ENABLED else []
    for rec in picks:
        sym = rec["ticker"].upper()
        universe.setdefault(sym, {"symbol": sym, "name": _clean_name(rec["name"]), "cik": int(rec["cik"]), "exchange": "OTC",
                                  "price": None, "mcap": None, "country": "", "sector": None, "industry": None,
                                  "listing": "otc"})
    return universe


def otc_picks(records, listed_ciks):
    """The SEC ticker records (company_tickers_exchange.json rows as dicts) of the over-the-counter quotes kept: a
    company's only quote that _otc_common keeps, for a company none of whose share classes trades on an exchange
    (listed_ciks). A company with two such quotes is left out, since its share count can't be split between them."""
    by_cik = {}
    for rec in records:
        sym = (rec.get("ticker") or "").upper()
        if rec.get("exchange") == "OTC" and rec.get("cik") not in listed_ciks and _otc_common(sym, rec.get("name") or ""):
            by_cik.setdefault(rec["cik"], []).append(rec)
    return [recs[0] for recs in by_cik.values() if len(recs) == 1]


# Over-the-counter quotes kept: common stock by its ticker, and not named as an ADR, a warrant or a blank-check
# company. An OTC ticker of five letters names a special security by its last: A or B a share class, which is common
# stock, but F or Y a foreign company's line (1,206 of the SEC's 2,572 OTC tickers in October 2026), E a company late
# with its filings, Q one in bankruptcy, G to P bonds and preferred stock (Fannie Mae's and Freddie Mac's preferred
# series among them), R rights, U units, W warrants. So four letters or fewer, or five ending in A or B.
def _otc_common(sym, name):
    if not re.fullmatch(r"[A-Z]{1,4}|[A-Z]{4}[AB]", sym):
        return False
    return not _excluded(name) and not re.search(r"/ADR\b|\bADR\b", name, re.I)


# Over-the-counter coverage is a reviewed list (OTC_REVIEW): each company was checked against its own SEC filings for
# its sector and industry, whether it is ordinary common stock of a going concern, whether it is a REIT, and whether its
# quote fits the trading its filings report. A first, unreviewed version was checked on a sample of 24 in October 2026
# and had serious errors in 10: sectors from SEC industry codes the companies had outgrown (a supplement maker under
# technology, a broker under software), a stale quote ($550 for a stock trading near $80), a title insurer taken for a
# REIT by its code, an LLC's capital units, and Fannie Mae, whose profit belongs to the Treasury's senior preferred
# stock. A quote not on the list is left out, and the build names it, until it has been reviewed.
OTC_ENABLED = True
OTC_REVIEW = os.path.join(os.path.dirname(__file__), "otc_review.json")

# An over-the-counter stock counts at OTC_MIN_PRICE or more, with a closing price from the last OTC_RECENT_DAYS: in
# October 2026, 316 of the 959 companies left after the ticker and name rules. Most below a dollar are shells whose
# filings are thin or late; one not traded in a week has no current price.
OTC_MIN_PRICE = 1.0
OTC_RECENT_DAYS = 7
# A quote whose last OTC_FLAT_WEEKS weekly closes take fewer than OTC_FLAT_PRICES values hasn't traded: one $550 close
# held for twelve weeks on one share of volume, while the bank's filings reported trades near $80. 45 of the October
# 2026 candidates were dropped this way.
OTC_FLAT_WEEKS = 8
OTC_FLAT_PRICES = 3
# Its market value is its price times the share count on its latest SEC filing's cover, which must be this recent.
OTC_SHARES_DAYS = 400


def price_otc(universe, prices, today, session_day):
    """Sets each OTC listing's price from its quote (prices, market.price_history's) and drops those without a
    closing price from the last OTC_RECENT_DAYS (`session_day` gives a quote's day), under OTC_MIN_PRICE, or whose
    weekly closes show no trading (OTC_FLAT_WEEKS)."""
    since = (today - dt.timedelta(days=OTC_RECENT_DAYS)).isoformat()
    for sym in [s for s, u in universe.items() if u.get("listing") == "otc"]:
        p = prices.get(sym) or {}
        day = session_day(p)
        closes = [c for _, c in (p.get("weekly") or [])[-OTC_FLAT_WEEKS:]]
        if not (day and day >= since and (p.get("price") or 0) >= OTC_MIN_PRICE and len(set(closes)) >= OTC_FLAT_PRICES):
            del universe[sym]
            continue
        universe[sym]["price"] = p["price"]


def sector_otc(universe):
    """Gives each OTC listing the sector, industry and REIT status its review found (OTC_REVIEW, keyed by CIK), and
    drops one the review left out or that hasn't been reviewed. Returns the tickers waiting for a review."""
    with open(OTC_REVIEW, encoding="utf-8") as fh:
        review = json.load(fh)["companies"]
    waiting = []
    for sym in [s for s, u in universe.items() if u.get("listing") == "otc"]:
        r = review.get(str(universe[sym]["cik"]))
        if not r:
            waiting.append(sym)
        if not r or not r["include"]:
            del universe[sym]
            continue
        universe[sym].update(sector=r["sector"], industry=r["industry"], reit_reviewed=bool(r["reit"]))
    return sorted(waiting)


def value_otc(universe, companies, today):
    """Sets each OTC listing's market value from the share count on its latest SEC filing's cover (companies,
    fundamentals' {cik: {"latest": ...}}), and drops one whose count is missing or older than OTC_SHARES_DAYS."""
    since = (today - dt.timedelta(days=OTC_SHARES_DAYS)).isoformat()
    for sym in [s for s, u in universe.items() if u.get("listing") == "otc"]:
        u = universe[sym]
        latest = (companies.get(u["cik"]) or {}).get("latest") or {}
        shares, as_of = latest.get("shares_out"), latest.get("shares_as_of") or ""
        if not shares or shares <= 0 or as_of < since:
            del universe[sym]
            continue
        u["mcap"] = shares * u["price"]


def outside_listing(u):
    """True for a listing kept out of other companies' peers, ranks and industry figures (load_universe's "listing")."""
    return bool(u.get("listing"))
