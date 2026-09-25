"""Company financials from SEC XBRL "frames": one request returns a single line item for every filer."""
import datetime as dt
import json
import math
import os
import statistics
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
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
# Write-downs other than goodwill, combined by _impairments(). Companies tag the same charge in different ways
# (one total, or pieces that may overlap), so the largest reading counts rather than the sum of all of them.
IMPAIRMENT = [
    "AssetImpairmentCharges",  # often the total, goodwill included
    "GoodwillAndIntangibleAssetImpairment",
    "ImpairmentOfIntangibleAssetsExcludingGoodwill",
    "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill",
    "ImpairmentOfIntangibleAssetsFinitelived",
    "ImpairmentOfLongLivedAssetsHeldForUse",
    "ImpairmentOfLongLivedAssetsToBeDisposedOf",
    "ImpairmentOfRealEstate",
    "TangibleAssetImpairmentCharges",
    "OtherAssetImpairmentCharges",
    "OperatingLeaseImpairmentLoss",
    "ImpairmentOfOilAndGasProperties",
]
# Write-downs that can't be reversed (see _impairments).
NO_REVERSAL = {"GoodwillImpairmentLoss", "GoodwillAndIntangibleAssetImpairment", "ImpairmentOfIntangibleAssetsExcludingGoodwill",
               "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill", "ImpairmentOfIntangibleAssetsFinitelived"}
# Gains (positive) and losses (negative) on selling a business or assets, combined by _sale(). The first four are
# businesses and stakes in them, the rest assets.
SALE = [
    "GainLossOnSaleOfBusiness",
    "DisposalGroupNotDiscontinuedOperationGainLossOnDisposal",
    "DeconsolidationGainOrLossAmount",
    "EquityMethodInvestmentRealizedGainLossOnDisposal",
    "GainLossOnDispositionOfAssets",
    "GainLossOnDispositionOfAssets1",
    "GainLossOnSaleOfPropertyPlantEquipment",
    "GainLossOnSaleOfOtherAssets",
    "GainsLossesOnSalesOfAssets",
    "GainLossOnDispositionOfIntangibleAssets",
]
# Gains (positive) and losses on selling real estate, which REITs report on their own lines. Only a REIT's are read,
# since funds from operations, the REIT earnings measure, leaves them out, while selling property it developed is part
# of the business for a developer or a real estate services firm (CBRE's development sales).
PROPERTY_SALE = ["GainsLossesOnSalesOfInvestmentRealEstate", "GainLossOnSaleOfProperties"]
# Income tax credits from the tax rate reconciliation, which a company earns from what it does every year (research,
# restaurant tips, clean energy), so they are part of its normal tax. The first is the total. Foreign tax credits are
# left out, since they only offset foreign tax. Reported with either sign, so they are read as amounts.
TAX_CREDITS = [
    "IncomeTaxReconciliationTaxCredits",
    "IncomeTaxReconciliationTaxCreditsResearch",
    "IncomeTaxReconciliationTaxCreditsOther",
    "IncomeTaxReconciliationTaxCreditsInvestment",
]
# Gains and losses on investment securities, sold or revalued, such as a bank selling bonds at a loss to reinvest at
# higher rates (Tompkins Financial's 2025: $78.7M). Combined like the sale tags.
SECURITIES = [
    "DebtSecuritiesAvailableForSaleRealizedGainLoss",
    "MarketableSecuritiesRealizedGainLossExcludingOtherThanTemporaryImpairments",
    "DebtSecuritiesRealizedGainLoss",
    "DebtAndEquitySecuritiesGainLoss",
]
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
    # The pieces between operating income and net income, read by _adjust(). The first pretax tag already
    # includes income from equity-method investments, the second leaves it out.
    "pretax": PRETAX,
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "discontinued": ["IncomeLossFromDiscontinuedOperationsNetOfTax", "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"],
    "equity_method": ["IncomeLossFromEquityMethodInvestments"],
    "minority": ["NetIncomeLossAttributableToNoncontrollingInterest"],
    # One-time items, read by _adjust(). The goodwill write-down is kept apart because it is usually not tax
    # deductible. The last four say how much tax a goodwill write-down saved and what drives a tax bill.
    "gw_impairment": ["GoodwillImpairmentLoss"],
    "impairment": IMPAIRMENT,
    "sale": SALE,
    "property_sale": PROPERTY_SALE,
    "securities": SECURITIES,
    "debt_extinguishment": ["GainsLossesOnExtinguishmentOfDebt"],
    "gw_impairment_net": ["GoodwillImpairmentLossNetOfTax"],
    "impairment_nondeductible": ["IncomeTaxReconciliationNondeductibleExpenseImpairmentLosses"],
    "valuation_allowance": ["IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance"],
    "tax_credits": TAX_CREDITS,
}
# Line items that only come from a company's financial statements, so the filings that supplied them are annual
# reports (or their amendments and recasts), whose one-time item and revenue figures _check_items() trusts without a
# lookup. Net income and revenue are left out: proxies tag them too (Quinstreet's 2026 proxy tagged a profit measure
# for each of 2022 to 2026 as Revenues, $112.5M for 2026, against $1.29B of sales in its 10-K).
STATEMENT_TAGS = {t for k in ("operating_income", "ocf", "pretax", "income_tax") for t in DURATION[k]}
REVENUE_TAGS = set(DURATION["revenue"])
# Without these for the two newest years, every company's latest figures would silently change.
CRITICAL = {"revenue", "net_income", "ocf", "capex", "pretax", "income_tax"}
# One-time item frames. If any of them failed for any year, _items() leaves all of them out for every year: a year
# that merely read as zero would make an item look one-time in the others, and reading only some kinds would take
# out gains without adding back charges or the reverse.
ITEMS = {"gw_impairment", "impairment", "sale", "property_sale", "securities", "debt_extinguishment",
         "gw_impairment_net", "impairment_nondeductible", "valuation_allowance", "tax_credits"}
