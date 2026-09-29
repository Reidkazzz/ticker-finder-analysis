"""Why a listing has no financial figures, told from what it files with the SEC.

Each listing the pipeline can't score gets a plain-English reason on its report (coverage) instead of a guess: a company
that has filed quarterly reports but no yearly one yet (or a first yearly one without machine-readable figures), a
company whose yearly reports are under international accounting rules or in another currency, a fund, or a company
whose yearly reports give no revenue this site can read. The kind of filer comes from its SEC company record (one
request, kept in .cache/filers.json), and for a foreign filer or one with no figures in dollars in the SEC's data from
which accounting rules and currency its figures are tagged in (one or two small requests).
"""
import datetime as dt
import json
import os
import threading
import time

from . import fundamentals, net

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "filers.json")
# A filer's kind rarely changes, so each record is looked up again every REFRESH_DAYS, at most REFRESH_PER_RUN a run,
# or at once where the company's figures no longer fit it (a company said to have filed no yearly report that now has
# a year's results in the SEC's data). The first run looks up every listing without figures, about 1,300 on September
# 2026 data, the most valuable first, within MINUTES; the rest wait for the next run.
REFRESH_DAYS = 30
REFRESH_PER_RUN = 60
MINUTES = 6
# The forms that tell what kind of filer a company is, counted among its recent filings (the SEC's "recent" list holds
# at least its last year of filings).
ANNUAL = ("10-K", "10-K/A", "10-KT", "10-KT/A")
QUARTERLY = ("10-Q", "10-Q/A")
FOREIGN = ("20-F", "20-F/A", "40-F", "40-F/A")
# A foreign company's current reports, which carry its news and interim results but no full year.
CURRENT = ("6-K",)
# An investment company's reports: shareholder reports and portfolio holdings. A business development company files
# 10-Ks and 10-Qs instead, and has figures (fundamentals' total investment income).
FUND = ("N-CSR", "N-CSRS", "N-CSR/A", "N-CSRS/A", "NPORT-P", "N-30D")
# The SEC's industry code for blank-check companies (SPACs), which hold the money they raised in a trust until they buy a
# business. The interest the trust earns is their only income, and some tag it as a business development company tags
# its revenue (build.py leaves such a company without figures).
BLANK_CHECK = "6770"
CURRENCIES = {"CAD": "Canadian dollars", "EUR": "euros", "GBP": "British pounds", "JPY": "Japanese yen",
              "CHF": "Swiss francs", "AUD": "Australian dollars", "CNY": "Chinese yuan", "HKD": "Hong Kong dollars",
              "INR": "Indian rupees", "BRL": "Brazilian reais", "MXN": "Mexican pesos", "SEK": "Swedish kronor",
              "DKK": "Danish kroner", "NOK": "Norwegian kroner", "ILS": "Israeli shekels", "KRW": "Korean won",
              "TWD": "Taiwan dollars", "SGD": "Singapore dollars", "ZAR": "South African rand", "CLP": "Chilean pesos",
              "COP": "Colombian pesos", "ARS": "Argentine pesos", "PEN": "Peruvian soles", "IDR": "Indonesian rupiah",
              "PHP": "Philippine pesos", "TRY": "Turkish lira", "RUB": "Russian rubles", "NZD": "New Zealand dollars"}


def _record(cik, rules=False):
    """A filer's profile from its SEC company record: {"forms": {form: [count, latest date]} for the forms above,
    "sic", "annual_xbrl"} (whether its newest yearly report, a 10-K, 20-F or 40-F, came with machine-readable figures;
    None without one), and for a foreign filer, or with `rules` a 10-K filer (a company without figures in dollars in
    the SEC's data), "rules" ({"taxonomy": "us-gaap", "ifrs-full" or None, "currency"}: the accounting rules and
    currency its balance sheet is tagged in, both None where the SEC's data holds no figures of it). "rules" is left out
    where the SEC answered without figures, which it sometimes does wrongly (fundamentals._tag_figures), so the next look
    at the record tries again."""
    sub = net.sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms, newest = {}, None
    flags = recent.get("isXBRL") or []
    for i, (form, day) in enumerate(zip(recent.get("form") or [], recent.get("filingDate") or [])):
        if form in ANNUAL + QUARTERLY + FOREIGN + FUND + CURRENT:
            count, last = forms.get(form, [0, ""])
            forms[form] = [count + 1, max(last, day)]
        if newest is None and form in ANNUAL + FOREIGN and not form.endswith("/A"):
            newest = i  # the list runs newest first; an amendment may carry only a page or two, so the report counts
    xbrl = bool(flags[newest]) if newest is not None and newest < len(flags) and flags[newest] is not None else None
    out = {"forms": forms, "sic": sub.get("sic") or None, "annual_xbrl": xbrl}
    # Only kind() and note() read the rules, for a foreign filer and for a 10-K filer without figures in dollars, and
    # never for a blank-check company.
    if ((any(f in forms for f in FOREIGN) or rules and any(f in forms for f in ANNUAL))
            and str(out["sic"] or "") != BLANK_CHECK):
        found, empty = None, False
        for taxonomy in ("us-gaap", "ifrs-full"):
            try:
                units = net.sec_json(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/Assets.json"
                                     )["units"]
            except net.NotFound:
                continue
            # The currency most of its balance sheets are in.
            counts = {u: len(v) for u, v in units.items() if isinstance(v, list) and v}
            if counts:
                found = {"taxonomy": taxonomy, "currency": max(counts, key=counts.get)}
                break
            empty = True
        if found or not empty:
            out["rules"] = found or {"taxonomy": None, "currency": None}
    return out


