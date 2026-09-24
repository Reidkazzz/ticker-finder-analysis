"""Company financials from SEC XBRL "frames": one request returns a single line item for every filer."""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, Throttled, sec_json

# Capex tags are combined by _capex(). Depreciation only tells whether a company without a readable capex
# tag must still have capex.
CAPEX = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToExploreAndDevelopOilAndGasProperties",
    "PaymentsToAcquireOilAndGasPropertyAndEquipment",
    "PaymentsToAcquireOilAndGasProperty",
]
DEPRECIATION = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation"]
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
    "capex": CAPEX + DEPRECIATION,
}
# Without these for the two newest years, every company's latest figures would silently change.
CRITICAL = {"revenue", "net_income", "ocf", "capex"}

# Debt, read by _debt(). Instrument tags (convertibles, notes, credit lines) often restate pieces of the
# broad totals in footnotes, so they only count when they add up to more than the broad tags do.
DEBT_FAMILIES = [  # (whole amount, noncurrent part, current part)
    (["ConvertibleNotesPayable"], ["ConvertibleDebtNoncurrent", "ConvertibleLongTermNotesPayable"],
     ["ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent"]),
    (["NotesPayable"], ["LongTermNotesPayable"], ["NotesPayableCurrent"]),
    (["LineOfCredit"], ["LongTermLineOfCredit"], ["LinesOfCreditCurrent"]),
    (["SeniorNotes"], [], []),
    (["SecuredDebt"], [], []),
    (["LoansPayable"], [], []),
    ([], ["OtherLongTermDebtNoncurrent"], []),
]
DEBT_TOTALS = ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]
DEBT = [
    "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesAndLoans", "LongTermDebt",
    "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "DebtCurrent", "ShortTermBorrowings", "CommercialPaper",
    *DEBT_TOTALS, *(t for family in DEBT_FAMILIES for part in family for t in part),
]
INSTANT = {
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "st_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "debt": DEBT,
}
# A company's balance sheet date is the newest quarter in which it reported one of these.
ANCHORS = INSTANT["equity"] + INSTANT["current_assets"]

RETRY_PAUSE = 60  # seconds to wait before retrying frames that failed


def _years(today):
    # A frame files each fiscal year under the calendar year it mostly covers (a July-June year counts for the
    # year it ends), so from July the current calendar year already holds filed annual reports. derive() uses
    # each company's newest filed year, and the fifth year keeps four full years for companies still to file.
    last = today.year if today.month >= 7 else today.year - 1
    return list(range(last - 4, last + 1))


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
        return {}  # nobody reported this line item for the period
    return {d["cik"]: d for d in data}


def _fetch_all(jobs):
    """Every job's frame, or None where it kept failing. After a pause, failures get one more sequential try."""
    blocked = threading.Event()  # once the SEC blocks us, stop asking until the retry pass
    errors = []

    def one(job):
        if blocked.is_set():
            return None
        try:
            return _frame(*job[3:])
        except Throttled:
            blocked.set()
        except Exception as e:  # a timeout, dropped connection or server error on one of many requests
            errors.append(f"{job[4]} {job[6]}: {e!r}")
        return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(one, jobs))
    todo = [i for i, f in enumerate(frames) if f is None]
    if todo:
        why = "the SEC is throttling" if blocked.is_set() else f"first error {errors[0]}"
        print(f"  {len(todo)} of {len(jobs)} SEC frame requests failed ({why}); retrying in {RETRY_PAUSE}s", flush=True)
        time.sleep(RETRY_PAUSE)
        blocked.clear()
        misses = 0
        for i in todo:
            frames[i] = one(jobs[i])
            misses = 0 if frames[i] is not None else misses + 1
            if blocked.is_set() or misses >= 5:
                break  # still down, so don't spend minutes on every remaining request
    return frames