ITEM_TAGS = {t for k in ITEMS for t in DURATION[k]}

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
    # Only for matching similar companies (sales per dollar of assets), so a failed request leaves it blank rather than
    # costing the quarter's whole balance sheet.
    "total_assets": ["Assets"],
}
OPTIONAL_INSTANT = {"shares_out", "total_assets"}
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
    bad_quarters = {p for m, p in failed if p in quarters and m not in OPTIONAL_INSTANT}
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
        if frame is None or (metric, period) in failed or (section == "instant" and metric not in OPTIONAL_INSTANT and period in bad_quarters):
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

    _check_items(annual)  # first, since the net income check compares against the revenue's filing
    reported = _check_net_income(annual)
    unknown = {m for m, _ in failed if m in ITEMS}
    if unknown:
        print("  One-time item frames incomplete, so write-downs, sales, securities and debt payoffs are not "
              "adjusted in this run", flush=True)
    companies = {}
    for cik, c in info.items():
        facts = annual.get(cik, {})
        has_capex = any(_capex(fy) is not None for fy in facts.values())
        c["annual"] = {y: _annual(facts.get(y, {}), has_capex, ("capex", y) in failed, reported.get((cik, y), False), unknown)
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


def _annual(facts, has_capex, capex_failed, report, unknown=frozenset()):
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
        "pretax": _val(_first(facts, PRETAX)),
        "pretax_has_equity_method": PRETAX[0] in facts,
        **{k: _val(_first(facts, DURATION[k])) for k in ("income_tax", "discontinued", "equity_method", "minority")},
        # The owners' own share, without what goes to minority holders (i3 Verticals' 2025: $14.2M of $20.9M).
        "discontinued_parent": _val(facts.get(DURATION["discontinued"][1])),
        **_items(facts, unknown),
    }


def _items(facts, unknown):
    """One year's possible one-time items. A tag the company did not use reads as zero, and None means unknown."""
    gw, other = _impairments(facts)
    out = {
        "gw_impairment": gw,
        "impairment": other,
        "sale": _sale(facts),
        "property_sale": _group(facts, PROPERTY_SALE),
        "securities": _group(facts, SECURITIES),
        "debt_extinguishment": _val(facts.get("GainsLossesOnExtinguishmentOfDebt")) or 0,
        "gw_tax_rate": _goodwill_tax_rate(facts, gw),
        "valuation_allowance": _val(facts.get("IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance")),
        "tax_credits": max(abs(_val(facts.get(TAX_CREDITS[0])) or 0), sum(abs(_val(facts.get(t)) or 0) for t in TAX_CREDITS[1:])),
    }
    if unknown & ITEMS:
        # Taking out gains while charges go unread (or the reverse) would tilt every company's earnings one way, so
        # a run missing any of these frames reads none of them. Discontinued operations, untagged gains and the tax
        # check still apply.
        out.update(gw_impairment=None, impairment=None, sale=None, property_sale=None, securities=None,
                   debt_extinguishment=None, gw_tax_rate=None, valuation_allowance=None, tax_credits=None)
    return out


def _impairments(facts):
    """(goodwill, everything else) written down in one year. Charges are positive. Every reading of the other
    write-downs is a floor on the true total, since pieces may overlap, so the largest counts.

    The same charge is often tagged more than once. The broad AssetImpairmentCharges at least as large as the goodwill
    write-down is taken to include it (Darling's 2025: $57.8M, $19.0M of it goodwill). Any other reading within 1% of
    the goodwill write-down is that write-down tagged again (Chord Energy's 2025: $539.3M as both goodwill and oil and
    gas properties; Integra's 2025: $511.4M as both goodwill and finite-lived intangibles), and two halves of a pair
    that match are one charge tagged twice (Americold's 2025: $47.1M both as assets held for use and as assets to be
    disposed of). OtherAssetImpairmentCharges is sometimes the total and sometimes only the rest (New Fortress Energy's
    2025: $860.9M besides $598.1M of goodwill), so it counts as the total when it matches or exceeds a total that
    includes goodwill (Jack in the Box's 2025: $214.0M, against $209.6M of goodwill and intangibles and $4.4M of
    other assets) or exceeds the goodwill write-down by less than a tenth (AGCO's 2024: $369.5M of impairment
    charges, $354.1M of them goodwill).

    A write-down of goodwill or of intangible assets in use is never reversed under US accounting rules, so a negative
    figure there is a slipped sign (Cummins' 2025 goodwill: -$210M). Any other negative figure is not a write-down: the
    reversal of a loss on assets held for sale (Newmont's 2025: $1.07B), a line that nets write-downs against gains on
    selling businesses (Community Health Systems' 2025: a $406M net gain) or a misplaced running total (GEE Group's
    goodwill write-downs to date). None of those is a charge to add back, so they read as zero."""
    g = lambda t: 0 if t not in facts else abs(facts[t]["val"]) if t in NO_REVERSAL else max(facts[t]["val"], 0)
    gw = g("GoodwillImpairmentLoss")
    broad, gw_and_intangibles = g("AssetImpairmentCharges"), g("GoodwillAndIntangibleAssetImpairment")
    totals = [t for t in (broad, gw_and_intangibles) if gw and t >= gw]

    def net(x, total=False):
        if gw and (total or abs(x - gw) <= 0.01 * gw):
            return max(0.0, x - gw)
        return x

    def pair(a, b):
        a, b = net(g(a)), net(g(b))
        return max(a, b) if abs(a - b) <= 0.005 * max(a, b) else a + b

    other = g("OtherAssetImpairmentCharges")
    readings = [
        net(g("ImpairmentOfIntangibleAssetsExcludingGoodwill")),
        pair("ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill", "ImpairmentOfIntangibleAssetsFinitelived"),
        pair("ImpairmentOfLongLivedAssetsHeldForUse", "ImpairmentOfLongLivedAssetsToBeDisposedOf"),
        *(net(g(t)) for t in IMPAIRMENT[7:] if t != "OtherAssetImpairmentCharges"),
        net(other, total=other >= gw and (other <= 1.1 * gw or any(other >= 0.995 * t for t in totals))),
        max(0.0, gw_and_intangibles - gw),
        broad - gw if broad >= gw else broad,
    ]
    return gw, max(readings)


def _group(facts, tags):
    """The largest gain (positive) or loss among `tags`, since a broad tag often includes a narrower one, or None when
    they point opposite ways: then one was tagged the wrong way round (U-Haul's 2025: -$104M and +$104M, Citigroup's
    securities gains) or they measure different things (Good Times' 2025: a $115K loss on disposals and a $469K
    gain), and neither can be trusted."""
    vals = [facts[t]["val"] for t in tags if facts.get(t, {}).get("val")]
    if not vals:
        return 0
    return None if min(vals) < 0 < max(vals) else max(vals, key=abs)


def _sale(facts):
    """Gain (positive) or loss on selling a business or assets, or None when the tags contradict each other. A
    business sale stands on its own when the asset tags point the other way, which is usually an ordinary disposal
    loss or a write-down (Ameresco's 2024: a $38.0M gain on selling a business, and $12.8M of write-downs and
    disposal losses)."""
    business, assets = _group(facts, SALE[:4]), _group(facts, SALE[4:])
    if not business:
        return assets if business == 0 else None
    return assets if assets and assets * business > 0 and abs(assets) > abs(business) else business