def profiles(needs, today, companies=None, minutes=MINUTES):
    """{cik: profile (_record) with "checked"} for the CIKs in `needs` ({cik: market value}) that have one: those not
    looked up yet first, the most valuable first, then those whose figures in `companies` (the fundamentals records)
    no longer fit their profile (stale) and the oldest records. A company the SEC's data holds no fiscal year's results
    of in dollars also has the accounting rules and currency of its figures looked up (_record). Stops at the time
    budget or when the SEC throttles; the rest wait for the next run. Kept in CACHE."""
    try:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    old = (today - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    recheck = {c for c in needs if str(c) in cache and stale(cache[str(c)], (companies or {}).get(c))}
    todo = sorted((c for c in needs if str(c) not in cache), key=lambda c: -(needs[c] or 0))
    todo += [c for c in needs if str(c) in cache and c in recheck]
    todo += sorted((c for c in needs if str(c) in cache and c not in recheck and cache[str(c)].get("checked", "") < old),
                   key=lambda c: cache[str(c)].get("checked", ""))[:REFRESH_PER_RUN]
    stop, done, blocked = time.monotonic() + minutes * 60, 0, threading.Event()
    for cik in todo:
        if time.monotonic() > stop or blocked.is_set():
            break
        try:
            rec = _record(cik, rules=not _results((companies or {}).get(cik)))
        except net.NotFound:
            rec = {"forms": {}, "sic": None, "missing": True}
        except net.Throttled:
            blocked.set()
            continue
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            continue
        cache[str(cik)] = {**rec, "checked": today.isoformat()}
        done += 1
    if done:
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            tmp = CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, CACHE)
        except OSError:
            pass
    known = sum(1 for c in needs if str(c) in cache)
    print(f"  Filer records of listings without full figures: {done} looked up"
          + (" (the SEC throttled the rest)" if blocked.is_set() else "") + f", {known} of {len(needs)} known",
          flush=True)
    return {c: cache[str(c)] for c in needs if str(c) in cache}


def _annual_count(profile):
    """How many yearly reports (10-K, not amendments) are among the company's recent filings."""
    return sum(profile["forms"].get(f, [0])[0] for f in ANNUAL if not f.endswith("/A"))


def _latest(profile, forms):
    return max((profile["forms"][f][1] for f in forms if f in profile.get("forms", {})), default="")