def load_fundamentals(today=None):
    """Returns {"years": [...], "companies": {cik: {"annual": {year: {...}}, "latest": {...}, "loc": "US-CA", ...}}}.

    Raises RuntimeError when the SEC can't supply the newest years' income or cash flow statements or any
    recent balance sheet, rather than publishing figures that would quietly fall back a year for everyone.
    """
    today = today or dt.date.today()
    years = _years(today)
    quarters = _quarters(today)

    jobs = []  # (section, metric, year or quarter, taxonomy, tag, unit, frame period)
    for metric, tags in DURATION.items():
        for y in years:
            jobs += [("annual", metric, y, "us-gaap", t, "USD", f"CY{y}") for t in tags]
    for y in years[-3:]:  # three years, so companies whose newest year isn't filed yet still get a dilution figure
        jobs.append(("annual", "diluted_shares", y, "us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", f"CY{y}"))
    for q in quarters:
        jobs += [("instant", metric, q, "us-gaap", t, "USD", q) for metric, tags in INSTANT.items() for t in tags]
        jobs.append(("instant", "shares_out", q, "dei", "EntityCommonStockSharesOutstanding", "shares", q))
    frames = _fetch_all(jobs)

    failed = {(j[1], j[2]) for j, f in zip(jobs, frames) if f is None}
    bad_quarters = {p for m, p in failed if p in quarters and m != "shares_out"}
    critical = sorted(f"{m} CY{p}" for m, p in failed if m in CRITICAL and p in years[-2:])
    if critical or len(bad_quarters) == len(quarters):
        raise RuntimeError("SEC financial statements unavailable after retries: "
                           + (", ".join(critical) or "every recent balance sheet"))
    if failed:
        # A metric with any failed tag is left blank for that period, since a missing tag would silently
        # change which tag wins (or what gets summed) for some companies.
        print("  WARNING: left blank for every company after SEC request failures: "
              + ", ".join(sorted(f"{m} {p if p in quarters else f'CY{p}'}" for m, p in failed))
              + (f". Balance sheets skip {', '.join(sorted(bad_quarters))}." if bad_quarters else ""), flush=True)

    annual, inst, shares, info = {}, {}, {}, {}
    for (section, metric, period, _tax, tag, _unit, _p), frame in zip(jobs, frames):
        if frame is None or (metric, period) in failed or (section == "instant" and metric != "shares_out" and period in bad_quarters):
            continue
        for cik, d in frame.items():
            if section == "annual":
                annual.setdefault(cik, {}).setdefault(period, {})[tag] = d
            elif metric == "shares_out":
                shares.setdefault(cik, {})[period] = d["val"]
            else:
                inst.setdefault(cik, {}).setdefault(period, {})[tag] = d
            c = info.setdefault(cik, {})
            if d.get("loc") and d["loc"] != "-" and (section == "annual" or "loc" not in c):
                c["loc"] = d["loc"]  # each metric's annual frames run oldest year first, so a recent filing's location wins
            c.setdefault("name", d.get("entityName"))

    reported = _check_net_income(annual)
    companies = {}
    for cik, c in info.items():
        facts = annual.get(cik, {})
        has_capex = any(_capex(fy) is not None for fy in facts.values())
        c["annual"] = {y: _annual(facts.get(y, {}), has_capex, ("capex", y) in failed, reported.get((cik, y), False))
                       for y in years}
        c["latest"] = _balance_sheet(inst.get(cik, {}), quarters)
        s = next((shares[cik][q] for q in quarters if q in shares.get(cik, {})), None)
        if s is not None:
            c["latest"]["shares_out"] = s
        companies[cik] = c
    return {"years": years, "companies": companies}


def _first(facts, tags):
    return next((facts[t] for t in tags if t in facts), None)


def _val(fact):
    return fact["val"] if fact else None


