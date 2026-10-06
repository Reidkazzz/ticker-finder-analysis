"""Sector screener: viable, undervalued, US small caps, and the watch lists beside them (companies worth less than
their net cash, banks and insurers under book value, giants priced far above their sales)."""
import collections
import datetime as dt
import json
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, Throttled, sec_json
from .report import one_time_note, sales_doubtful

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "registrations.json")

# Deposits, loans and insurance reserves make the debt, net cash and free cash flow checks meaningless
# for banks and insurers (their "debt-free" balance sheets are full of customer money), so the whole
# sector is left out, as value screens like the Magic Formula do, rather than scored on noise.
EXCLUDED_SECTORS = {
    "Finance": "Banks, insurers and other financial companies are not screened. Their cash, debt and cash flow "
               "work differently from other businesses, so these checks would mislead.",
}

_NOT_CHECKED = "Could not confirm it is incorporated and based in the US: "
UNVERIFIED_REASONS = {
    "blocked": _NOT_CHECKED + "the SEC was limiting requests during today's update.",
    "failed": _NOT_CHECKED + "the SEC company record did not load.",
    "missing": _NOT_CHECKED + "the SEC has no company record for it.",
    "blank": _NOT_CHECKED + "its SEC company record does not say.",
}
# For a company that passes every other check but whose debt could not be read (_debt_unread).
DEBT_UNREAD = "Its debt could not be read reliably from its filings, so the low-debt rule could not be confirmed."

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
    "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)

# The hard rules every company on the list passes, and the thresholds of two flags (out_of_favor_drawdown, which also
# marks an industry out of favor, and undiscovered_max_analysts). The profit margin rule was 8% until September 2026: at
# 3 cents of profit on each sales dollar (without one-time items) it keeps out companies that barely break even while
# letting in thin-margin businesses such as retailers and distributors, and the score still ranks higher margins higher.
RULES = {
    "max_ps": 2.0,
    "max_mcap": 3e9,
    "bonus_mcap": 2e9,
    "max_lt_de": 0.5,
    "min_net_margin": 0.03,
    "min_profitable_years": 2,
    "min_revenue_growth": -0.05,
    "out_of_favor_drawdown": 0.25,
    "undiscovered_max_analysts": 2,
}

# The most points each part of the score can give (_score), out of 100 together. A module-level dict so a test can
# change one weight (the backtest does); _score refuses a key it doesn't know, so a misspelt one can't pass unnoticed.
# Keys: "ps" cheap vs. sales, "debt" (no debt, more cash than debt, or low debt), "margin" profit margin, "fcf" cash
# generation, "growth" sales growth, "small" under $2B, "out_of_favor" below the 52-week high, "undiscovered" few
# analysts, "under_book" under book value, "insiders_buying" Tier 1 or 2 insider buying.
SCORE_WEIGHTS = {
    "ps": 15,
    "debt": 20,
    "margin": 15,
    "fcf": 10,
    "growth": 10,
    "small": 5,
    "out_of_favor": 5,
    "undiscovered": 5,
    "under_book": 10,
    "insiders_buying": 5,
}
# Each part's name on the page. The debt part is named by its tier (_score).
SCORE_LABELS = {
    "ps": "Cheap vs. sales", "debt": "Debt", "margin": "Profit margin", "fcf": "Cash generation",
    "growth": "Sales growth", "small": "Under $2B", "out_of_favor": "Out of favor", "undiscovered": "Few analysts",
    "under_book": "Under book value", "insiders_buying": "Insiders buying",
}
# Shares of a part's weight for its middle tiers: a company with more cash than debt gets 70% of the debt part (14 of 20
# points), a company with some long-term debt at most 40% (8 of 20), falling to none at the debt limit, and a company one
# or two analysts cover 60% of the few-analysts part (3 of 5 points; none at all gets all 5).
NET_CASH_SHARE = 0.7
LOW_DEBT_SHARE = 0.4
FEW_ANALYSTS_SHARE = 0.6
# Price to book at or under which the under-book part gives full points, and at or over which it gives none, falling
# evenly between.
UNDER_BOOK_FULL, UNDER_BOOK_NONE = 1.0, 2.0

# An industry's out-of-favor test (_industries) needs at least this many companies with a year of weekly prices, so one
# or two companies' slumps don't mark a whole industry. A listing with fewer weekly closes than INDUSTRY_MIN_WEEKS is a
# recent one, whose "52-week high" is its high since listing, often the first days' pop, so it isn't counted.
INDUSTRY_MIN_LISTINGS = 5
INDUSTRY_MIN_WEEKS = 50
# A fiscal year that ended this long before the company's latest balance sheet is out of date for the watch lists: a
# company that stopped tagging revenue (a drug developer between products, some lenders) keeps the last year that has
# it, so Navient's figures on September 30, 2026 were its 2024 profit, though it lost $80M in 2025. Fifteen months
# leaves room for a 10-K filed late.
STALE_YEAR_DAYS = 456
# The upper Bollinger band: the average of the last BAND_WEEKS weekly closes plus BAND_WIDTH standard deviations of them.
# A price above it has risen unusually fast for its recent range, which traders read as a reason to wait for a pullback
# rather than buy after the run-up. A flag only: it never keeps a company off the list.
BAND_WEEKS = 20
BAND_WIDTH = 2
# The "Overpriced giants" watch list: market value over GIANT_MIN_MCAP and price to sales over GIANT_MIN_PS. A company
# that size priced at over 10 times its sales needs years of fast growth just to justify today's price.
GIANT_MIN_MCAP = 90e9
GIANT_MIN_PS = 10.0


