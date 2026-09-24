"""Sector screener: viable, undervalued, US small caps."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, Throttled, sec_json
from .report import one_time_note

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

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
    "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)

RULES = {
    "max_ps": 2.0,
    "max_mcap": 3e9,
    "bonus_mcap": 2e9,
    "max_lt_de": 0.5,
    "min_net_margin": 0.08,
    "min_profitable_years": 2,
    "min_revenue_growth": -0.05,
    "out_of_favor_drawdown": 0.25,
    "undiscovered_max_analysts": 2,
}


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


def registrations(ciks):
    """{cik: record, or a key of UNVERIFIED_REASONS when the record could not be read}."""
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

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = dict(zip(ciks, pool.map(one, ciks)))
    # Dropped connections and gateway errors usually clear up, so each one gets a second try.
    for cik in [c for c, r in out.items() if r == "failed"]:
        out[cik] = one(cik)

    # Where a company is incorporated and based rarely changes, so a record from an earlier run stands in.
    cache = _load_cache()
    for cik, r in out.items():
        if isinstance(r, dict):
            cache[str(cik)] = r
        elif str(cik) in cache:
            out[cik] = cache[str(cik)]
    _save_cache(cache)
    return out


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


def _score(row):
    """0-100 rank score. Every component is listed so the page can explain it."""
    parts = []
    ps = row["ps"]
    parts.append(("Cheap vs. sales", max(0.0, (RULES["max_ps"] - ps) / RULES["max_ps"]) * 20, 20))
    if row["total_debt"] <= 0:
        parts.append(("No debt at all", 20, 20))
    elif row["net_cash"] > 0:
        parts.append(("More cash than debt", 14, 20))
    else:
        parts.append(("Low debt", max(0.0, 1 - row["lt_de"] / RULES["max_lt_de"]) * 8, 20))
    parts.append(("Profit margin", min(row["net_margin"], 0.30) / 0.30 * 15, 15))
    fy = row["fcf_yield"] or 0
    parts.append(("Cash generation", max(0.0, min(fy, 0.15)) / 0.15 * 15, 15))
    g = row["revenue_growth"] or 0
    parts.append(("Sales growth", max(0.0, min(g, 0.20)) / 0.20 * 10, 10))
    parts.append(("Under $2B", 5 if row["mcap"] < RULES["bonus_mcap"] else 0, 5))
    dd = row["drawdown"]
    parts.append(("Out of favor", max(0.0, min(dd or 0, 0.5)) / 0.5 * 8, 8))
    a = row["analysts"]
    parts.append(("Few analysts", 7 if a == 0 else 4 if a is not None and a <= RULES["undiscovered_max_analysts"] else 0, 7))
    # Whole points (half rounds up), so the parts listed on the page add up exactly to the score.
    parts = [(label, int(pts + 0.5), mx) for label, pts, mx in parts]
    return sum(p for _, p, _ in parts), [{"label": l, "points": p, "max": mx} for l, p, mx in parts]


def run(universe, metrics, prices, analyst_counter):
    """universe/metrics/prices keyed by symbol. Returns the screener JSON payload."""
    candidates, checked_by_sector = [], {}
    for sym, u in universe.items():
        if u["sector"] in EXCLUDED_SECTORS:
            continue
        m = metrics.get(sym)
        checked_by_sector[u["sector"]] = checked_by_sector.get(u["sector"], 0) + 1
        if not m or u["country"] not in ("United States", ""):
            continue
        if not (m.get("loc") or "").startswith("US"):
            continue
        checks, ps = _checks(u, m)
        if all(ok for _, ok, _ in checks):
            candidates.append((sym, ps))

    # Only survivors need the slower per-company lookups.
    syms = [s for s, _ in candidates]
    found = registrations(sorted({universe[s]["cik"] for s in syms}))
    regs, us, unverified = {}, [], []
    for s in syms:
        r = found[universe[s]["cik"]]
        if isinstance(r, dict):
            regs[s] = r
            places = (r["incorporated"], r["hq"])
            if all(p in US_STATES for p in places):
                us.append(s)
                continue
            if any(p and p not in US_STATES for p in places):
                continue  # confirmed outside the US
            r = "blank"
        # The rules require confirmed US incorporation and headquarters, so these stay out but are listed.
        unverified.append({"symbol": s, "name": universe[s]["name"], "sector": universe[s]["sector"],
                           "reason": UNVERIFIED_REASONS[r]})
    if unverified:
        print(f"  {len(unverified)} screener candidates could not be checked with the SEC and were left out", flush=True)
    analysts = analyst_counter.counts(us)

    rows = []
    ps_by = dict(candidates)
    for sym in us:
        u, m, p = universe[sym], metrics[sym], prices.get(sym) or {}
        price = p.get("price") or u["price"]
        high = p.get("high52")
        drawdown = (1 - price / high) if high else None
        row = {
            "symbol": sym,
            "name": u["name"],
            "sector": u["sector"],
            "industry": u["industry"],
            "hq": regs[sym]["hq"],
            "price": price,
            "mcap": u["mcap"],
            "ps": round(ps_by[sym], 3),
            # Without one-time items, as the margin rule and the score use it.
            "net_margin": m["adj_net_margin"],
            "net_margin_reported": m["net_margin"],
            "one_time_note": one_time_note(m),
            "lt_de": m["lt_debt_to_equity"],
            "total_debt": m["total_debt"],
            "net_cash": m["net_cash"],
            "fcf": m["fcf"],
            "fcf_yield": (m["fcf"] / u["mcap"]) if u["mcap"] else None,
            "revenue": m["revenue"],
            "revenue_growth": m["revenue_growth"],
            "profitable_years": m["profitable_years"],
            "years_checked": m["years_checked"],
            "fiscal_year": m["fiscal_year"],
            "high52": high,
            "drawdown": drawdown,
            "analysts": analysts.get(sym),
        }
        row["debt_status"] = "none" if row["total_debt"] <= 0 else "net_cash" if row["net_cash"] > 0 else "low"
        row["flags"] = []
        if drawdown is not None and drawdown >= RULES["out_of_favor_drawdown"]:
            row["flags"].append("out_of_favor")
        if row["analysts"] is not None and row["analysts"] <= RULES["undiscovered_max_analysts"]:
            row["flags"].append("undiscovered")
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
        "sectors": sectors,
        "results": rows,
        "unverified": sorted(unverified, key=lambda x: x["symbol"]),
    }