def _annual(facts, has_capex, capex_failed, report):
    rev = _first(facts, DURATION["revenue"])
    ocf = _val(_first(facts, DURATION["ocf"]))
    capex = _capex(facts)
    dep = _val(_first(facts, DEPRECIATION))
    if ocf is None or capex_failed:
        fcf = None
    elif capex is not None:
        fcf = ocf - capex
    elif has_capex or (dep and dep > 0.01 * abs(_val(rev) or 0)):
        # It reports capex in other years, or depreciation says it owns assets it must replace, so its capex
        # is under a tag this doesn't read. Operating cash flow alone would overstate free cash flow.
        fcf = None
    else:
        fcf = ocf
    return {
        "revenue": _val(rev),
        "net_income": _net_income(facts, report),
        "operating_income": _val(facts.get("OperatingIncomeLoss")),
        "fcf": fcf,
        "diluted_shares": _val(facts.get("WeightedAverageNumberOfDilutedSharesOutstanding")),
        "end": (rev or facts.get("NetIncomeLoss") or {}).get("end"),
    }


def _capex(facts):
    """Capital spending. Oil and gas producers report drilling spend on their own lines, next to ordinary
    property purchases. PaymentsToAcquireProductiveAssets is broader and already includes it."""
    v = lambda t: _val(facts.get(t))
    ppe, broad = v(CAPEX[0]), v(CAPEX[1])
    og = [x for x in (v(CAPEX[2]), v(CAPEX[3])) if x is not None]
    og = sum(og) if og else None
    if ppe is not None:
        return ppe + og if og is not None and og != ppe else ppe  # an equal figure is one line tagged twice
    if broad is not None or og is not None:
        return max(x for x in (broad, og) if x is not None)
    return v(CAPEX[4])  # property purchases: usually acquisitions, so only when nothing else is reported


NEEDS_REPORT = object()


def _net_income(facts, report=False):
    """NetIncomeLoss, checked for proxy statements. A proxy's pay-versus-performance table is tagged NetIncomeLoss
    and filed after the annual report, so the frame shows it, and it is often unscaled (thousands read as dollars).

    A suspect figure returns NEEDS_REPORT while `report` is None. Otherwise `report` is the annual report's own
    figure, which replaces it, or False when that lookup found nothing.
    """
    ni = facts.get("NetIncomeLoss")
    if ni is None:
        return _val(facts.get("ProfitLoss") or facts.get("NetIncomeLossAvailableToCommonStockholdersBasic"))
    rev = _first(facts, DURATION["revenue"])
    x, r = ni["val"], _val(rev)
    if rev and ni["accn"] == rev["accn"]:
        return None if r > 50e6 and abs(x) < 1e-4 * r else x
    # It came from another filing, so compare it with the annual report's own figures (from the filing that
    # reported revenue, or from any other filing when there is no revenue). A wrong scale is off by 1,000x.
    sane = lambda v: not (r and r > 10e6) or 1e-3 * r <= abs(v) <= 50 * r
    alts = [facts[t]["val"] for t in ("NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss")
            if t in facts and facts[t]["val"] and facts[t]["accn"] != ni["accn"] and (not rev or facts[t]["accn"] == rev["accn"])]
    if any(1 / 50 < abs(x / a) < 50 for a in alts) or (not alts and sane(x)):
        return x
    if report is None:
        return NEEDS_REPORT
    if report is not False:
        return report
    return next((a for a in alts if sane(a)), None)