def registration(cik):
    """State of incorporation and business address from the SEC's company record. Raises if it can't be read."""
    s = sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    biz = (s.get("addresses") or {}).get("business") or {}
    return {
        "incorporated": (s.get("stateOfIncorporation") or "").upper(),
        "hq": (biz.get("stateOrCountry") or "").upper(),
        "sic": s.get("sicDescription"),
    }


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    except OSError:
        pass


def registrations(ciks, reuse=()):
    """{cik: record, or a key of UNVERIFIED_REASONS when the record could not be read}. The CIKs in `reuse` take the
    record an earlier run saved when it gives both places, and are looked up otherwise."""
    blocked = threading.Event()

    def one(cik):
        # Once the SEC has refused even after waiting out its block, every other lookup would wait just as long.
        if blocked.is_set():
            return "blocked"
        try:
            return registration(cik)
        except NotFound:
            return "missing"
        except Throttled:
            blocked.set()
            return "blocked"
        except Exception:
            return "failed"

    # Where a company is incorporated and based rarely changes, so a record from an earlier run stands in: for the
    # CIKs in `reuse` in place of a lookup, and for the others when theirs fails. A saved record missing either place
    # is looked up again, as the page promises for a registration it could not confirm.
    cache = _load_cache()
    fresh = list(dict.fromkeys(ciks))
    saved = {c: cache[str(c)] for c in reuse if c not in fresh and isinstance(cache.get(str(c)), dict)
             and cache[str(c)].get("incorporated") and cache[str(c)].get("hq")}
    todo = fresh + [c for c in dict.fromkeys(reuse) if c not in fresh and c not in saved]
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = dict(zip(todo, pool.map(one, todo)))
    # Dropped connections and gateway errors usually clear up, so each one gets a second try.
    for cik in [c for c, r in out.items() if r == "failed"]:
        out[cik] = one(cik)

    for cik, r in out.items():
        if isinstance(r, dict):
            cache[str(cik)] = r
        elif str(cik) in cache:
            out[cik] = cache[str(cik)]
    _save_cache(cache)
    return {**out, **saved}


def _checks(u, m):
    """Each hard rule as (key, passed, plain-English note). Missing data counts as a fail."""
    ps = u["mcap"] / m["revenue"] if u.get("mcap") and m.get("revenue") and m["revenue"] > 0 else None
    out = [
        ("ps", ps is not None and ps < RULES["max_ps"], ps),
        ("mcap", bool(u.get("mcap")) and u["mcap"] < RULES["max_mcap"], u.get("mcap")),
        ("debt", m.get("lt_debt_to_equity") is not None and m["lt_debt_to_equity"] < RULES["max_lt_de"], m.get("lt_debt_to_equity")),
        ("margin", m.get("adj_net_margin") is not None and m["adj_net_margin"] >= RULES["min_net_margin"], m.get("adj_net_margin")),
        ("equity", bool(m.get("equity")) and m["equity"] > 0, m.get("equity")),
        ("fcf", m.get("fcf") is not None and m["fcf"] > 0, m.get("fcf")),
        ("profitable", m.get("years_checked", 0) >= 2 and m["profitable_years"] >= RULES["min_profitable_years"], m.get("profitable_years")),
        ("growth", m.get("revenue_growth") is not None and m["revenue_growth"] >= RULES["min_revenue_growth"], m.get("revenue_growth")),
    ]
    return out, ps


def _debt_unread(m):
    """Whether the low-debt rule could not be checked because the debt read from the company's filings looks far too
    small to be all of it (fundamentals' debt_known False). The debt that was read is then a floor, so a company it
    already puts over the limit fails the rule outright (Collegium Pharmaceutical's 2026 convertible notes alone,
    beside the term loan the reading missed)."""
    equity = m.get("equity")
    return m.get("debt_known") is False and bool(equity) and equity > 0 \
        and (m.get("lt_debt") or 0) / equity < RULES["max_lt_de"]


