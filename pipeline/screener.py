"""Sector screener: viable, undervalued, US small caps."""
from concurrent.futures import ThreadPoolExecutor

from .net import sec_json

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
    """State of incorporation and business address from the SEC's company record."""
    try:
        s = sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    except Exception:
        return None
    biz = (s.get("addresses") or {}).get("business") or {}
    return {
        "incorporated": (s.get("stateOfIncorporation") or "").upper(),
        "hq": (biz.get("stateOrCountry") or "").upper(),
        "sic": s.get("sicDescription"),
    }


def _checks(u, m):
    """Each hard rule as (key, passed, plain-English note). Missing data counts as a fail."""
    ps = u["mcap"] / m["revenue"] if u.get("mcap") and m.get("revenue") and m["revenue"] > 0 else None
    out = [
        ("ps", ps is not None and ps < RULES["max_ps"], ps),
        ("mcap", bool(u.get("mcap")) and u["mcap"] < RULES["max_mcap"], u.get("mcap")),
        ("debt", m.get("lt_debt_to_equity") is not None and m["lt_debt_to_equity"] < RULES["max_lt_de"], m.get("lt_debt_to_equity")),
        ("margin", m.get("net_margin") is not None and m["net_margin"] >= RULES["min_net_margin"], m.get("net_margin")),
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
    parts.append(("Cheap vs. sales", round(max(0.0, (RULES["max_ps"] - ps) / RULES["max_ps"]) * 20, 1), 20))
    if row["total_debt"] <= 0:
        parts.append(("No debt at all", 20.0, 20))
    elif row["net_cash"] > 0:
        parts.append(("More cash than debt", 14.0, 20))
    else:
        parts.append(("Low debt", round(max(0.0, 1 - row["lt_de"] / RULES["max_lt_de"]) * 8, 1), 20))
    parts.append(("Profit margin", round(min(row["net_margin"], 0.30) / 0.30 * 15, 1), 15))
    fy = row["fcf_yield"] or 0
    parts.append(("Cash generation", round(max(0.0, min(fy, 0.15)) / 0.15 * 15, 1), 15))
    g = row["revenue_growth"] or 0
    parts.append(("Sales growth", round(max(0.0, min(g, 0.20)) / 0.20 * 10, 1), 10))
    parts.append(("Under $2B", 5.0 if row["mcap"] < RULES["bonus_mcap"] else 0.0, 5))
    dd = row["drawdown"]
    parts.append(("Out of favor", round(max(0.0, min(dd or 0, 0.5)) / 0.5 * 8, 1), 8))
    a = row["analysts"]
    parts.append(("Few analysts", 7.0 if a == 0 else 4.0 if a is not None and a <= RULES["undiscovered_max_analysts"] else 0.0, 7))
    return round(sum(p[1] for p in parts)), [{"label": l, "points": p, "max": mx} for l, p, mx in parts]


def run(universe, metrics, prices, analyst_counter):
    """universe/metrics/prices keyed by symbol. Returns the screener JSON payload."""
    candidates, checked_by_sector = [], {}
    for sym, u in universe.items():
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
    with ThreadPoolExecutor(max_workers=4) as pool:
        regs = dict(zip(syms, pool.map(lambda s: registration(universe[s]["cik"]), syms)))
    us = [s for s in syms if regs.get(s) and regs[s]["incorporated"] in US_STATES and regs[s]["hq"] in US_STATES]
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
            "net_margin": m["net_margin"],
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
    sectors = sorted({u["sector"] for u in universe.values()})
    return {
        "rules": RULES,
        "sectors": [
            {"name": s, "checked": checked_by_sector.get(s, 0), "passed": sum(1 for r in rows if r["sector"] == s)}
            for s in sectors
        ],
        "results": rows,
    }