def _report_net_income(cik, ends):
    """{fiscal year end: net income} from the company's own 10-K filings, the latest filing winning."""
    facts = sec_json(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/NetIncomeLoss.json")["units"].get("USD", [])
    out = {}
    for f in sorted(facts, key=lambda f: f.get("filed", "")):
        if f.get("form", "").startswith("10-K") and f.get("start") and f["end"] in ends:
            if (dt.date.fromisoformat(f["end"]) - dt.date.fromisoformat(f["start"])).days > 300:
                out[f["end"]] = f["val"]
    return out


def _check_net_income(annual):
    """{(cik, year): the annual report's net income, or False} for every frame figure that looks like a proxy's."""
    suspects = {}
    for cik, by_year in annual.items():
        for y, facts in by_year.items():
            if _net_income(facts, None) is NEEDS_REPORT:
                suspects.setdefault(cik, {})[y] = facts["NetIncomeLoss"]["end"]
    if not suspects:
        return {}
    blocked = threading.Event()

    def look(cik):
        if not blocked.is_set():
            try:
                return _report_net_income(cik, set(suspects[cik].values()))
            except Throttled:
                blocked.set()
            except Exception:
                pass
        return {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(suspects, pool.map(look, suspects)))
    out = {(cik, y): found[cik].get(end, False) for cik, ys in suspects.items() for y, end in ys.items()}
    fixed = sum(1 for v in out.values() if v is not False)
    print(f"  {len(out)} net income figures came from a later filing and disagreed with the annual report; "
          f"{fixed} replaced with the 10-K's own figure" + (" (the SEC throttled the rest)" if blocked.is_set() else "")
          + ", the rest use the 10-K's other net income tags or stay blank", flush=True)
    return out


def _balance_sheet(by_quarter, quarters):
    """Every balance sheet figure from the same quarter, so one stale line can't mix with newer ones."""
    q = next((q for q in quarters if any(t in by_quarter.get(q, {}) for t in ANCHORS)), None)
    if q is None:
        return {}
    facts = by_quarter[q]
    total, lt, reported = _debt({t: d["val"] for t, d in facts.items()})
    out = {m: _val(_first(facts, INSTANT[m])) for m in ("equity", "cash", "st_investments", "current_assets", "current_liabilities")}
    out.update(as_of=_first(facts, ANCHORS).get("end"), total_debt=total, lt_debt=lt, debt_reported=reported)
    return out


def _debt(v):
    """(total debt, long-term part, whether any debt figure was reported) from one quarter's tag values."""
    if not any(t in v for t in DEBT):
        return 0, 0, False
    g = lambda tags: next((v[t] for t in tags if t in v), None)

    # Broad tags. DebtCurrent already includes current maturities and short-term borrowings, and
    # LongTermDebt includes its own current portion. An identical figure under two tags is one debt.
    cur_ltd = g(["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"])
    st = g(["ShortTermBorrowings", "CommercialPaper"])
    if st == cur_ltd:
        st = None
    cur = g(["DebtCurrent"])
    if cur is None and (cur_ltd is not None or st is not None):
        cur = (cur_ltd or 0) + (st or 0)
    lt = g(["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesAndLoans"])
    # LongTermDebt also stands in when the noncurrent figure is far too small to be the same debt, which happens
    # when a company puts a single small loan or lease under that tag.
    if "LongTermDebt" in v and (lt is None or lt + (cur or 0) < 0.5 * v["LongTermDebt"]):
        lt = v["LongTermDebt"]
        if cur_ltd is not None and lt >= cur_ltd:
            lt -= cur_ltd
        elif lt == cur:
            lt = 0
    options = [((lt or 0) + (cur or 0), lt or 0)]

    # Instrument tags: one figure per kind of debt. Near-equal figures are the same debt tagged twice.
    kinds = []
    for whole, nc, c in DEBT_FAMILIES:
        w, n, cu = g(whole), g(nc), g(c)
        if w is not None or n is not None or cu is not None:
            amount = max(w or 0, (n or 0) + (cu or 0))
            kinds.append((amount, min(cu or 0, amount)))
    kept = []
    for amount, current in sorted(kinds, reverse=True):
        if not any(abs(amount - k[0]) <= 0.03 * k[0] for k in kept):
            kept.append((amount, current))
    # One figure matching all the others combined is their total (notes payable made up of senior notes and loans).
    if len(kept) > 2 and abs(sum(a for a, _ in kept[1:]) - kept[0][0]) <= 0.03 * kept[0][0]:
        kept = kept[:1]
    options.append((sum(a for a, _ in kept), sum(a - c for a, c in kept)))

    # Reported totals sometimes leave out short-term borrowings or leases, so they act as a floor.
    t = max((v[x] for x in DEBT_TOTALS if x in v), default=None)
    if t is not None:
        current = cur if cur is not None else sum(c for _, c in kept)
        options.append((t, max(t - current, 0)))
    total, long_term = max(options, key=lambda o: o[0])
    return total, long_term, True


def derive(f):
    """Turns raw line items into the ratios the screener and reports use. Missing data stays None."""
    years = sorted(f["annual"])
    series = [f["annual"][y] for y in years]
    i_last = next((i for i in reversed(range(len(series)))
                   if series[i].get("revenue") is not None and series[i].get("net_income") is not None), None)
    if i_last is None:
        return None
    # Every company is measured over the four fiscal years ending with its newest one.
    ys, ss = years[max(0, i_last - 3): i_last + 1], series[max(0, i_last - 3): i_last + 1]
    last, prev = ss[-1], ss[-2] if len(ss) > 1 else {}
    L = f["latest"]

    lt_debt = L.get("lt_debt") or 0
    total_debt = L.get("total_debt") or 0
    cash = (L.get("cash") or 0) + (L.get("st_investments") or 0)
    equity = L.get("equity")
    rev = last["revenue"]
    ni = last["net_income"]

    hist = [{"year": y, "revenue": s.get("revenue"), "net_income": s.get("net_income"), "fcf": s.get("fcf")}
            for y, s in zip(ys, ss) if s.get("revenue") is not None or s.get("net_income") is not None]
    recent3 = [s["net_income"] for s in ss[-3:] if s.get("net_income") is not None]
    revs = [(y, s["revenue"]) for y, s in zip(ys, ss) if s.get("revenue")]
    growth_1y = (rev / prev["revenue"] - 1) if prev.get("revenue") and prev["revenue"] > 0 and rev is not None else None
    cagr = None
    if len(revs) >= 3 and revs[0][1] > 0 and revs[-1][1] > 0:
        cagr = (revs[-1][1] / revs[0][1]) ** (1 / (revs[-1][0] - revs[0][0])) - 1
    # Only back-to-back years are compared, so a missing year never reads as one year of growth.
    pairs = [(a, b) for (ya, a), (yb, b) in zip(revs, revs[1:]) if yb == ya + 1]

    dil = None
    d_now, d_prev = last.get("diluted_shares"), prev.get("diluted_shares")
    if d_now and d_prev:
        dil = d_now / d_prev - 1

    return {
        "fiscal_year": ys[-1],
        "fiscal_year_end": last.get("end"),
        "balance_as_of": L.get("as_of"),
        "revenue": rev,
        "net_income": ni,
        "operating_income": last.get("operating_income"),
        "fcf": last.get("fcf"),
        "fcf_history": [s["fcf"] for s in ss[-3:] if s.get("fcf") is not None],
        "net_margin": (ni / rev) if rev else None,
        "op_margin": (last["operating_income"] / rev) if rev and last.get("operating_income") is not None else None,
        "equity": equity,
        "lt_debt": lt_debt,
        "total_debt": total_debt,
        "debt_reported": bool(L.get("debt_reported")),
        "cash": cash,
        "net_cash": cash - total_debt,
        "lt_debt_to_equity": (lt_debt / equity) if equity and equity > 0 else None,
        "current_ratio": (L["current_assets"] / L["current_liabilities"]) if L.get("current_assets") and L.get("current_liabilities") else None,
        "roe": (ni / equity) if equity and equity > 0 else None,
        "shares_out": L.get("shares_out"),
        "profitable_years": sum(1 for x in recent3 if x > 0),
        "years_checked": len(recent3),
        "revenue_growth": growth_1y,
        "revenue_cagr": cagr,
        "revenue_up_years": sum(1 for a, b in pairs if b > a),
        # Years in the back-to-back comparison, so revenue_years - 1 is the number of comparisons.
        "revenue_years": len(pairs) + 1 if revs else 0,
        "dilution": dil,
        "history": hist,
        "loc": f.get("loc"),
    }