def _score(row):
    """0-100 rank score: each part of SCORE_WEIGHTS, as the share of its weight the row earns. Every part is listed,
    with its key, name, points and most points, so the page can explain it; a part weighted 0 is left out.

    Cheap vs. sales: full points at a price to sales of 0, none at the limit (RULES max_ps). Debt: full points for no
    debt at all, NET_CASH_SHARE for more cash than debt, else up to LOW_DEBT_SHARE, falling to none at the debt limit.
    Profit margin: full points at 30% or more. Cash generation: full points for free cash flow of 15% or more of market
    value. Sales growth: full points at 20% or more. Under $2B: all or nothing. Out of favor: full points at 50% or more
    below the 52-week high. Few analysts: full points for none, FEW_ANALYSTS_SHARE for one or two. Under book value: full
    points at a price to book under 1, falling evenly to none at 2 (none without a book value above zero). Insiders
    buying: all or nothing, for Tier 1 or 2 open-market buying (the insiders_buying flag)."""
    unknown = set(SCORE_WEIGHTS) - set(SCORE_LABELS)
    if unknown:
        raise KeyError(f"SCORE_WEIGHTS has keys the score does not read: {sorted(unknown)}")
    share, label = {}, dict(SCORE_LABELS)
    share["ps"] = max(0.0, (RULES["max_ps"] - row["ps"]) / RULES["max_ps"])
    if row["total_debt"] <= 0:
        label["debt"], share["debt"] = "No debt at all", 1.0
    elif (row["net_cash"] or 0) > 0:
        label["debt"], share["debt"] = "More cash than debt", NET_CASH_SHARE
    else:
        label["debt"], share["debt"] = "Low debt", max(0.0, 1 - row["lt_de"] / RULES["max_lt_de"]) * LOW_DEBT_SHARE
    share["margin"] = max(0.0, min(row["net_margin"], 0.30)) / 0.30
    share["fcf"] = max(0.0, min(row["fcf_yield"] or 0, 0.15)) / 0.15
    share["growth"] = max(0.0, min(row["revenue_growth"] or 0, 0.20)) / 0.20
    share["small"] = 1.0 if row["mcap"] < RULES["bonus_mcap"] else 0.0
    share["out_of_favor"] = max(0.0, min(row["drawdown"] or 0, 0.5)) / 0.5
    a = row["analysts"]
    share["undiscovered"] = 1.0 if a == 0 else FEW_ANALYSTS_SHARE \
        if a is not None and a <= RULES["undiscovered_max_analysts"] else 0.0
    pb = row.get("pb")
    share["under_book"] = 0.0 if pb is None else \
        max(0.0, min(1.0, (UNDER_BOOK_NONE - pb) / (UNDER_BOOK_NONE - UNDER_BOOK_FULL)))
    share["insiders_buying"] = 1.0 if "insiders_buying" in row["flags"] else 0.0
    # Whole points (half rounds up), so the parts listed on the page add up exactly to the score.
    parts = [{"key": k, "label": label[k], "points": int(SCORE_WEIGHTS[k] * share[k] + 0.5), "max": SCORE_WEIGHTS[k]}
             for k in SCORE_LABELS if SCORE_WEIGHTS.get(k)]
    return sum(p["points"] for p in parts), parts


# The insider page's screener check (fit) states a rule a company misses as a fact, so a rule is only "missed" when its
# figures show it. _checks counts a missing figure as a fail, which is right for keeping a company off the screener
# but not for saying why: free cash flow left unread because the capital spending tag isn't read, a first year of
# sales with none before it, or debt that could not be read reliably would read as "no free cash flow", "sales down
# more than 5%" or "long-term debt half of equity or more". Those rules are "not checked" instead, and say why.
# Each miss is worded from RULES when it is stated, so it always names the limit run() applied ("profit margin under
# 3%"), even where a test changes a rule.
def _num(x):
    """A limit as the page writes it: 3.0 as "3", 0.5 as "0.5", without float noise (0.03 * 100 is 3.0000000000000004)."""
    return f"{x:.10g}"


FIT_MISSES = {
    "ps": lambda r: f"price to sales {_num(r['max_ps'])} or more",
    "mcap": lambda r: f"worth ${_num(r['max_mcap'] / 1e9)}B or more",
    "debt": lambda r: "long-term debt " + ("half of equity" if r["max_lt_de"] == 0.5
                                          else f"{_num(r['max_lt_de'])} times equity") + " or more",
    "margin": lambda r: f"profit margin under {_num(round(r['min_net_margin'] * 100, 6))}%",
    "fcf": lambda r: "no free cash flow",
    "growth": lambda r: f"sales down more than {_num(round(-r['min_revenue_growth'] * 100, 6))}%",
    "us": lambda r: "incorporated or based outside the US",
}
# Each rule's subject, for "... could not be checked".
FIT_RULES = {
    "ps": "its price to sales", "mcap": "its market value", "debt": "its debt", "margin": "its profit margin",
    "equity": "its equity", "fcf": "its free cash flow", "profitable": "its profit record",
    "growth": "its sales growth", "us": "where it is incorporated and based",
}
_SALES_DOUBT = "Its sales figure in the SEC data looks incomplete, since profit came out larger than sales."


def _fit_rules(u, m):
    """The rules of _checks a company's figures miss, as [(key, plain-English miss)], and those that could not be
    checked, as [(key, why)]. A miss of None is stated by another: negative equity fails the debt rule too, and a
    year without sales fails the profit margin rule too."""
    missed, unchecked = [], []
    rev, equity = m.get("revenue"), m.get("equity")
    doubt = sales_doubtful(m)
    for key, ok, _ in _checks(u, m)[0]:
        if ok:
            continue
        if key in ("ps", "mcap") and not u.get("mcap"):
            unchecked.append((key, "It has no market value."))
        elif key in ("ps", "margin", "growth") and doubt:
            unchecked.append((key, _SALES_DOUBT))
        elif key == "ps" and not (rev and rev > 0):
            missed.append((key, "no sales in its latest year, which fails the profit margin rule too"))
        elif key == "margin" and not (rev and rev > 0):
            missed.append((key, None if any(k == "ps" for k, _ in missed) else "no sales in its latest year"))
        elif key == "margin" and m.get("adj_net_margin") is None:
            unchecked.append((key, "Its profit could not be read."))
        elif key in ("debt", "equity") and equity is None:
            unchecked.append((key, "Its shareholders' equity could not be read."))
        elif key == "debt" and equity <= 0:
            missed.append((key, None))
        elif key == "equity":
            missed.append((key, ("negative equity" if equity < 0 else "no equity") + ", which fails the debt rule too"))
        elif key == "debt" and _debt_unread(m):
            unchecked.append((key, DEBT_UNREAD))
        elif key == "fcf" and m.get("fcf") is None:
            unchecked.append((key, "Its free cash flow could not be read from its filings."))
        elif key == "profitable" and m.get("years_checked", 0) < 2:
            unchecked.append((key, ("Its filings give profit figures for only one year" if m.get("years_checked") == 1
                                    else "Its filings give no yearly profit figures") + ", and the rule needs 2."))
        elif key == "profitable":
            missed.append((key, f"profitable in {m['profitable_years']} of the {m['years_checked']} years checked"))
        elif key == "growth" and m.get("revenue_growth") is None:
            unchecked.append((key, "It has no earlier year of sales to compare with."))
        else:
            missed.append((key, FIT_MISSES[key](RULES)))
    return missed, unchecked