def _goodwill_tax_rate(facts, gw):
    """Tax saved per dollar of goodwill written down, where the filing says, else None. Goodwill write-downs are
    usually not deductible: Hewlett Packard Enterprise's 2025 tax reconciliation shows $330M of tax it could not
    save on its $1.58B write-down, which is 21% of the whole amount."""
    if not gw:
        return None
    net = _val(facts.get("GoodwillImpairmentLossNetOfTax"))
    if net is not None and 0.6 * gw <= abs(net) <= gw:  # a tax saving above 40% would be a misread figure
        return (gw - abs(net)) / gw
    lost = _val(facts.get("IncomeTaxReconciliationNondeductibleExpenseImpairmentLosses"))
    if lost is not None and lost > 0:
        return max(0.0, NORMAL_TAX - lost / gw)
    return None


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
    and filed after the annual report, so the frame shows it. It is often unscaled (thousands read as dollars) or
    shows a loss as a positive number, and it can predate a restatement in the annual report (Twin Disc's 2026
    proxy: $34.5M, its 10-K: $27.1M).

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
    # A lost minus sign shows against another filing's income before tax less the tax, or its operating income
    # when that is all it tags.
    pretax, op = _first(facts, PRETAX), facts.get("OperatingIncomeLoss")
    own = (pretax, pretax["val"] - (_val(facts.get("IncomeTaxExpenseBenefit")) or 0)) if pretax else (op, _val(op))
    flipped = own[0] is not None and own[0]["accn"] != ni["accn"] and x * own[1] < 0
    # Matching only one of them is not enough: a proxy often shows profit including minority holders' share,
    # which is ProfitLoss (Tenet's 2022: $1.0B, against the owners' $411M).
    if alts and all(abs(x - a) <= 0.02 * abs(a) for a in alts) or (not alts and sane(x) and not flipped):
        return x
    # Preferred dividends and minority interests also set those tags apart, and income from sold businesses can
    # turn a pretax loss into a profit, so a gap short of a scale or sign error is checked against the annual
    # report, and stands when that lookup fails.
    same_scale = any(1 / 50 < x / a < 50 for a in alts) or (not alts and sane(x))
    if report is None:
        return NEEDS_REPORT
    if report is not False:
        return report
    return x if same_scale else next((a for a in alts if sane(a)), None)


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


ITEM_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "item_facts.json")
ITEM_MINUTES = 20  # time budget for looking up one-time item and revenue figures; the rest wait for the next run


def _check_items(annual, minutes=ITEM_MINUTES):
    """Replaces one-time item and revenue figures that came from a filing other than an annual report with the annual
    report's own figure, or drops them when no annual report has one. A frame shows the latest filing that tagged a
    year, which can be a proxy or a quarterly report with the year mislabelled or restated (Mobileye's 2026 proxy: its
    $2.7B 2024 goodwill write-down tagged as 2025's, when its annual report shows none in 2025; Xponential Fitness's
    2026 10-Q: $55.4M of 2025 goodwill write-downs, against $7.5M in its 10-K; Quinstreet's proxy, above). A revenue
    figure is only dropped when another revenue tag remains for that year, so a company never loses its sales figure.

    A figure counts as the annual report's when it came from a filing that supplied the company's pretax income, tax,
    operating income or operating cash flow for that year or the next two (whose annual reports repeat it). The
    others are looked up in the company's 10-K filings, one request per company and tag, and the answers are kept in
    .cache/item_facts.json, since a filing never changes. Figures still unchecked when the SEC throttles or the time
    budget runs out stay as the frame has them, and the next run checks them."""
    flagged = {}  # (cik, tag) -> [(year, fact)]
    for cik, by_year in annual.items():
        statements = {y: {d["accn"] for t, d in facts.items() if t in STATEMENT_TAGS} for y, facts in by_year.items()}
        for y, facts in by_year.items():
            ok = set().union(*(statements.get(y + k, set()) for k in range(3)))
            for t, d in facts.items():
                if (t in ITEM_TAGS or t in REVENUE_TAGS) and d.get("val") and d.get("accn") not in ok:
                    flagged.setdefault((cik, t), []).append((y, d))
    if not flagged:
        return
    try:
        with open(ITEM_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    key = lambda cik, t, d: f"{cik}|{t}|{d['accn']}|{d.get('start')}|{d['end']}"
    todo = [ct for ct, facts in flagged.items() if any(key(*ct, d) not in cache for _, d in facts)]
    todo.sort(key=lambda ct: -max(y for y, _ in flagged[ct]))  # the newest years matter most
    stop = time.monotonic() + minutes * 60
    blocked = threading.Event()

    def look(ct):
        if blocked.is_set() or time.monotonic() > stop:
            return None
        try:
            return _report_items(*ct)
        except Throttled:
            blocked.set()
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            pass
        return None

    looked = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (cik, t), found in zip(todo, pool.map(look, todo)):
            if found is not None:
                looked += 1
                for _, d in flagged[(cik, t)]:
                    cache[key(cik, t, d)] = found.get((d.get("start"), d["end"]))
    if looked:
        try:
            os.makedirs(os.path.dirname(ITEM_CACHE), exist_ok=True)
            tmp = ITEM_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, ITEM_CACHE)
        except OSError:
            pass
    fixed = dropped = unchecked = kept = 0
    for (cik, t), facts in flagged.items():
        for y, d in facts:
            k = key(cik, t, d)
            if k not in cache:
                unchecked += 1
            elif cache[k] is None:
                year = annual[cik][y]
                other = [r for r in REVENUE_TAGS if r != t and r in year and year[r].get("val")]
                if t in REVENUE_TAGS and not any(cache.get(key(cik, r, year[r]), 0) is not None for r in other):
                    kept += 1  # no other sales figure to fall back on
                    continue
                del year[t]
                dropped += 1
            else:
                # The annual report's own figure and filing, so _net_income() compares a net income figure with that
                # filing's (JAKKS Pacific's 2025 proxy: net income 1,000 times too large, next to a 10-Q's quarter
                # tagged as the year's sales). Cache entries from before the filing was kept are bare values.
                val, accn = cache[k] if isinstance(cache[k], list) else (cache[k], d["accn"])
                if val != d["val"] or accn != d["accn"]:
                    annual[cik][y][t] = dict(d, val=val, accn=accn)
                    fixed += val != d["val"]
    print(f"  {sum(len(f) for f in flagged.values())} one-time item and revenue figures came from a filing other than an "
          f"annual report: {fixed} replaced with the 10-K's figure, {dropped} dropped as absent from the 10-K ({kept} "
          f"revenue figures kept for lack of another), {unchecked} unchecked" + (" (the SEC throttled)" if blocked.is_set() else "")
          + f"; {looked} lookups this run", flush=True)


