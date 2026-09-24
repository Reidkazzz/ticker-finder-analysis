"""Company financials from SEC XBRL "frames": one request returns a single line item for every filer."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, sec_json

# Each metric lists the XBRL tags companies use for it, in order of preference.
DURATION = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_income": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
DURATION_SHARES = {"diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"]}
INSTANT = {
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "lt_debt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "st_debt": ["DebtCurrent", "ShortTermBorrowings", "CommercialPaper"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "st_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
}


def _years(today):
    # A fiscal year is published within ~4 months of its end, so the newest complete year lags.
    last = today.year - 1 if today.month >= 5 else today.year - 2
    return [last - 3, last - 2, last - 1, last]


def _quarters(today, n=5):
    q = (today.month - 1) // 3  # the current quarter is still open
    y = today.year
    out = []
    for _ in range(n):
        q -= 1
        if q < 0:
            q, y = 3, y - 1
        out.append(f"CY{y}Q{q + 1}I")
    return out  # newest first


def _frame(taxonomy, tag, unit, period):
    try:
        data = sec_json(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json")["data"]
    except NotFound:
        return {}
    return {d["cik"]: d for d in data}


def load_fundamentals(today=None):
    """Returns {cik: {"annual": {year: {...}}, "latest": {...}, "loc": "US-CA", ...}}."""
    today = today or dt.date.today()
    years = _years(today)
    quarters = _quarters(today)

    jobs = []
    for metric, tags in DURATION.items():
        for y in years:
            for tag in tags:
                jobs.append(("annual", metric, y, "us-gaap", tag, "USD", f"CY{y}"))
    for metric, tags in DURATION_SHARES.items():
        for y in years[-2:]:
            for tag in tags:
                jobs.append(("annual", metric, y, "us-gaap", tag, "shares", f"CY{y}"))
    for metric, tags in INSTANT.items():
        for q in quarters:
            for tag in tags:
                jobs.append(("instant", metric, q, "us-gaap", tag, "USD", q))
    for q in quarters:
        jobs.append(("instant", "shares_out", q, "dei", "EntityCommonStockSharesOutstanding", "shares", q))

    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(lambda j: _frame(j[3], j[4], j[5], j[6]), jobs))

    companies = {}

    def rec(cik):
        return companies.setdefault(cik, {"annual": {y: {} for y in years}, "latest": {}, "_latest_rank": {}})

    # Annual line items: the first tag in preference order that a company reports wins.
    for (kind, metric, period, _tax, tag, _unit, _p), frame in zip(jobs, frames):
        if kind != "annual":
            continue
        tags = DURATION.get(metric) or DURATION_SHARES[metric]
        rank = tags.index(tag)
        for cik, d in frame.items():
            c = rec(cik)
            slot = c["annual"][period]
            if metric not in slot or rank < slot[f"_{metric}_rank"]:
                slot[metric] = d["val"]
                slot[f"_{metric}_rank"] = rank
                slot["end"] = d.get("end")
            if d.get("loc") and d["loc"] != "-":
                c["loc"] = d["loc"]
            c["name"] = d.get("entityName")

    # Balance sheet: the newest quarter wins, then tag preference.
    for (kind, metric, period, _tax, tag, _unit, _p), frame in zip(jobs, frames):
        if kind != "instant":
            continue
        q_rank = quarters.index(period)
        tag_rank = INSTANT[metric].index(tag) if metric in INSTANT else 0
        for cik, d in frame.items():
            c = rec(cik)
            key = (q_rank, tag_rank)
            if metric not in c["latest"] or key < c["_latest_rank"][metric]:
                c["latest"][metric] = d["val"]
                c["_latest_rank"][metric] = key
                if metric == "equity":
                    c["latest"]["as_of"] = d.get("end")
            if d.get("loc") and d["loc"] != "-":
                c.setdefault("loc", d["loc"])

    for c in companies.values():
        c.pop("_latest_rank", None)
        for slot in c["annual"].values():
            for k in [k for k in slot if k.startswith("_")]:
                slot.pop(k)
    return {"years": years, "companies": companies}


def derive(f):
    """Turns raw line items into the ratios the screener and reports use. Missing data stays None."""
    years = sorted(f["annual"])
    series = [f["annual"][y] for y in years]
    last = next((s for s in reversed(series) if s.get("revenue") is not None and s.get("net_income") is not None), None)
    if last is None:
        return None
    i_last = series.index(last)
    prev = series[i_last - 1] if i_last > 0 else {}
    L = f["latest"]

    def fcf(s):
        if s.get("ocf") is None:
            return None
        return s["ocf"] - (s.get("capex") or 0)

    lt_debt = (L.get("lt_debt") or 0)
    total_debt = lt_debt + (L.get("lt_debt_current") or 0) + (L.get("st_debt") or 0)
    cash = (L.get("cash") or 0) + (L.get("st_investments") or 0)
    equity = L.get("equity")
    rev = last["revenue"]
    ni = last["net_income"]

    hist = []
    for y, s in zip(years, series):
        if s.get("revenue") is not None or s.get("net_income") is not None:
            hist.append({"year": y, "revenue": s.get("revenue"), "net_income": s.get("net_income"), "fcf": fcf(s)})

    recent3 = [s for s in series[: i_last + 1] if s.get("net_income") is not None][-3:]
    revs = [s["revenue"] for s in series[: i_last + 1] if s.get("revenue")]
    growth_1y = (rev / prev["revenue"] - 1) if prev.get("revenue") and prev["revenue"] > 0 and rev is not None else None
    cagr = None
    if len(revs) >= 3 and revs[0] > 0 and revs[-1] > 0:
        cagr = (revs[-1] / revs[0]) ** (1 / (len(revs) - 1)) - 1
    up_years = sum(1 for a, b in zip(revs, revs[1:]) if b > a)

    dil = None
    d_now, d_prev = last.get("diluted_shares"), prev.get("diluted_shares")
    if d_now and d_prev:
        dil = d_now / d_prev - 1

    return {
        "fiscal_year": years[i_last],
        "fiscal_year_end": last.get("end"),
        "balance_as_of": L.get("as_of"),
        "revenue": rev,
        "net_income": ni,
        "operating_income": last.get("operating_income"),
        "fcf": fcf(last),
        "fcf_history": [fcf(s) for s in series[: i_last + 1] if fcf(s) is not None][-3:],
        "net_margin": (ni / rev) if rev else None,
        "op_margin": (last["operating_income"] / rev) if rev and last.get("operating_income") is not None else None,
        "equity": equity,
        "lt_debt": lt_debt,
        "total_debt": total_debt,
        "cash": cash,
        "net_cash": cash - total_debt,
        "lt_debt_to_equity": (lt_debt / equity) if equity and equity > 0 else None,
        "current_ratio": (L["current_assets"] / L["current_liabilities"]) if L.get("current_assets") and L.get("current_liabilities") else None,
        "roe": (ni / equity) if equity and equity > 0 else None,
        "shares_out": L.get("shares_out"),
        "profitable_years": sum(1 for s in recent3 if s["net_income"] > 0),
        "years_checked": len(recent3),
        "revenue_growth": growth_1y,
        "revenue_cagr": cagr,
        "revenue_up_years": up_years,
        "revenue_years": len(revs),
        "dilution": dil,
        "history": hist,
        "loc": f.get("loc"),
    }