def _fit_us(u, m, reg):
    """Whether a company meets the US rule as run() applies it, from its listing's country, the SEC financial data's
    address and its SEC registration (`reg`, a registrations() record or reason): "ok", "miss", or why it could not be
    checked."""
    if u.get("country") not in ("United States", ""):
        return "miss"
    places = (reg["incorporated"], reg["hq"]) if isinstance(reg, dict) else ()
    if any(p and p not in US_STATES for p in places):
        return "miss"
    if not (m.get("loc") or "").startswith("US"):
        return "The SEC's financial data does not give it a US address."
    if isinstance(reg, str):
        return UNVERIFIED_REASONS[reg]
    if not places:
        return _NOT_CHECKED + "its SEC company record was not looked up."
    return "ok" if all(p in US_STATES for p in places) else UNVERIFIED_REASONS["blank"]


def fit(sym, u, m, result, reg=None):
    """How a company measures against the screener's rules, for a page that lists it for another reason (insider
    buying). `result` is run's payload and `reg` the company's SEC registration (registrations()), for the US rule.

    Returns {"status", "misses", "missed", "unchecked", "why", "ps", "net_margin", "lt_de"}. status is "pass" (on the
    screener), "near" (its figures miss exactly one rule and every other rule was checked), "fail" (they miss more, or
    one while another could not be checked), "unconfirmed" (they miss none, and the rules in unchecked could not be
    checked), "not_screened" (a financial company, and why says which kind) or "no_data" (no yearly figures). misses
    states each rule missed as a fact its figures show, missed counts the rules (a miss can state two), unchecked names
    each rule that could not be checked and why says why, in sentences. ps and net_margin are left out when the sales
    figure looks incomplete, as the company's report leaves them out."""
    out = {"status": None, "misses": [], "missed": 0, "unchecked": [], "why": None,
           "ps": None, "net_margin": None, "lt_de": None}
    if u.get("sector") in EXCLUDED_SECTORS:
        return {**out, "status": "not_screened", "why": "Finance sector"}
    if (m or {}).get("revenue_basis"):
        return {**out, "status": "not_screened", "why": "its revenue is a lender's"}
    if not m:
        return {**out, "status": "no_data"}
    if not sales_doubtful(m):
        out.update(ps=_checks(u, m)[1], net_margin=m.get("adj_net_margin"))
    out["lt_de"] = m.get("lt_debt_to_equity")
    if any(r["symbol"] == sym for r in result["results"]):
        return {**out, "status": "pass"}
    unv = next((x for x in result["unverified"] if x["symbol"] == sym), None)
    if unv:
        # run() lists a company as unverified only when it passed every other rule.
        return {**out, "status": "unconfirmed", "unchecked": [FIT_RULES[k] for k in unv["unconfirmed"]],
                "why": unv["reason"]}
    missed, unchecked = _fit_rules(u, m)
    us = _fit_us(u, m, reg)
    if us == "miss":
        missed.append(("us", FIT_MISSES["us"](RULES)))
    elif us != "ok":
        unchecked.append(("us", us))
    if not missed and not unchecked:
        # Not on the screener, yet nothing here says why: run() read a different registration (a lookup that failed
        # there and not here), so the US rule is the one in doubt.
        unchecked.append(("us", _NOT_CHECKED.rstrip(": ") + "."))
    status = "unconfirmed" if not missed else "near" if len(missed) == 1 and not unchecked else "fail"
    return {**out, "status": status, "misses": [t for _, t in missed if t], "missed": len(missed),
            "unchecked": [FIT_RULES[k] for k, _ in unchecked],
            "why": " ".join(dict.fromkeys(w for _, w in unchecked)) or None}


def _us_listing(u, m):
    """The US tests run() makes before any lookup: the listing's country (Nasdaq's, blank counting as the US) and the
    address the SEC's financial data gives. Where the company is incorporated and based is then confirmed with its SEC
    registration (registrations(), _us_confirmed)."""
    return u.get("country") in ("United States", "") and (m.get("loc") or "").startswith("US")


def _us_confirmed(reg):
    """Whether a registrations() record confirms a company is incorporated and based in a US state: True, False when it
    names a place outside the US, or None when it could not be read or leaves a place blank."""
    places = (reg["incorporated"], reg["hq"]) if isinstance(reg, dict) else ()
    if places and all(p in US_STATES for p in places):
        return True
    return False if any(p and p not in US_STATES for p in places) else None


def _ps(u, m):
    """Price to sales for the watch lists and the industry medians: market value over the newest fiscal year's sales.
    None without both, with sales of nothing or less, or with a sales figure that looks incomplete (report.sales_doubtful,
    whose multiples the reports neither show nor use)."""
    rev = (m or {}).get("revenue")
    if not (u.get("mcap") and rev and rev > 0) or sales_doubtful(m):
        return None
    return u["mcap"] / rev