def kind(profile, company=None):
    """Why a listing without full figures has none, from its filer profile and its fundamentals record (`company`,
    None where the SEC's data has nothing of it), or None where that can't be told (no profile yet):
    "blank_check" (a SPAC, by its industry code), "fund" (an investment company's reports, newer than any 10-K, 10-Q,
    20-F or 40-F it filed),
    "foreign" (its newest yearly report is a 20-F or 40-F whose figures the SEC's data doesn't hold in dollars under US
    rules), "currency" (a 10-K under US rules whose figures are in another currency), "no_annual" (10-Qs but no yearly report), "annual_untagged" (10-Qs, and a yearly
    report filed without machine-readable figures, as a company's first after it lists may be), "new" (none of these, as
    for a company that listed weeks ago), "revenue_unread" (yearly reports whose newest results show a profit, or whose
    quarters show revenue, so the revenue its years must have is under labels not read), "no_revenue" (yearly reports
    without a revenue figure read, no profit and no quarter with revenue), "annual_missing" (yearly reports filed with machine-readable figures, of which the SEC's data holds no full
    year), or "unread" (yearly reports whose figures could not be read for another reason)."""
    if not profile:
        return None
    forms = profile.get("forms") or {}
    annual, foreign, quarterly = _latest(profile, ANNUAL), _latest(profile, FOREIGN), _latest(profile, QUARTERLY)
    years = _results(company)
    rules = profile.get("rules") or {}
    if str(profile.get("sic") or "") == BLANK_CHECK:
        return "blank_check"
    if _latest(profile, FUND) > max(annual, foreign, quarterly):
        # Its fund reports are newer than any operating company's report among its recent filings, which can reach back
        # decades: Nuveen Municipal Value Fund's list still holds a 10-K from 2000, and Ellington Credit's its 10-Ks
        # up to 2025, when it became a closed-end fund.
        return "fund"
    if foreign and foreign >= annual and not years:
        return "foreign"
    if annual and not years and rules.get("taxonomy") == "us-gaap" and rules.get("currency") not in (None, "USD"):
        return "currency"
    if not annual and not foreign:
        return "no_annual" if quarterly else "new"
    if not years and profile.get("annual_xbrl") is False and quarterly:
        return "annual_untagged"
    if years and all(s.get("revenue") is None for _, s in years):
        # A profit needs revenue, and so do quarters that show it (Proficient Auto Logistics' $109M in its latest quarter,
        # beside yearly revenue tagged only under its own labels).
        return "revenue_unread" if years[-1][1]["net_income"] > 0 or _quarter_revenue(company) else "no_revenue"
    if not years and profile.get("annual_xbrl"):
        return "annual_missing"
    return "unread"


def _quarter_revenue(company):
    """Whether any quarter in a fundamentals record gives revenue under a tag the site reads (fundamentals.REVENUE_TAGS)."""
    return any(fundamentals.REVENUE_TAGS & facts.keys() for facts in ((company or {}).get("quarters") or {}).values())


def _results(company):
    """[(fiscal year, figures)] of the years with net income in a fundamentals record, oldest first."""
    return [(y, s) for y, s in sorted(((company or {}).get("annual") or {}).items()) if s.get("net_income") is not None]


# Kinds whose page shows the quarters the company's reports give (build.py's pending_doc): a listing whose SEC data holds
# quarters but no full fiscal year. Where a yearly report's revenue label isn't read, or a company's figures are in
# another currency or unread for another reason, a quarter here and there would sit oddly beside that reason.
QUARTER_KINDS = ("no_annual", "annual_untagged", "annual_missing")


def shows_quarters(k, company):
    """Whether a listing of kind `k` (kind; None where its filer record isn't known yet) shows its quarters: one of
    QUARTER_KINDS, or of an unknown kind with no fiscal year's results in the SEC's data."""
    return k in QUARTER_KINDS or k is None and not _results(company)


def stale(profile, company):
    """Whether a profile no longer fits the company's figures: it lists no yearly report, while the SEC's data holds a
    year's results (a first 10-K filed since it was looked up)."""
    return bool(profile) and not _latest(profile, ANNUAL + FOREIGN) and bool(_results(company))