def _report_items(cik, tag):
    """{(start, end): [value, accession]} for one line item from the company's annual reports (10-K, or 20-F and 40-F
    for foreign companies), the latest filing winning."""
    try:
        facts = sec_json(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json")["units"].get("USD", [])
    except NotFound:
        return {}
    out = {}
    for f in sorted(facts, key=lambda f: f.get("filed", "")):
        if f.get("form", "").startswith(("10-K", "20-F", "40-F")) and f.get("start"):
            out[(f["start"], f["end"])] = [f["val"], f.get("accn")]
    return out


def _balance_sheet(by_quarter, quarters):
    """Every balance sheet figure from the same quarter, so one stale line can't mix with newer ones."""
    q = next((q for q in quarters if any(t in by_quarter.get(q, {}) for t in ANCHORS)), None)
    if q is None:
        return {}
    facts = by_quarter[q]
    total, lt, reported = _debt({t: d["val"] for t, d in facts.items()})
    out = {m: _val(_first(facts, INSTANT[m])) for m in ("equity", "cash", "st_investments", "current_assets", "current_liabilities")}
    out.update(as_of=_first(facts, ANCHORS).get("end"), total_debt=total, lt_debt=lt, debt_reported=reported,
               total_assets=_val(facts.get("Assets")))
    # The most cash held at any recent quarter end, since interest earned last year came from the cash held then.
    out["peak_cash"] = max((_val(_first(f, INSTANT["cash"])) or 0) + (_val(_first(f, INSTANT["st_investments"])) or 0)
                           for f in by_quarter.values())
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


NORMAL_TAX = 0.21  # the US federal rate, used when a company's own tax rate is not steady enough to go by
CASH_YIELD = 0.05  # interest a company can earn on its cash, which counts as ordinary income rather than a one-time gain
# How large a one-time item must be to count, against the company's earnings scale: reported net income, or 2% of
# revenue (SCALE_SALES) when that is larger, so a year near breakeven doesn't make every small item count. Each item
# must move net income by ITEM_MIN of that, all of them together by TOTAL_MIN, and an unusual tax bill must also be
# at least TAX_MIN of pretax income away from normal.
SCALE_SALES = 0.02
ITEM_MIN = 0.02
TOTAL_MIN = 0.05
TAX_MIN = 0.10
# Room above a company's best operating margin in its other years that a written-down year may reach once the
# write-downs are added back. More means the write-down was not in operating income (usually a business later
# reported as discontinued) or the figure is misread.
MARGIN_ROOM = 0.10

PROPERTY = ("gw_impairment", "impairment", "sale")  # items a REIT leaves out whole

# Kinds of one-time item in derive()'s "one_time" list. Each is {kind, amount, effect, ...}: `amount` is the item's
# size as reported (before tax, except for discontinued operations and tax items), and `effect` is the change made to
# net income (negative takes a gain out, positive adds a charge back). `rate` is the tax rate applied to the item,
# `reported` the whole amount as reported when only part of it counts as one-time, and `share` the owners' share of
# the item when minority holders own part of the business (tax items and discontinued operations never carry one: a
# tax item is the owners' own, and discontinued operations are the owners' share already).
KINDS = {
    "goodwill_impairment": "goodwill written down (added back before tax unless the filing shows a tax saving)",
    "impairment": "other assets written down: intangibles, property, leases, real estate",
    "sale_gain": "gain on selling a business or assets",
    "sale_loss": "loss on selling a business or assets",
    "securities_gain": "gain on investment securities, sold or revalued (Alphabet's 2025: $24.6B)",
    "securities_loss": "loss on investment securities, such as a bank selling bonds to reinvest at higher rates",
    "debt_gain": "gain on paying off debt for less than its book value",
    "debt_loss": "loss on paying off debt early",
    "gain": "other income below operating income beyond interest on cash and what came in the two prior years",
    "discontinued": "profit from businesses sold or shut down",
    "discontinued_loss": "loss from businesses sold or shut down",
    "tax_benefit": "tax bill below normal: `amount` is how far below, `reported_tax` the bill as reported, `normal_tax` "
                   "the normal bill on `adj_pretax` (pretax income without the other items), and `cause` is "
                   "valuation_allowance when a tagged valuation allowance release explains most of it",
    "tax_charge": "tax bill above normal, such as a valuation allowance being set up or tax assets remeasured (same "
                  "fields as tax_benefit)",
}


def _non_operating(s):
    """Income between operating income and pretax income, other than from equity-method investments and the tagged
    gains that _adjust() takes out on their own. Only gains, so a tagged loss inside operating income cannot make
    the rest look like a gain, and not a sale gain that discontinued operations already hold (i3 Verticals' 2025)."""
    pretax, op = s.get("pretax"), s.get("operating_income")
    if pretax is None or op is None:
        return None
    sale = max(s.get("sale") or 0, 0) - max(s.get("discontinued") or 0, 0) / (1 - NORMAL_TAX)
    tagged = max(sale, 0) + max(s.get("securities") or 0, 0) + max(s.get("debt_extinguishment") or 0, 0)
    return pretax - op - ((s.get("equity_method") or 0) if s.get("pretax_has_equity_method") else 0) - tagged


def _reit_year(s):
    """A REIT's year as _adjust() reads it. Its gains on selling real estate count with its other sales, the larger
    reading winning, since the tags overlap (Realty Income's 2025: $177.6M, only on its own line). A REIT that tags
    no income tax or pretax income (Kite Realty's 2025) pays none that matters, so its pretax income is what its net
    income adds up to, and the income statement is taken to reconcile."""
    s = dict(s)
    sale, prop = s.get("sale"), s.get("property_sale")
    if prop and sale is not None:
        s["sale"] = prop if sale * prop < 0 or abs(prop) > abs(sale) else sale
    if s.get("net_income") is not None and (s.get("pretax") is None or s.get("income_tax") is None):
        tax = s.get("income_tax") or 0
        if s.get("pretax") is None:
            eq = s.get("equity_method") or 0
            s.update(pretax=s["net_income"] + tax - eq - (s.get("discontinued") or 0) + (s.get("minority") or 0),
                     pretax_has_equity_method=False)
        s["income_tax"] = tax
    return s


def _own_discontinued(s):
    """The owners' share of discontinued operations: the tagged figure when it is plausible, else the whole. Minority
    holders can take at most their own part (DNA X's 2024: $58.3M tagged as the owners' share of a $30.8M loss, with
    no minority holders)."""
    disc, own, mi = s.get("discontinued") or 0, s.get("discontinued_parent"), s.get("minority") or 0
    if own is None or own * disc < 0 or abs(own) > abs(disc) + abs(mi) + 1e5:
        return disc
    return own


def _minor(mi, s):
    """Whether a year's minority result is too small next to the business to be a share of it."""
    return abs(mi) < 0.1 * max(abs(s.get("net_income") or 0), SCALE_SALES * (s.get("revenue") or 0))


def _owners_share(s):
    """The owners' share of a year's continuing results, where minority holders own part of the business (NET Power's
    2025: 35%, the rest belonging to the holders of its operating company's units), or None when the owners and the
    minority holders went opposite ways. Above 1 when the minority holders lost money while the owners made it."""
    mi, ni = s.get("minority") or 0, s.get("net_income")
    if ni is None or _minor(mi, s):
        # A minority stake this small next to the business is a slice of a few ventures, not a share of the whole,
        # and dividing by a year's net income near breakeven would make it look large (Bloomin' Brands' 2025: $5.0M
        # to minority partners, against its $8.2M).
        return 1.0
    mine, whole = ni - _own_discontinued(s), ni + mi - (s.get("discontinued") or 0)
    return mine / whole if mine * whole > 0 else None


def _minority_whole(series, years):
    """Whether minority holders own a share of the whole business, as in an Up-C, rather than separate ventures. A
    share of the whole moves with the owners' result (Bumble's minority holders took 30% of each year's loss in 2022
    to 2025), while a stake in separate ventures brings in much the same every year whatever the owners make
    (Alexandria Real Estate's $149M to $213M in 2022 to 2025, against owners' results from a $1.4B loss to a $522M
    profit). With three years or more the likelier of the two counts; with fewer, only a minority result at least
    half the owners' own, the same way, is taken as a share of the whole (NET Power, public since 2023)."""
    pts = []
    for x in years:
        s = series[x]
        mi, ni = s.get("minority") or 0, s.get("net_income")
        if ni is not None and not _minor(mi, s):
            pts.append((ni - _own_discontinued(s), mi))
    if len(pts) >= 3:
        k = statistics.median(mi / mine for mine, mi in pts if mine)
        steady = statistics.median(mi for _, mi in pts)
        return sum(abs(mi - k * mine) for mine, mi in pts) < sum(abs(mi - steady) for _, mi in pts)
    return bool(pts) and all(mine * mi > 0 and abs(mi) >= 0.5 * abs(mine) for mine, mi in pts)


def _reconciles(s):
    """Whether the income statement lines add up to reported net income. Otherwise one of them is misread and any
    adjustment built on them would be too."""
    ni, pretax, tax = s.get("net_income"), s.get("pretax"), s.get("income_tax")
    if ni is None or pretax is None or tax is None:
        return False
    eq = 0 if s.get("pretax_has_equity_method") else s.get("equity_method") or 0
    return abs(pretax - tax + eq + (s.get("discontinued") or 0) - (s.get("minority") or 0) - ni) \
        <= 0.1 * max(abs(ni), abs(pretax)) + 1e5


def _scaled(values, j, series):
    """The other years' amounts, each scaled to year j's sales (within half to double)."""
    rev = series[j].get("revenue")
    out = []
    for x, v in values.items():
        if x == j or v is None:
            continue
        rx = series[x].get("revenue")
        out.append(v * (min(2.0, max(0.5, rev / rx)) if rev and rx and rev > 0 and rx > 0 else 1.0))
    return out


def _one_off(amount, others, level=False):
    """The one-time part of a gain (positive) or charge (negative), given the same item in the company's other
    years. Something that comes in most years is part of the business, so the usual size is what this year and
    enough other years to make a majority reach in the same direction: the second largest of three other years, the
    largest of one or two.

    An item is wholly one-time at four times its usual size or more (Hewlett Packard Enterprise's $1.58B goodwill
    write-down against $0.9B, nothing and $0.1B in the three years before) and part of the business at twice or less
    (Cracker Barrel's $19.8M of store write-downs against $17.4M and $11.7M), with the share in between sliding from
    all to none. A tax bill is a `level`: whatever lies beyond the usual gap from normal counts, fading in as the
    usual gap grows from a quarter to half of this year's.
    """
    if not amount:
        return 0.0
    same = sorted((abs(v) for v in others if v * amount > 0), reverse=True)
    k = (len(others) + 1) // 2
    usual = same[k - 1] if k and len(same) >= k else 0.0
    ratio = usual / abs(amount)
    if level:
        return math.copysign(max(0.0, abs(amount) - usual * min(1.0, max(0.0, (ratio - 0.25) / 0.25))), amount)
    return amount * min(1.0, max(0.0, (0.5 - ratio) / 0.25))


def _tax_one_off(gap, others, opposite=True):
    """The one-time part of a tax gap (positive: tax above normal), given the gaps the company's other years show.
    What most of the window reaches in the same direction is usual (this year counting toward the majority, as in
    _one_off) and never counts, fading in as it grows from a quarter to half of this year's gap, as in _one_off. A gap
    every other year shows the other way is usual too, wholly, so this year's is measured from the smallest of them
    (Qualcomm's 2025 tax charge against a rate below 21% in each of 2022 to 2024, from its low-taxed foreign-derived
    income)."""
    if not gap:
        return 0.0
    same = sorted((abs(v) for v in others if v * gap > 0), reverse=True)
    k = (len(others) + 1) // 2
    if k and len(same) >= k:
        return _one_off(gap, others, level=True)
    against = sorted((abs(v) for v in others if v * gap < 0), reverse=True)
    if opposite and len(against) == len(others) >= 2:
        return gap + math.copysign(against[-1], gap)
    return gap


def _growing(values, j, series):
    """Whether an item came the same way in each of the two years before, last year at least half this year's size,
    which makes a growing gain or loss on sales part of the business even while the oldest year was much smaller
    (Portland General Electric's losses on selling other assets: $24M, $112M and $179M in 2023 to 2025, which are its
    sales of tax credits). Not write-downs: two large ones in a row mark a business in decline, and each is one-time
    (Kraft Heinz's brand write-downs: $152M, $2.0B and $2.6B in 2023 to 2025)."""
    now, last, before = values.get(j), values.get(j - 1), values.get(j - 2)
    if not now or not last or not before or now * last <= 0 or now * before <= 0:
        return False
    return abs(_scaled({j - 1: last}, j, series)[0]) >= 0.5 * abs(now)


def _normal_rate(series, j, years, tax_rate):
    """The tax rate a year's profit is normally taxed at: 0 for a REIT, else the company's own rate over its other
    profitable years when that is steady (within 8 points) and between 10% and 35%, else 21%. Lower steady rates
    usually come from losses carried forward, which run out, so they don't count; recurring credits are handled
    by _adjust() instead."""
    if tax_rate is None:
        return NORMAL_TAX
    if tax_rate != NORMAL_TAX:
        return tax_rate
    rates = [series[x]["income_tax"] / series[x]["pretax"] for x in years
             if x != j and (series[x].get("pretax") or 0) > 0 and series[x].get("income_tax") is not None]
    if len(rates) >= 2 and max(rates) - min(rates) <= 0.08 and 0.10 <= statistics.median(rates) <= 0.35:
        return statistics.median(rates)
    return NORMAL_TAX


def _tax_gap(pretax, tax, rate):
    """How far a tax bill is from normal: positive when above it. On a pretax loss anything from no tax at all to a
    full credit at the normal rate is normal (Ford's 31% credit on its 2025 loss), since a company with past losses
    may not record one."""
    if pretax > 0:
        return tax - rate * pretax
    return tax - min(max(tax, rate * pretax), 0.0)


def _adjust(series, years, cash, tax_rate):
    """{year index: (net income without one-time items, [items])} for each of `years` (indexes into `series`, the
    window the company is measured over).

    Adds back one-time charges and takes out one-time gains: write-downs, gains and losses on selling a business,
    assets or investment securities and on paying off debt (all from their XBRL tags), untagged income below
    operating income beyond interest on cash, discontinued operations, and an unusual tax bill. An item is one-time
    only when it is well above what the company reports of the same kind in most of its other years (_one_off), or
    growing year after year (_growing), so a restaurant's yearly store write-downs or an aircraft lessor's gains on
    selling planes stay in; a REIT's property gains and write-downs always come out, as funds from operations does.
    Add-backs and take-outs are after tax at the company's normal rate (_normal_rate), or at none when it is recording
    no tax on its results, and a goodwill write-down is added back whole unless the filing shows a tax saving. Where
    minority holders own part of the business, only the owners' share of each item moves net income (_owners_share).

    The tax bill on what is left is then compared with normal (_tax_gap), and only the one-time part of any gap comes
    out (_tax_one_off): the part beyond the company's usual gap both as a share of pretax income and in dollars (so
    structurally low rates and recurring credits stand), plus a usual gap every other year shows the other way, less
    the tax credits it reports every year, and for a tax bill above normal also the part beyond the year before. A
    valuation allowance that grows most years is usual tax, and a normal tax on a profit is no lower than the company
    paid in its other profitable years. A `tax_rate` of None (a utility) makes any tax benefit on a profit normal.
    Nothing changes unless the income statement lines add up to reported net income.
    """
    reit = tax_rate == 0
    if reit:
        series = [_reit_year(s) for s in series]
    ok = [j for j in years if _reconciles(series[j])]
    has = [x for x in years if series[x].get("net_income") is not None]
    vals = {}
    for k, sign in (("gw_impairment", -1), ("impairment", -1), ("sale", 1), ("securities", 1), ("debt_extinguishment", 1)):
        vals[k] = {x: None if series[x].get(k) is None else sign * series[x][k] for x in has}
    for x in has:
        # Goodwill written down this year and last, tagged together as another write-down (Brunswick's 2025: $385.8M
        # of indefinite-lived intangibles, which is its $305.8M and $80.0M goodwill write-downs of 2025 and 2024).
        gw, before = series[x].get("gw_impairment") or 0, (series[x - 1].get("gw_impairment") or 0) if x else 0
        if gw and before and abs(-(vals["impairment"][x] or 0) - gw - before) <= 0.01 * (gw + before):
            vals["impairment"][x] = 0.0
    # The owners' share of each year's results, where minority holders own a share of the whole business: an item
    # moves net income (the owners' share) only by that much. A year far from the company's usual share, or one that
    # can't be measured, takes the usual share. Where the minority holders own separate ventures instead, the items
    # are unlikely to be in them, so they count whole (_minority_whole).
    shares = {x: _owners_share(series[x]) for x in has}
    usual_share = statistics.median([v for v in shares.values() if v is not None and v != 1.0] or [1.0])
    whole = _minority_whole(series, has)

    def owners(x):
        if not whole or usual_share > 0.9:
            return 1.0
        v = shares.get(x)
        return min(1.0, v if v is not None and abs(v - usual_share) <= 0.25 else usual_share)

    rates, pre, owned = {}, {}, {}
    for j in ok:
        s, r = series[j], _normal_rate(series, j, years, tax_rate)
        rates[j] = r
        own = owned[j] = owners(j)
        # The tax an item actually carried. A company that recorded no tax benefit on a loss, or paid next to no tax
        # on a profit while releasing its valuation allowance, has losses it cannot yet use, so a write-down saved it
        # no tax and a gain cost it none (Comtech's 2025 write-downs: a $0.1M benefit on a $155M loss). A loss that
        # only a goodwill write-down made, which is not deductible anyway, doesn't count.
        va = s.get("valuation_allowance") or 0
        if s["pretax"] + (s.get("gw_impairment") or 0) <= 0 and s["income_tax"] >= 0.1 * r * s["pretax"] or (
                s["pretax"] > 0 and abs(s["income_tax"]) <= 0.05 * s["pretax"] and va <= -0.5 * NORMAL_TAX * s["pretax"]):
            m = 0.0
        else:
            m = r
        ni, rev = s["net_income"], s.get("revenue") or 0
        scale = max(abs(ni), SCALE_SALES * rev)
        big = lambda effect: effect != 0 and abs(effect) >= ITEM_MIN * scale
        mine = lambda item: dict(item, share=round(own, 3)) if own < 0.995 else item
        # A REIT's gains on selling property and its write-downs are left out whole, however often they come, as
        # the industry's funds from operations measure does (Realty Income writes property down every year).
        found = {k: v[j] if reit and k in PROPERTY
                 else 0.0 if k not in ("gw_impairment", "impairment") and _growing(v, j, series)
                 else _one_off(v[j], _scaled(v, j, series))
                 for k, v in vals.items() if v.get(j) is not None}

        # A write-down or sale inside discontinued operations is also in the cash flow statement tags, and the
        # discontinued result already comes out whole (Fidelity National Information Services' 2022: a $17.6B
        # goodwill write-down, all in its $17.3B discontinued loss; Mammoth Energy's 2025: $63M of write-downs,
        # about half of them in businesses it sold at a gain). So write-downs and sales shrink by the discontinued
        # result, the charges and the gains each by the whole of it. The discontinued result is after tax, so it is
        # grossed up at the normal rate; a remainder under a tenth of the item is that estimate's error, not a part
        # of it outside discontinued operations (Greif's 2025: a $1.09B gain on selling its containerboard business,
        # all in its $825M discontinued result).
        disc = s.get("discontinued") or 0
        for sign in (-1, 1) if big(disc) else ():
            room = abs(disc) / (1 - NORMAL_TAX)
            for k in ("gw_impairment", "impairment", "sale"):
                if room > 0 and (found.get(k) or 0) * sign > 0:
                    cut = min(abs(found[k]), room)
                    found[k] -= sign * cut
                    room -= cut
                    if abs(found[k]) < 0.1 * abs(vals[k][j]):
                        found[k] = 0.0
        # Write-downs sit in operating income, so adding them back must leave a plausible operating margin.
        op = s.get("operating_income")
        margins = [series[x]["operating_income"] / series[x]["revenue"] for x in years
                   if x != j and series[x].get("operating_income") is not None and (series[x].get("revenue") or 0) > 0]
        down = -(found.get("gw_impairment", 0) + found.get("impairment", 0))
        if down > 0 and op is not None and rev > 0 and len(margins) >= 2:
            fit = max(0.0, (max(margins) + MARGIN_ROOM) * rev - op)
            for k in ("impairment", "gw_impairment"):
                cut = min(-found.get(k, 0), down - fit)
                if cut > 0:
                    found[k] += cut
                    down -= cut

        items, moved, taxes = [], 0.0, 0.0
        for k, kind in (("gw_impairment", "goodwill_impairment"), ("impairment", "impairment"), ("sale", "sale"),
                        ("securities", "securities"), ("debt_extinguishment", "debt")):
            x = found.get(k) or 0
            rate = min(s.get("gw_tax_rate") or 0, m) if k == "gw_impairment" else m
            if not big(x * (1 - rate) * own):
                continue
            if kind in ("sale", "securities", "debt"):
                kind += "_gain" if x > 0 else "_loss"
            items.append(mine({"kind": kind, "amount": abs(x), "effect": -x * (1 - rate) * own, "rate": round(rate, 4)}))
            if abs(x) < 0.995 * abs(vals[k][j]):
                items[-1]["reported"] = abs(vals[k][j])  # only part of it counts as one-time
            moved += x
            taxes += x * rate * own
        if op is not None and s["pretax"] > 0:
            # Income that came in every recent year is ordinary, such as Robert Half's deferred pay trusts (offset by
            # pay expense inside operating income) or interest on long-term investments.
            prior = [_non_operating(p) for p in series[max(0, j - 2): j]]
            usual = min(prior) if len(prior) == 2 and None not in prior else 0
            gain = _non_operating(s) - max(CASH_YIELD * max(cash or 0, 0), usual)
            if gain >= 0.25 * s["pretax"] and big(gain * (1 - m) * own):
                items.append(mine({"kind": "gain", "amount": gain, "effect": -gain * (1 - m) * own, "rate": round(m, 4)}))
                moved += gain
                taxes += gain * m * own
        d = _own_discontinued(s)  # net income is the owners' share, so only their share comes out of it
        if big(d):
            items.append({"kind": "discontinued" if d > 0 else "discontinued_loss", "amount": abs(d), "effect": -d})
        pre[j] = (s["pretax"] - moved, s["income_tax"] - taxes, items)

    gaps = {j: _tax_gap(p, t, rates[j]) for j, (p, t, _) in pre.items()}
    # A valuation allowance that grows by a similar amount most years (losses in one country that can't offset profits
    # in another, as at ManpowerGroup and HubSpot) is part of the company's tax, so only growth well beyond the usual
    # counts as one-time. A release always does, since it ends once the old losses are used.
    allowance = {x: series[x].get("valuation_allowance") for x in has}  # None: not reported that year
    va_one = {x: (_one_off(v, _scaled(allowance, x, series)) if v > 0 else v) if v is not None else 0
              for x, v in allowance.items()}
    # What a year's gap says about the company's usual tax: the part a one-time valuation allowance movement explains
    # is not usual, since it comes from losses carried forward (Power Solutions' tax was near zero in 2022 to 2024 as
    # it used old losses, then it released its allowance in 2025; its normal rate is not near zero).
    steady = {}
    for j, g in gaps.items():
        v = va_one.get(j, 0)
        steady[j] = g - (max(v, g) if g < 0 else min(v, g)) if v * g > 0 else g
    # Each year's rate as the company normally pays it, for years whose pretax income was mostly not one-time items,
    # since the tax assumed on those swamps the rest (Uber's 2023: $1.8B of investment gains in $2.3B).
    usual_rate = {x: rates[x] + steady[x] / p for x, (p, _, _) in pre.items()
                  if p > 0 and abs(p - series[x]["pretax"]) <= 0.5 * abs(series[x]["pretax"])}
    # Without the item frames, write-downs stay in, and the tax a non-deductible goodwill write-down didn't save would
    # look like a one-time tax charge, so none is added back.
    degraded = any(series[x].get("impairment") is None for x in has)
    out = {}
    for j in years:
        s = series[j]
        if j not in pre:
            out[j] = (s.get("net_income"), [])
            continue
        pretax, tax, items = pre[j]
        ni, rev, r = s["net_income"], s.get("revenue") or 0, rates[j]
        scale = max(abs(ni), SCALE_SALES * rev)
        gap = gaps[j]
        if tax_rate is None and pretax > 0 > gap:
            gap = 0.0
        # The usual gap two ways, from this year's normal rate: in dollars scaled to sales (a restaurant's tip credits,
        # which stay put when profit falls) and as a share of pretax income (a low foreign rate). Only what is unusual
        # both ways counts. A usual gap the other way only counts against the 21% fallback, since a company's own
        # steady rate is its usual tax already.
        others = {x: steady[x] + (rates[x] - r) * max(pre[x][0], 0) for x in pre if x != j}
        views = [_scaled(others, j, series)]
        if pretax > 0:
            views.append([(usual_rate[x] - r) * pretax if x in usual_rate else 0.0 for x in others])
        own_rate = tax_rate == NORMAL_TAX and r != NORMAL_TAX
        one_off = min((_tax_one_off(gap, v, opposite=not own_rate) for v in views), key=abs)
        va1 = va_one.get(j, 0)
        if one_off > 0 and others.get(j - 1, 0) > 0 and not va1 >= 0.5 * gap:
            # A tax bill above normal two years running is more likely the company's tax structure (foreign profits
            # taxed while home losses are not) than a one-time charge, so the year before alone can make it usual,
            # unless this year's valuation allowance charge explains it (Intel's 2025, after an untagged one in 2024).
            # Not for benefits: Uber released valuation allowances in both 2024 and 2025, each one-time.
            one_off = min(one_off, _tax_one_off(gap, _scaled({j - 1: others[j - 1]}, j, series)))
        credits, usual_credits = s.get("tax_credits") or 0, 0.0
        if one_off < 0 < pretax and credits and not own_rate:
            # Tax credits the company earns every year are normal too (Good Times' restaurant tip credits, $1.2M in
            # 2025 against a tax bill $0.9M below 21%), up to what it reported in its other years, or up to the whole
            # normal tax when it reports them for the first time (as many companies did in 2025, in a new format).
            # Not with its own steady rate, which already has them, nor on a loss, which leaves them nothing to
            # offset (Lyft's 2025: $344M of research credits, first reported the year its valuation allowance came off).
            before = {x: series[x].get("tax_credits") for x in has}
            if any(v for x, v in before.items() if x != j):
                usual_credits = credits - _one_off(credits, _scaled(before, j, series))
            else:
                usual_credits = min(credits, r * pretax)
            one_off = max(one_off, min(0.0, gap + usual_credits))
        if one_off < 0 < pretax and owned[j] < 0.95:
            # Minority holders of a partnership the company controls (an Up-C, or physicians' stakes in hospitals)
            # pay the tax on their share themselves, so a bill below normal by up to the normal rate on their share
            # is normal (TPG's 2025: $67M on $667M of pretax income, a third of it its own).
            one_off = min(0.0, one_off + r * pretax * (1 - owned[j]))
        normal = tax - one_off
        if one_off < 0 < pretax and tax_rate is not None:
            # Normal tax on a profit is no lower than the company paid in its other profitable years, up to the normal
            # rate, however low its usual tax in dollars would put it (Hewlett Packard Enterprise's 2025, whose pretax
            # income halved: at least the 9.2% of 2023), nor below nothing. Unless it paid less than nothing on a
            # profit in some other year, which is how a company whose tax credits are fixed in dollars shows (Cracker
            # Barrel's tip credits: -69% in 2024).
            lows = [usual_rate[x] for x in usual_rate if x != j]
            if min(lows, default=0.0) >= 0:
                normal = max(normal, min(min(lows, default=0.0), r) * pretax)
        one_off = tax - normal
        if degraded and one_off > 0:
            one_off = 0.0
        floor = ITEM_MIN * scale
        if one_off and abs(one_off) >= TAX_MIN * abs(pretax) and abs(one_off) >= floor:
            # A tax bill above normal right where charges were added back after tax most likely means those charges
            # saved no tax (Centene's 2025 write-down of goodwill it tagged as other assets), so that part moves into
            # the charges and the tax item shows only what they don't explain.
            saved = [n for n, i in enumerate(items) if i.get("rate") and i["effect"] > 0 < one_off]
            held = sum(items[n]["amount"] * items[n]["rate"] * items[n].get("share", 1) for n in saved)
            if held > 0:
                part = min(one_off, held) / held
                items = [dict(i, rate=round(i["rate"] * (1 - part), 4),
                              effect=i["amount"] * (1 - i["rate"] * (1 - part)) * i.get("share", 1))
                         if n in saved else i for n, i in enumerate(items)]
                one_off -= min(one_off, held)
            # Tax on a loss year or a year near breakeven (foreign profits taxed while home losses are not, costs that
            # are never deductible), or any tax a REIT pays (on its taxable subsidiaries and abroad), usually recurs,
            # so a tax bill above normal there counts only when a valuation allowance being set up explains it
            # (Certara's 2025: $9.2M of tax on $7.6M of pretax income, 2% of its sales).
            if one_off > 0 and (pretax <= SCALE_SALES * rev or reit) and not va1 >= 0.5 * one_off:
                one_off = 0.0
            if abs(one_off) >= floor:
                item = {"kind": "tax_charge" if one_off > 0 else "tax_benefit", "amount": abs(one_off), "effect": one_off,
                        "rate": round(r, 4), "reported_tax": s["income_tax"], "normal_tax": normal, "adj_pretax": pretax}
                if va1 * one_off > 0 and abs(va1) >= 0.5 * abs(one_off):
                    item["cause"] = "valuation_allowance"
                items = items + [item]
        total = sum(i["effect"] for i in items)
        if not items or abs(total) < TOTAL_MIN * scale:
            out[j] = (ni, [])  # too small to change any score
        else:
            out[j] = (ni + total, items)
    return out


def derive(f, tax_rate=NORMAL_TAX):
    """Turns raw line items into the ratios the screener and reports use. Missing data stays None.

    Earnings-based measures (the adj_ keys and profitable_years) leave out one-time items. `tax_rate` is the
    normal rate those earnings are taxed at: 0 for a REIT, which pays no corporate income tax on what it pays out,
    None for a company whose tax is normally below nothing, or NORMAL_TAX to use the company's own steady rate where
    it has one (see _adjust and _normal_rate).
    """
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
    window = list(range(max(0, i_last - 3), i_last + 1))
    found = _adjust(series, window, max(cash, L.get("peak_cash") or 0), tax_rate)
    adjusted = [found[j] for j in window]
    adj_ni, one_time = adjusted[-1]

    hist = [{"year": y, "revenue": s.get("revenue"), "net_income": s.get("net_income"), "fcf": s.get("fcf")}
            for y, s in zip(ys, ss) if s.get("revenue") is not None or s.get("net_income") is not None]
    recent3 = [a for a, _ in adjusted[-3:] if a is not None]
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
        # The same measures without one-time items (equal to the above when there are none).
        "adj_net_income": adj_ni,
        "adj_net_margin": (adj_ni / rev) if rev and adj_ni is not None else None,
        "adj_roe": (adj_ni / equity) if equity and equity > 0 and adj_ni is not None else None,
        "one_time": one_time,
        "pretax_income": last.get("pretax"),
        "income_tax": last.get("income_tax"),
        "shares_out": L.get("shares_out"),
        "total_assets": L.get("total_assets"),
        "profitable_years": sum(1 for x in recent3 if x > 0),
        "profitable_years_reported": sum(1 for s in ss[-3:] if (s.get("net_income") or 0) > 0),
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