def _book(m):
    """Book value for price to book: shareholders' equity less preferred stock (fundamentals' preferred_stock), which is
    owed to preferred holders before the common shares the market value prices, and less minority holders' stakes in
    subsidiaries where the equity read includes them (fundamentals' equity_includes_minority): UWM Holdings' book would
    otherwise count its founder's $851M stake beside the listed shares' $134M. None without equity."""
    equity = m.get("equity")
    if equity is None:
        return None
    minority = (m.get("minority_interest") or 0) if m.get("equity_includes_minority") else 0
    return equity - (m.get("preferred_stock") or 0) - minority


def _upper_band(weekly):
    """The upper Bollinger band from market.price_history's weekly closes ([[time, close], ...], oldest first): the
    average of the last BAND_WEEKS closes plus BAND_WIDTH standard deviations of the same closes (the population's, as
    the band is usually drawn). None with fewer closes than that.

    While a week is under way, Yahoo's chart ends with that week's bar (dated its Monday, with a close that can lag)
    and then a separate point for the latest price: on September 30, 2026, Apple's ended with the week of September 28
    at $329.40 and $333.02 at that day's close. The two are one week, whose close so far is the latest price, so a point
    in the same calendar week (by its date in UTC, where Yahoo stamps each weekly bar early on the Monday) as the point
    before it replaces that point's close rather than counting as a week of its own. Calendar weeks, not 7 days of
    seconds: after the spring clock change a bar comes 6 days 23 hours after the one before."""
    closes, week = [], None
    for t, c in weekly or []:
        if not c:
            continue
        w = dt.datetime.fromtimestamp(t, dt.timezone.utc).isocalendar()[:2]
        if closes and w == week:
            closes[-1] = c
        else:
            closes.append(c)
            week = w
    if len(closes) < BAND_WEEKS:
        return None
    last = closes[-BAND_WEEKS:]
    return statistics.fmean(last) + BAND_WIDTH * statistics.pstdev(last)


def _industries(universe, metrics, prices):
    """(industries, the whole market's median price to sales). industries lists each Nasdaq industry with at least
    INDUSTRY_MIN_LISTINGS companies that have a year of weekly prices (INDUSTRY_MIN_WEEKS) and a 52-week high, furthest
    below its highs first, as {"industry", "sector", "count", "median_drawdown", "median_ps", "out_of_favor"}.

    count is those companies, one listing each (the one with the largest market value where a company lists several
    share classes), and median_drawdown the median of how far each trades below its 52-week high. median_ps is the
    median price to sales of the industry's companies with figures and sales (_ps), None without any, to set beside the
    market's median over every listing with them: an industry priced far under the market may be cheap as a whole.
    sector is the one most of the industry's companies are in, since Nasdaq files a few industries under several. An
    industry is out of favor when its median company trades RULES["out_of_favor_drawdown"] or more below its 52-week
    high: the whole industry has fallen, not just one company in it (the company's own out_of_favor flag)."""
    groups, market = {}, []
    for sym, u in universe.items():
        ps = _ps(u, metrics.get(sym))
        if ps is not None:
            market.append(ps)
        p = prices.get(sym) or {}
        price, high = p.get("price") or u.get("price"), p.get("high52")
        if not (u.get("industry") and price and high and len(p.get("weekly") or []) >= INDUSTRY_MIN_WEEKS):
            continue
        cos = groups.setdefault(u["industry"], {})
        best = cos.get(u["cik"])
        if best is None or (u.get("mcap") or 0) > best["mcap"]:
            cos[u["cik"]] = {"mcap": u.get("mcap") or 0, "drawdown": 1 - price / high, "ps": ps, "sector": u["sector"]}
    out = []
    for name, cos in groups.items():
        if len(cos) < INDUSTRY_MIN_LISTINGS:
            continue
        dd = statistics.median(c["drawdown"] for c in cos.values())
        ps = [c["ps"] for c in cos.values() if c["ps"] is not None]
        out.append({"industry": name, "sector": collections.Counter(c["sector"] for c in cos.values()).most_common(1)[0][0],
                    "count": len(cos), "median_drawdown": dd, "median_ps": statistics.median(ps) if ps else None,
                    "out_of_favor": dd >= RULES["out_of_favor_drawdown"]})
    out.sort(key=lambda x: (-x["median_drawdown"], x["industry"]))
    return out, statistics.median(market) if market else None


def _stale(m):
    """Whether a company's newest fiscal year of figures ended more than STALE_YEAR_DAYS before its latest balance
    sheet, so its yearly profit and cash flow are out of date (a company that stopped tagging revenue keeps the last
    year that had it). Unknown dates count as current."""
    end, bal = m.get("fiscal_year_end"), m.get("balance_as_of")
    if not (end and bal):
        return False
    return (dt.date.fromisoformat(bal) - dt.date.fromisoformat(end)).days > STALE_YEAR_DAYS