def note(name, k, profile, company=None, quarters=False):
    """The report's plain-English reason for a listing of kind `k` (kind) having no scores or fair value, or None.
    `quarters`: the page shows quarters from its reports (build.py's pending_doc)."""
    # The listing's name as a sentence can carry it: runs of spaces closed up, and a parenthesis Nasdaq's listing cut off
    # left out ("Banco Macro S.A.  ADR (representing Ten Class B").
    name = " ".join((name or "").split())
    if "(" in name and ")" not in name[name.rindex("("):]:
        name = name[:name.rindex("(")].rstrip()
    until = " Until then this page shows the quarters its reports give." if quarters else ""
    if k == "no_annual":
        return (f"{name} has filed quarterly reports (Form 10-Q) with the SEC but no yearly report (Form 10-K) yet, "
                "which is usual for a company that listed or was spun off recently. The health check and fair value "
                "need a full fiscal year of results, so they will appear after its first yearly report." + until)
    if k == "annual_untagged":
        if _annual_count(profile) <= 1:
            return (f"{name}'s first yearly report (Form 10-K) was filed without the machine-readable figures its "
                    "quarterly reports carry, which SEC rules allow when a company's first report after it lists is a "
                    "yearly one. The health check and fair value need a full fiscal year of results in that form, so "
                    "they will appear after its next yearly report." + until)
        return (f"{name} files its yearly reports (Form 10-K) without machine-readable figures, so the SEC's data holds "
                "no full fiscal year of its results and this site can't score or value it."
                + until.replace("Until then", "Meanwhile"))
    if k == "currency":
        money = CURRENCIES.get(profile["rules"]["currency"], profile["rules"]["currency"])
        return (f"{name} files its yearly reports with the SEC (Form 10-K) under US accounting rules, but in {money}. "
                "This site reads only figures reported in US dollars, and converting them would take exchange rates its "
                "filings don't give, so it can't score or value this company yet.")
    if k == "annual_missing":
        first = _annual_count(profile) <= 1
        return (f"{name} files yearly reports with the SEC with machine-readable figures, but the SEC's data holds no "
                + ("full fiscal year of results from its first one, which may cover only part of a year" if first else
                   "full fiscal year of results from its recent ones")
                + ", so this site can't score or value it yet."
                + (" Meanwhile this page shows the quarters its reports give." if quarters else ""))
    if k == "foreign":
        form = "Form 40-F" if _latest(profile, ("40-F", "40-F/A")) >= _latest(profile, ("20-F", "20-F/A")) \
            else "Form 20-F"
        rules = profile.get("rules") or {}
        head = f"{name} files its yearly reports with the SEC as a foreign company ({form})"
        if rules.get("taxonomy") == "ifrs-full":
            money = CURRENCIES.get(rules.get("currency"), rules.get("currency"))
            return (f"{head}, under international accounting rules (IFRS)"
                    + (f", in {money}" if rules.get("currency") not in (None, "USD") else "")
                    + ". This site reads only figures reported under US accounting rules, so it can't score or value "
                      "this company yet.")
        if rules.get("taxonomy") == "us-gaap" and rules.get("currency") not in (None, "USD"):
            money = CURRENCIES.get(rules["currency"], rules["currency"])
            return (f"{head}, in {money}. This site reads only figures reported in US dollars, and converting them would "
                    "take exchange rates its filings don't give, so it can't score or value this company yet.")
        if rules.get("taxonomy") is None and "rules" in profile:
            return (f"{head}, and the SEC's data holds no machine-readable figures from them, so this site can't score "
                    "or value it.")
        return (f"{head}. This site could not read a full year of revenue and profit from those reports, so it can't "
                "score or value this company yet.")
    if k == "blank_check":
        return (f"{name} is a blank-check company (a SPAC): it holds the money it raised from investors in a trust until "
                "it buys or merges with a business, so it has no business of its own to score or value yet. What its SEC "
                "filings show as income is mostly interest on that trust, not sales.")
    if k == "fund":
        return (f"{name} is a fund: it holds a portfolio of investments and reports to the SEC as an investment company "
                "(Form N-CSR), not with the financial statements of an operating business. A fund is worth what its "
                "holdings are worth (its net asset value), so it isn't scored or valued here as a company would be.")
    if k == "new":
        if _latest(profile, CURRENT):
            return (f"{name} files with the SEC as a foreign company, so far only current reports (Form 6-K), which "
                    "carry no full year of results. The health check and fair value need a yearly report (Form 20-F or "
                    "40-F), so there are none here yet.")
        return (f"{name} has not filed a quarterly or yearly report with the SEC recently, so there are no financial "
                "figures from the SEC to read. It may have listed only weeks ago, or report to another regulator, as some "
                "banks do.")
    if k == "revenue_unread":
        y, s = _results(company)[-1]
        return (f"{name} files yearly reports under US accounting rules and reported "
                f"{'a profit' if s['net_income'] > 0 else 'a loss'} for {y}, but its revenue in those reports is under "
                "labels this site doesn't read (often ones of the company's own), so it has no full year of sales here "
                "and can't be scored or valued.")
    if k == "no_revenue":
        return (f"{name} files yearly reports under US accounting rules, but none of them gives a revenue figure under "
                "the labels this site reads. It may have no sales yet, or label its revenue in a way of its own. Without "
                "a sales figure it can't be scored or valued.")
    if k == "unread":
        if company is None and "rules" in profile and profile["rules"]["taxonomy"] is None:
            return (f"{name} files yearly reports with the SEC, but the SEC's data holds no machine-readable figures "
                    "from them, so this site can't score or value it.")
        return (f"This site could not read a full year of revenue and profit from {name}'s SEC filings, so it can't "
                "score or value it.")
    return None


# Kinds whose missing figures are expected (a listing too new, or whose first yearly report has no machine-readable
# figures, a foreign filer, a company reporting in another currency, a fund, a SPAC, a company without revenue), which
# the run's log lists apart from those that deserve a look (build.py's warning).
EXPECTED = ("no_annual", "annual_untagged", "new", "foreign", "currency", "fund", "blank_check", "no_revenue")