def _net_cash_row(sym, u, m):
    """The "Under 1x cash" row of a company worth less than its net cash, or None.

    Net cash here is cash and short-term investments (fundamentals' cash_and_st_investments) less all debt and the
    claims that come before the common shares or beside them (preferred stock and minority holders' stakes in its
    subsidiaries, as report.other_claims counts them): roughly the money left for common shareholders after paying off
    everything it borrowed, without the long-term securities the screener's own net cash counts, which may take years to
    turn into cash. Other bills (payables, leases, deferred revenue) are not subtracted, as the page says. It needs all
    of the debt, so a company whose debt read looks far too small (debt_known False) is left out, and so is one with
    shareholders' equity of nothing or less, which owes more than it owns however much cash it holds: Purple Innovation's
    $127M of debt moved to a line fundamentals doesn't read in its June 2026 10-Q, leaving $0 of debt read beside equity
    of -$64M. Profit is not required: many of these companies are burning cash, so the row says how many years its cash
    and short-term investments would last at the latest fiscal year's burn (negative free cash flow), or why it can't
    (runway_note "not_burning" for free cash flow of nothing or more, "unknown" where free cash flow could not be read,
    "stale" where the fiscal year is out of date, _stale)."""
    liquid, equity = m.get("cash_and_st_investments"), m.get("equity")
    if m.get("debt_known") is not True or not u.get("mcap") or liquid is None or not (equity and equity > 0):
        return None
    debt = m.get("total_debt") or 0
    claims = (m.get("preferred_stock") or 0) + (m.get("minority_interest") or 0)
    net = liquid - debt - claims
    if not u["mcap"] < net:
        return None
    fcf, stale = m.get("fcf"), _stale(m)
    burning = fcf is not None and fcf < 0 and not stale
    return {"symbol": sym, "name": u["name"], "sector": u["sector"], "industry": u["industry"], "mcap": u["mcap"],
            "cash": liquid, "total_debt": debt, "other_claims": claims, "net_cash": net,
            "mcap_to_net_cash": u["mcap"] / net, "fcf": None if stale else fcf,
            "runway_years": liquid / -fcf if burning else None,
            "runway_note": None if burning else "stale" if stale else "unknown" if fcf is None else "not_burning",
            "fiscal_year": m.get("fiscal_year"), "balance_as_of": m.get("balance_as_of")}


def _financial_row(sym, u, m):
    """The "Banks and insurers under book value" row of a profitable financial company priced under its book value, or
    None. REITs and business development companies (revenue_basis "investment_income") are left out: what they own is
    property or loans to private companies, valued their own way. So are the land developers and property companies
    Nasdaq files under Finance with the industry "Real Estate" (Five Point Holdings, Forestar, Tejon Ranch on September
    30, 2026), whose book value is land carried at cost, not a bank's or an insurer's loans and securities.

    A bank (fundamentals' bank fields) is measured on tangible book value, what common shareholders would keep if the
    premiums paid for acquisitions were worth nothing (tangible_equity, None where a line it needs is unknown), with its
    profit and return on equity left to common shareholders (adj_net_income_common, adj_roe_common). Any other
    financial company (an insurer, a broker, a lender), for which fundamentals works out no bank fields, is measured on
    book value (_book), with its profit without one-time items left to common shareholders: less preferred dividends
    and the other amounts fundamentals' common_gap holds, since eHealth's $40M of 2025 profit was a $10M loss for its
    common shares once its preferred holders were paid. Return on equity is that profit over that book. Both profit
    tests must pass: a stock under book value while losing money may be cheap for a reason. A company whose newest
    fiscal year is out of date (_stale) is left out, as its profit can't be called last year's. book_basis says which
    book the row used ("tangible" or "book")."""
    if m.get("reit") or m.get("revenue_basis") == "investment_income" or u.get("industry") == "Real Estate" \
            or not u.get("mcap") or _stale(m):
        return None
    if m.get("bank"):
        basis, book, profit, roe = ("tangible", m.get("tangible_equity"), m.get("adj_net_income_common"),
                                    m.get("adj_roe_common"))
    else:
        basis, book = "book", _book(m)
        adj = m.get("adj_net_income")
        profit = None if adj is None else adj - (m.get("common_gap") or 0)
        roe = profit / book if profit is not None and book and book > 0 else None
    if not (book and book > 0 and profit is not None and profit > 0 and roe is not None and roe > 0):
        return None
    if not u["mcap"] < book:
        return None
    return {"symbol": sym, "name": u["name"], "industry": u["industry"], "kind": "bank" if m.get("bank") else "other",
            "mcap": u["mcap"], "book_basis": basis, "book": book, "price_to_book": u["mcap"] / book,
            "net_income": profit, "roe": roe, "fiscal_year": m.get("fiscal_year"),
            "balance_as_of": m.get("balance_as_of")}


def _giant_row(sym, u, m):
    """The "Overpriced giants" row of a company worth over GIANT_MIN_MCAP at more than GIANT_MIN_PS times its sales, or
    None. Not a company whose revenue is read the way a lender's is (revenue_basis), nor one whose sales figure looks
    incomplete (_ps), since its price to sales would be off, nor a REIT: its sales are rent, which every REIT is priced
    at many times (Welltower, Prologis and Equinix at 11 to 15 times on September 30, 2026), so it is valued on funds
    from operations instead. Any country: the list is of what to avoid, not to buy.

    Sales are the last twelve months' where its quarters give them (fundamentals' ttm_revenue, ending ttm_end), else its
    newest fiscal year's: a fast grower's fiscal year can be 13 months old, which put Micron at 32 times sales on its
    year to August 2025 and 21 times on its last twelve months. sales_basis says which ("ttm" or "fiscal_year")."""
    if m.get("revenue_basis") or m.get("reit") or sales_doubtful(m) or not u.get("mcap"):
        return None
    ttm = m.get("ttm_revenue")
    sales, basis, end = (ttm, "ttm", m.get("ttm_end")) if ttm and ttm > 0 else \
        (m.get("revenue"), "fiscal_year", m.get("fiscal_year_end"))
    if not (sales and sales > 0):
        return None
    ps = u["mcap"] / sales
    if not (u["mcap"] > GIANT_MIN_MCAP and ps > GIANT_MIN_PS):
        return None
    return {"symbol": sym, "name": u["name"], "sector": u["sector"], "mcap": u["mcap"], "ps": ps, "revenue": sales,
            "sales_basis": basis, "sales_end": end, "fiscal_year": m.get("fiscal_year")}


def _one_per_company(rows, universe):
    """A watch list's rows with one per company (SEC CIK) where several of its share classes qualify: the listing with
    the largest market value, so the company's figures are measured at its dearest class's price. Nasdaq values each
    class at its own price times every class's shares: on September 30, 2026, $4,208B for Alphabet's GOOGL and $4,167B
    for GOOG, $660M and $644M for Donegal Group's DGICA and DGICB, both of which the book value list would take."""
    best = {}
    for r in rows:
        cik = universe[r["symbol"]]["cik"]
        if cik not in best or (r["mcap"], r["symbol"]) > (best[cik]["mcap"], best[cik]["symbol"]):
            best[cik] = r
    return list(best.values())


def run(universe, metrics, prices, analyst_counter, insiders=None):
    """universe/metrics/prices keyed by symbol. Returns the screener JSON payload.

    `insiders` is {symbol: {"tier": 1, 2 or 3, "avg_price": float or None, "total_value": float}} for the companies with
    open-market insider purchases in the insider scan's window (build.py makes it from insiders.build's companies), or
    None where no scan was run: no company then gets the insider flags or points, and the payload's insiders_checked
    says so.

    Besides the ranked list ("results") and the companies left out of it for a check that could not be confirmed
    ("unverified"), the payload has the industries measured for the out-of-favor test ("industries", with the market's
    median price to sales in "market_median_ps") and three watch lists that don't touch the ranked list or its scores:
    US companies worth less than their net cash ("net_cash_list", _net_cash_row), US banks and other financial companies
    under book value ("financials_list", _financial_row), and the largest companies priced at over 10 times sales
    ("giants", _giant_row). The US lists name, in "net_cash_unconfirmed" and "financials_unconfirmed", the companies that
    met every other test but whose US registration could not be confirmed."""
    candidates, debt_unread, checked_by_sector = [], [], {}
    cash_rows, fin_rows, giants = [], [], []
    for sym, u in universe.items():
        m = metrics.get(sym)
        giant = m and _giant_row(sym, u, m)
        if giant:
            giants.append(giant)
        if u["sector"] in EXCLUDED_SECTORS:
            # Not screened, but its banks, insurers and other financial companies under book value are listed.
            row = m and _us_listing(u, m) and _financial_row(sym, u, m)
            if row:
                fin_rows.append(row)
            continue
        checked_by_sector[u["sector"]] = checked_by_sector.get(u["sector"], 0) + 1
        if not m or u["country"] not in ("United States", ""):
            continue
        if m.get("revenue_basis"):
            # Revenue read the way a lender's or a business development company's is (fundamentals.derive): its cash,
            # debt and cash flow are its business, as for the Finance sector, whatever sector Nasdaq files it under.
            continue
        if not (m.get("loc") or "").startswith("US"):
            continue
        row = _net_cash_row(sym, u, m)
        if row:
            cash_rows.append(row)
        checks, ps = _checks(u, m)
        failed = [key for key, ok, _ in checks if not ok]
        if not failed:
            candidates.append((sym, ps))
        elif failed == ["debt"] and _debt_unread(m):
            debt_unread.append(sym)

    # Only survivors need the slower per-company lookups. Those left out for their debt are only listed, so a
    # registration an earlier run saved is enough for them, and so it is for the watch lists, whose companies' records
    # are looked up once and kept between runs.
    syms = [s for s, _ in candidates]
    fresh = {universe[s]["cik"] for s in syms}
    listed = [r["symbol"] for r in cash_rows + fin_rows]
    found = registrations(sorted(fresh), reuse=sorted({universe[s]["cik"] for s in debt_unread + listed} - fresh))
    regs, us, unverified = {}, [], []
    for s in syms + debt_unread:
        r = found[universe[s]["cik"]]
        debt = s in debt_unread
        if isinstance(r, dict):
            regs[s] = r
            places = (r["incorporated"], r["hq"])
            if all(p in US_STATES for p in places):
                r = None
                if not debt:
                    us.append(s)
                    continue
            elif any(p and p not in US_STATES for p in places):
                continue  # confirmed outside the US, so it fails that rule whatever its debt
            else:
                r = "blank"
        # The rules require low debt and confirmed US incorporation and headquarters, so these stay out but are listed,
        # with each check that could not be confirmed ("debt", "us") and why.
        unconfirmed = (["debt"] if debt else []) + (["us"] if r else [])
        unverified.append({"symbol": s, "name": universe[s]["name"], "sector": universe[s]["sector"],
                           "reason": " ".join(([DEBT_UNREAD] if debt else []) + ([UNVERIFIED_REASONS[r]] if r else [])),
                           "unconfirmed": unconfirmed})
    for key, why in (("us", "could not be checked with the SEC"), ("debt", "had debt that could not be read reliably")):
        left = [x["symbol"] for x in unverified if key in x["unconfirmed"]]
        if left:
            print(f"  {len(left)} screener candidates {why} and were left out: {', '.join(left)}", flush=True)
    # The watch lists keep the companies the SEC confirms are incorporated and based in the US. One it places outside
    # the US fails the test and is dropped; one it can't place either way is named as unconfirmed.
    lists = {}
    for name, rows_in in (("net_cash", _one_per_company(cash_rows, universe)),
                          ("financials", _one_per_company(fin_rows, universe))):
        kept, unsure = [], []
        for r in rows_in:
            ok = _us_confirmed(found[universe[r["symbol"]]["cik"]])
            if ok:
                kept.append(r)
            elif ok is None:
                unsure.append(r["symbol"])
        if unsure:
            print(f"  {len(unsure)} {name} list companies could not be confirmed as US companies with the SEC and were "
                  f"left out: {', '.join(sorted(unsure))}", flush=True)
        lists[name] = kept, sorted(unsure)
    analysts = analyst_counter.counts(us)
    industries, market_ps = _industries(universe, metrics, prices)
    slumped = {x["industry"] for x in industries if x["out_of_favor"]}

    rows = []
    ps_by = dict(candidates)
    for sym in us:
        u, m, p = universe[sym], metrics[sym], prices.get(sym) or {}
        price = p.get("price") or u["price"]
        high = p.get("high52")
        drawdown = (1 - price / high) if high else None
        book = _book(m)
        band = _upper_band(p.get("weekly"))
        bought = (insiders or {}).get(sym)
        bought = bought if bought and bought.get("tier") in (1, 2) else None
        row = {
            "symbol": sym,
            "name": u["name"],
            "sector": u["sector"],
            "industry": u["industry"],
            "hq": regs[sym]["hq"],
            "price": price,
            "mcap": u["mcap"],
            "ps": round(ps_by[sym], 3),
            # Market value over book value (equity less preferred stock, _book), None where that is nothing or less.
            "pb": round(u["mcap"] / book, 3) if book and book > 0 else None,
            "book_value": book,
            # Without one-time items, as the margin rule and the score use it.
            "net_margin": m["adj_net_margin"],
            "net_margin_reported": m["net_margin"],
            "one_time_note": one_time_note(m, ffo=False),
            "lt_de": m["lt_debt_to_equity"],
            "total_debt": m["total_debt"],
            "net_cash": m["net_cash"],
            "fcf": m["fcf"],
            "fcf_yield": (m["fcf"] / u["mcap"]) if u["mcap"] else None,
            "revenue": m["revenue"],
            "revenue_growth": m["revenue_growth"],
            "profitable_years": m["profitable_years"],
            # As reported, so the page can say when leaving out one-time items changed the count.
            "profitable_years_reported": m.get("profitable_years_reported"),
            "years_checked": m["years_checked"],
            "fiscal_year": m["fiscal_year"],
            "high52": high,
            "drawdown": drawdown,
            # The upper Bollinger band from the weekly closes (_upper_band), None with under BAND_WEEKS of them.
            "upper_band": band,
            "analysts": analysts.get(sym),
            # Tier 1 or 2 open-market insider buying in the insider scan's window: the tier, the dollars bought and the
            # average price paid per share (None where the filings give no price). None without such buying.
            "insiders": {k: bought.get(k) for k in ("tier", "total_value", "avg_price")} if bought else None,
        }
        # Net cash is None where the latest balance sheet tags no cash (fundamentals.derive's cash_known).
        row["debt_status"] = "none" if row["total_debt"] <= 0 else "net_cash" if (row["net_cash"] or 0) > 0 else "low"
        row["flags"] = []
        if drawdown is not None and drawdown >= RULES["out_of_favor_drawdown"]:
            row["flags"].append("out_of_favor")
        if row["analysts"] is not None and row["analysts"] <= RULES["undiscovered_max_analysts"]:
            row["flags"].append("undiscovered")
        if u["industry"] in slumped:
            row["flags"].append("industry_out_of_favor")
        if row["pb"] is not None and row["pb"] < UNDER_BOOK_FULL:
            row["flags"].append("under_book")
        if bought:
            row["flags"].append("insiders_buying")
            if bought.get("avg_price") and price < bought["avg_price"]:
                row["flags"].append("below_insider_price")
        if band is not None and price > band:
            row["flags"].append("above_upper_band")
        row["score"], row["score_parts"] = _score(row)
        rows.append(row)

    rows.sort(key=lambda r: -r["score"])
    sectors = []
    for s in sorted({u["sector"] for u in universe.values()}):
        sectors.append({"name": s, "checked": checked_by_sector.get(s, 0), "passed": sum(1 for r in rows if r["sector"] == s)})
        if s in EXCLUDED_SECTORS:
            sectors[-1]["excluded"] = EXCLUDED_SECTORS[s]
    return {
        "rules": RULES,
        "score_weights": dict(SCORE_WEIGHTS),
        "sectors": sectors,
        "results": rows,
        "unverified": sorted(unverified, key=lambda x: x["symbol"]),
        "insiders_checked": insiders is not None,
        "industries": industries,
        "market_median_ps": market_ps,
        # Cheapest against their net cash first; lowest price to (tangible) book first; highest price to sales first.
        "net_cash_list": sorted(lists["net_cash"][0], key=lambda r: (r["mcap_to_net_cash"], r["symbol"])),
        "net_cash_unconfirmed": lists["net_cash"][1],
        "financials_list": sorted(lists["financials"][0], key=lambda r: (r["price_to_book"], r["symbol"])),
        "financials_unconfirmed": lists["financials"][1],
        "giants": sorted(_one_per_company(giants, universe), key=lambda r: (-r["ps"], r["symbol"])),
    }
