"""Single-ticker report: 1-100 health bars, a fair-value range and a verdict."""
import bisect
import datetime as dt
import math
import statistics

from .fundamentals import FEDERAL_TAX, LEASE_MIN, OWN_RATE, STATE_TAX, STATUTORY_TAX
from .reit_types import TYPES as REIT_TYPES

# Deposits, loans and insurance reserves make cash, debt and cash flow part of the business itself for
# banks, insurers and lenders, so cash-based measures are neither scored nor used to value them.
FINANCIAL_SECTOR = "Finance"
FINANCIAL_NOTE = ("Not scored for banks, insurers and other financial companies. Their cash and borrowing are part of "
                  "the business itself (deposits, loans, insurance reserves), so this measure would mislead.")


def is_financial(u):
    return u.get("sector") == FINANCIAL_SECTOR


# The SEC's industry codes for banks and savings institutions (thrifts): national, state and other commercial banks,
# and federally and state chartered savings institutions. Bank holding companies file under their bank's code.
BANK_SIC = {"6021", "6022", "6029", "6035", "6036"}
# Nasdaq's labels for them, which count where the SEC code is unknown. On September 2026 data all 317 companies with one
# of the codes above carried one of these labels, and so did 9 more whose code was unknown (Bank OZK, which files with
# the FDIC rather than the SEC, and foreign banks), while no company under these labels had another code.
BANK_INDUSTRIES = {"Major Banks", "Commercial Banks", "Banks", "Savings Institutions"}


def is_bank(u, sic=None):
    """Whether a company is a bank or savings institution, valued and scored the way bank analysts do: its SEC industry
    code (`sic`) is a bank's, or it is unknown and Nasdaq labels the company a bank. A known code that is not a bank's
    (a card issuer, a broker, an insurer) wins over the label."""
    code = str(sic or "")
    return code in BANK_SIC if code else u.get("industry") in BANK_INDUSTRIES


UTILITIES = {"Electric Utilities: Central", "Power Generation", "Natural Gas Distribution", "Water Supply"}
REIT_INDUSTRY = "Real Estate Investment Trusts"
REIT_SIC = "6798"  # the SEC's industry code for real estate investment trusts
# Nasdaq lists some REITs under these instead of "Real Estate Investment Trusts" (Invitation Homes and Terreno Realty
# under Real Estate, Sunstone Hotel Investors under Hotels/Resorts).
PROPERTY_INDUSTRIES = {"Real Estate", "Building operators", "Hotels/Resorts"}
# Sectors whose companies' SEC industry codes are looked up (build.sic_codes): financial companies for their peer
# groups, and the sectors and industries where Nasdaq lists REITs, so one it labels otherwise shows by its code.
SIC_SECTORS = {"Finance", "Real Estate"}


def wants_sic(u):
    return u.get("sector") in SIC_SECTORS or u.get("industry") in PROPERTY_INDUSTRIES


def is_reit(u, company=None, sic=None):
    """Whether a company is a real estate investment trust (REIT): Nasdaq labels it one, its SEC industry code is
    6798 (`sic`), or it is a property company (PROPERTY_INDUSTRIES) whose filings show what a REIT's do.

    On September 2026 data every company with code 6798 was also labelled a REIT by Nasdaq, while Nasdaq listed at least
    fourteen REITs under other industries. _untaxed() finds those that have been profitable for years, and
    _paying_landlord() the ones depreciation has left with losses, which a tax test on profitable years misses (Hudson
    Pacific, Office Properties Income Trust, Piedmont, Urban Edge, AH Realty, Xenia Hotels). `company` is the
    fundamentals record.

    The label and the code are not enough on their own, since both outlast a REIT's conversion to an ordinary taxed
    company (_taxed_company)."""
    if u.get("industry") == REIT_INDUSTRY or str(sic or "") == REIT_SIC:
        return not _taxed_company(company, sic)
    if u.get("industry") not in PROPERTY_INDUSTRIES:
        return False
    return _untaxed(company) or _paying_landlord(company)


def _taxed_company(company, sic=None):
    """Whether a company labelled a REIT behaves as an ordinary taxed company instead: its newest year's common
    dividends came to under a tenth of its net income plus depreciation (a REIT must pay out most of its taxable
    income), and it paid income tax of over 15% of pretax income in at least two of its last three profitable years or
    its SEC industry code is neither a real estate one (65) nor a REIT's (6798). On September 2026 data that picks out
    CoreCivic, Howard Hughes, Transcontinental Realty and Income Opportunity Realty (taxed, no dividends), Uniti Group
    (a telecom company since its 2025 merger, no dividend) and DigitalBridge (an asset manager paying 4%), while REITs
    that pay tax on large taxable subsidiaries (Iron Mountain, Rithm Capital) pay out most of their earnings, and a REIT
    that cut its dividend to little (Industrial Logistics) pays no tax. A run whose dividend frames failed keeps the
    label."""
    annual = (company or {}).get("annual") or {}
    newest = _newest(annual)
    paid = newest.get("dividends")
    if paid is None:
        return False
    earned = (newest.get("net_income") or 0) + (newest.get("depreciation") or 0)
    if paid > 0.10 * max(earned, 0):
        return False
    rates = []
    for y in sorted(annual):
        s = annual[y]
        ni = s.get("net_income")
        base = s.get("pretax") if s.get("pretax") is not None else ni
        if ni is not None and ni > 0 and base and base > 0:
            rates.append((s.get("income_tax") or 0) / base)
    code = str(sic or "")
    return sum(1 for r in rates[-3:] if r > 0.15) >= 2 or bool(code) and not (code.startswith("65") or code == REIT_SIC)


def normal_tax_rate(u, company=None, sic=None):
    """derive()'s tax_rate. A REIT pays no corporate income tax on the profit it pays out, so its normal rate is zero
    (is_reit), which also has derive() work out its funds from operations. A utility's tax is shaped every year by
    regulators and energy tax credits (PG&E paid less than nothing in each of 2022 to 2025), so a benefit on its profit
    is normal (None). Any other company is taxed at its own steady rate where it has one (OWN_RATE)."""
    if is_reit(u, company, sic):
        return 0.0
    return None if u.get("industry") in UTILITIES else OWN_RATE


def _untaxed(company):
    """Whether a company paid next to no income tax (under 5% of pretax income) in each of at least three profitable
    years, and paid its shareholders a dividend in its newest year, as a REIT must. For a property company that means a
    REIT. On September 2026 data it picked out seven REITs Nasdaq lists under other industries (Curbline, Gladstone
    Commercial, Getty Realty, Innovative Industrial Properties, Invitation Homes, Sunstone Hotel Investors, Terreno
    Realty) and Ellington Financial, a mortgage REIT. Without the dividend test it also picked TDH Holdings, a small
    company that is not one."""
    annual = (company or {}).get("annual") or {}
    ok = []
    for s in annual.values():
        ni = s.get("net_income")
        base = s.get("pretax") if s.get("pretax") is not None else ni
        if ni is not None and ni > 0 and base and base > 0:
            ok.append(abs(s.get("income_tax") or 0) <= 0.05 * base)
    if len(ok) < 3 or not all(ok):
        return False
    paid = _newest(annual).get("dividends")
    return paid is None or paid > 0  # a run whose dividend frames failed can't tell, so it keeps the tax test alone


def _paying_landlord(company):
    """Whether a property company looks like a REIT that depreciation has left with losses: it owns property
    (_landlord), paid common dividends in at least three of its last four years (a REIT must pay out most of its
    taxable income, and keeps paying while its depreciation makes losses), and paid next to no income tax (under 5% of
    pretax income or 1% of sales) in every year but at most one (Urban Edge's 2023 tax on selling property). Of the
    property companies that are not REITs on September 2026 data, those without income tax paid no dividend (Sky
    Harbour, Seritage, Mobile Infrastructure) and those that pay dividends paid tax (Forestar, hotel and casino
    companies)."""
    annual = (company or {}).get("annual") or {}
    years = [y for y in sorted(annual) if annual[y].get("net_income") is not None]
    if len(years) < 3 or not _landlord(_newest(annual)):
        return False
    taxed = 0
    for y in years:
        s = annual[y]
        base = s.get("pretax") if s.get("pretax") is not None else s["net_income"]
        sales = s.get("revenue") or s.get("lease_income") or 0
        taxed += abs(s.get("income_tax") or 0) > max(0.05 * abs(base), 0.01 * sales)
    recent = [annual[y].get("dividends") for y in years[-4:]]
    return taxed <= 1 and None not in recent and sum(1 for d in recent if d > 0) >= min(3, len(recent))


def _newest(annual):
    """The newest year with results."""
    return next((annual[y] for y in sorted(annual, reverse=True) if annual[y].get("net_income") is not None), {})


def _landlord(s):
    """Whether a year's depreciation came to at least a tenth of its sales, as a property owner's does."""
    sales = s.get("revenue") or s.get("lease_income")
    return bool(sales and sales > 0 and (s.get("depreciation") or 0) >= 0.10 * sales)


# A REIT whose depreciation comes to less than this share of its sales earns mostly interest: a mortgage REIT, or a
# landlord whose leases are booked as loans (VICI Properties, Safehold). Its funds from operations are much the same as
# its net income, so it is valued on earnings like a lender, against other such REITs, and not on sales. On September
# 2026 data, for the 13 of them valued both ways, their peers' price to earnings missed their price by a typical factor
# of 1.17 (median absolute log error 0.161, a median 3% low), where matching them with REITs that own property and
# other real estate companies had missed by 3.18, all too high. Their peers' price to sales missed by 1.77.
REIT_DEPRECIATION_MIN = 0.05
# A lender that took over a few properties can pass that test (Ares Commercial Real Estate's 2025 depreciation came to
# 15% of its sales, BrightSpire Capital's to 11%), but its depreciation is a sliver of its assets, which are mostly
# loans. A REIT whose depreciation is under this share of its total assets is taken for a lender unless its rent comes
# to at least fundamentals.LEASE_MIN of its assets, as a farmland owner's does, whose land is never depreciated
# (Farmland Partners': 0.6% and 5.1%). On September 2026 data, landlords' depreciation came to 1.6% of their assets or
# more, Ares Commercial's to 0.5% and BrightSpire's to 1.0% (with rent of 2.4%).
REIT_LANDLORD_DEPRECIATION = 0.0125


def reit_kind(m):
    """"property" for a REIT valued on funds from operations, "mortgage" for one that earns mostly interest (its
    depreciation is under REIT_DEPRECIATION_MIN of its sales, or its assets are mostly loans by
    REIT_LANDLORD_DEPRECIATION, or it tagged none in any year), else None: a REIT whose newest year lacks the figures
    though earlier ones had depreciation (Rayonier's 2025), or whose depreciation frames failed, is valued as other
    companies are, and so is a timber REIT (reit_types), whose depletion is the cost of the timber it sells rather than
    wear on a building, and which reports no funds from operations (Weyerhaeuser)."""
    if not m or not m.get("reit") or m.get("reit_type") == "timber":
        return None
    share = m.get("depreciation_share")
    to_assets = m.get("depreciation_to_assets")
    lender = (to_assets is not None and to_assets < REIT_LANDLORD_DEPRECIATION
              and (m.get("rent_to_assets") or 0) < LEASE_MIN)
    if share is not None and (share < REIT_DEPRECIATION_MIN or lender):
        return "mortgage"
    if share is not None:
        return "property" if m.get("adj_ffo") is not None else None
    return "mortgage" if not m.get("depreciation_years") and not m.get("depreciation_failed") else None


def _whole(m):
    """The listed shares' part of a REIT that owns property, where holders of its operating partnership's units own the
    rest (fundamentals._owners): its sales, debt and cash are the whole business's, its market value only theirs
    (Strawberry Fields' Class A shares: 23%; Simon Property's: 88%). 1 for any other company."""
    return (m.get("owners_share") or 1.0) if reit_kind(m) == "property" else 1.0


def financial(u, m=None):
    """Whether a company is valued and scored as a financial one: Nasdaq's Finance sector, where banks, insurers and
    lenders sit, and any mortgage REIT (reit_kind), but not a REIT that owns property, which Nasdaq lists under Finance
    (Terreno Realty) as often as under Real Estate. A landlord's debt and cash are an ordinary company's."""
    kind = reit_kind(m)
    return kind == "mortgage" or kind is None and is_financial(u)


def _lerp(x, pts):
    """Piecewise-linear map through [(x, score), ...] sorted by x, clamped to 1-100."""
    if x <= pts[0][0]:
        y = pts[0][1]
    elif x >= pts[-1][0]:
        y = pts[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                break
    return int(round(max(1, min(100, y))))


def _pct_rank(value, population, lower_is_better):
    """Share of peers this company beats, as 1-100."""
    pop = [p for p in population if p is not None]
    if value is None or len(pop) < 5:
        return None
    worse = sum(1 for p in pop if (p > value if lower_is_better else p < value))
    ties = sum(1 for p in pop if p == value)
    return int(round(max(1, min(100, (worse + ties / 2) / len(pop) * 100))))


def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def multiple(x):
    return "n/a" if x is None else f"{x:.1f}x"


def dollars(x):
    return f"${x:,.2f}"


def money(x):
    x = abs(x)
    return f"${x / 1e9:,.1f}B" if x >= 1e9 else f"${x / 1e6:,.0f}M" if x >= 1e6 else f"${x:,.0f}"


def cents(x):
    n = round(abs(x) * 100)
    return f"{n} cent{'' if n == 1 else 's'}"


def profit(x):
    return money(x) if x >= 0 else f"a loss of {money(x)}"


def one_time_note(m, ffo=True):
    """Plain-English note on the one-time items left out of last year's profit, or None when there were none.

    Gains taken out and charges added back are named separately: "Last year's profit of $57M included a $248M gain
    on selling a business or assets, and was reduced by a $1.6B write-down of goodwill (...). Scores, screener checks
    and the fair value use profit without them, which gives $1.1B." fundamentals.KINDS lists what each kind covers.
    Where minority holders own part of the business, the note says that only the company's own share counts.

    For a REIT that owns property, whose report scores and values funds from operations, it is ffo_note() instead,
    unless `ffo` is False (the screener, which checks every company's profit margin the same way).
    """
    if ffo and reit_kind(m) == "property":
        return ffo_note(m)
    items = m.get("one_time") or []
    if not items:
        return None
    phrases = {
        "goodwill_impairment": "a {} write-down of goodwill (the premium it paid for past acquisitions)",
        "impairment": "{} of write-downs of other assets",
        "sale_gain": "a {} gain on selling a business or assets",
        "sale_loss": "a {} loss on selling a business or assets",
        "securities_gain": "a {} gain on investments",
        "securities_loss": "a {} loss on investments",
        "debt_gain": "a {} gain on paying off debt for less than it owed",
        "debt_loss": "a {} loss on paying off debt early",
        "gain": "about {} of gains outside its main business, such as sales of assets or investments",
        "discontinued": "{} of profit from businesses it sold or closed",
        "discontinued_loss": "{} of losses from businesses it sold or closed",
        "tax_benefit": "a tax bill {} below normal",
        "tax_charge": "a tax bill {} above normal",
    }
    listed = lambda parts: parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    gains, charges, tax = [], [], None
    for i in sorted(items, key=lambda i: i["kind"] == "gain"):  # its long phrase reads best last
        text = phrases.get(i["kind"], "{} of one-time items")
        if i.get("reported"):  # only part of the item counts as one-time
            noun = text[2:] if text.startswith("a ") else text[6:] if text.startswith("about ") else text
            phrase = f"{money(i['amount'])} of its " + noun.format(money(i["reported"]))
        else:
            phrase = text.format(money(i["amount"]))
        if i["kind"] in ("tax_benefit", "tax_charge"):
            tax = i
            if i.get("cause") == "valuation_allowance":
                phrase += (", mostly from counting past losses as future tax savings" if i["kind"] == "tax_benefit"
                           else ", mostly from writing off tax savings it no longer expects to use")
        # Older items carry no effect, and they were all gains taken out.
        (charges if i.get("effect", -i["amount"]) > 0 else gains).append(phrase)
    ni = m["net_income"]
    if ni >= 0:
        clauses = ([f"included {listed(gains)}"] if gains else []) + ([f"was reduced by {listed(charges)}"] if charges else [])
        head = f"Last year's profit of {money(ni)}"
    else:
        clauses = ([f"included {listed(charges)}"] if charges else []) + ([f"was made smaller by {listed(gains)}"] if gains else [])
        head = f"Last year's loss of {money(ni)}"
    how = ""
    if tax and (tax.get("adj_pretax") or 0) > 0:
        rate = tax["normal_tax"] / tax["adj_pretax"]
        why = _tax_basis_text(tax, rate, abroad=not (m.get("loc") or "US").startswith("US"))
        how = (f", with tax at a normal {_rate_text(rate)} of profit{why}" if 0.05 <= rate <= 0.4
               else ", with tax about as low as in its other years" if rate < 0.05
               else ", with tax about as high as in its other years")
    them = "it" if len(items) == 1 else "them"
    # Where minority holders own part of the business, only the company's own share of an item moves its profit.
    share = min((i["share"] for i in items if i.get("share") is not None), default=None)
    what = "that item" if len(items) == 1 else "the items other than tax" if tax else "these items"
    owned = (f" Minority holders own part of the business, so only its {share * 100:.0f}% share of {what} counts."
             if share is not None else "")
    return (f"{head} {', and '.join(clauses)}.{owned} Scores, screener checks and the fair value use profit without "
            f"{them}{how}, which gives {profit(m['adj_net_income'])}.")


# One-time items outside funds from operations' own adjustments, as ffo_note() names them.
_FFO_OTHER = {
    "goodwill_impairment": "a {} write-down of goodwill",
    "securities_gain": "a {} gain on investments", "securities_loss": "a {} loss on investments",
    "debt_gain": "a {} gain on paying off debt for less than it owed",
    "debt_loss": "a {} loss on paying off debt early",
    "gain": "about {} of other gains", "discontinued": "{} of profit from businesses it sold or closed",
    "discontinued_loss": "{} of losses from businesses it sold or closed",
    "tax_benefit": "a tax bill {} below normal", "tax_charge": "a tax bill {} above normal",
}


def ffo_note(m):
    """Plain-English note on how a REIT's funds from operations (FFO) come from its net income, for a REIT that owns
    property, or None without the figures: "Valued on funds from operations (FFO) ... Last year's net income of $401M
    for common shareholders, plus $122M of depreciation and amortization, less $238M of gains on selling or revaluing
    property, gives FFO of $284M." Then the one-time items the scores and fair value also leave out, if any. FFO is
    worked out from the filings' tags the way Nareit, the REIT industry body, defines it, so it can differ from the
    figure a REIT reports (fundamentals._ffo lists what the tags miss), and the note says so."""
    parts, ffo, adj = m.get("ffo_parts"), m.get("ffo"), m.get("adj_ffo")
    if not parts or ffo is None or adj is None:
        return None
    ni = parts["net_income_common"]
    steps = [f"plus {money(parts['depreciation'])} of depreciation and amortization"]
    gains, down = parts.get("property_gains") or 0, parts.get("write_downs") or 0
    if abs(gains) >= 5e5:
        steps.append(f"less {money(gains)} of gains on selling or revaluing property" if gains > 0
                     else f"plus {money(gains)} of losses on selling property")
    if down >= 5e5:
        steps.append(f"plus {money(down)} of property write-downs")
    if (parts.get("partners") or 0) >= 5e5:
        steps.append(f"less {money(parts['partners'])} of that depreciation belonging to its partners in jointly owned "
                     "properties (at least as much as the loss they took)")
    # Discontinued operations _ffo() left out itself, where the one-time items could not be read, join the steps, so
    # the FFO named is the one without them (Sun Communities' 2025: the $1.4B it made mostly on selling its marinas).
    d = parts.get("discontinued") or 0
    if d:
        steps.append(f"less {money(d)} of profit from businesses it sold or closed" if d > 0
                     else f"plus {money(d)} of losses from businesses it sold or closed")
    shown = ffo - d
    listed = ", ".join(steps[:-1]) + " and " + steps[-1] if len(steps) > 1 else steps[0]
    what = f"net income of {money(ni)}" if ni >= 0 else f"net loss of {money(ni)}"
    result = f"FFO of {money(shown)}" if shown >= 0 else f"negative FFO of {money(shown)}"
    text = ("Valued on funds from operations (FFO), the profit measure REITs report, rather than net income. FFO "
            "adds back depreciation, a cost on paper that tends to overstate the wear on well-kept buildings, and leaves "
            f"out gains on selling property. Last year's {what} for common shareholders, {listed}, gives {result}.")
    if parts.get("share") is not None:
        text += (f" Holders of its operating partnership's units own part of the business, so only the listed shares' "
                 f"{parts['share'] * 100:.0f}% of depreciation, gains and write-downs counts.")
    if abs(adj - shown) > 0.01 * max(abs(shown), abs(adj)):
        other = [_FFO_OTHER[i["kind"]].format(money(i["amount"]))
                 for i in (m.get("one_time") or []) if i["kind"] in _FFO_OTHER]
        named = f" ({other[0] if len(other) == 1 else ', '.join(other[:-1]) + ' and ' + other[-1]})" if other else ""
        text += (f" Scores and the fair value also leave out other one-time items{named}, which gives "
                 + (f"{money(adj)}." if adj >= 0 else f"negative FFO of {money(adj)}."))
    else:
        text += " Scores and the fair value use it."
    return text + (" It is worked out from the REIT's SEC filings and can differ from the FFO the REIT reports "
                   "itself.")


def _rate_text(rate):
    """A tax rate as a percentage with one decimal where it has one: 23.5%, 21%."""
    return f"{round(rate * 100, 1):g}%"


def _tax_basis_text(tax, rate, abroad=False):
    """Where the normal tax rate in one_time_note() comes from, in brackets, or "" for items written before the basis
    was recorded. The rate starts from the company's own steady rate or the statutory one (tax["rate"]) and moves from
    there by what the company usually saves or pays in its other years (fundamentals._adjust). The explanation names
    a rate only when it is the one shown, as rounded, and names US federal and state taxes only for a company based in
    the US (`abroad` is False)."""
    basis, start = tax.get("basis"), tax.get("rate")
    if basis not in ("own", "statutory") or start is None:
        return ""
    shown = _rate_text(rate)
    if basis == "own" and shown == _rate_text(start):
        return " (its steady rate in years without one-time tax items)"
    if shown == _rate_text(STATUTORY_TAX):
        # Also a company whose tax moved from its own rate toward this one, which only counts as one-time beyond it.
        if not abroad:
            return f" (the {_rate_text(FEDERAL_TAX)} federal rate plus {_rate_text(STATE_TAX)} for state taxes)"
        own = ", since its own years show no steady rate" if basis == "statutory" else ""
        return f" (a typical rate on company profits{own})"
    if basis == "own":
        return " (close to what it paid in its other years)"
    side = "below" if rate < start else "above"
    if abroad:
        return f" ({side} a typical {_rate_text(start)} on company profits, as in its other years)"
    return f" ({side} the usual {_rate_text(start)} for federal and state taxes, as in its other years)"


def band(score):
    return None if score is None else "strong" if score >= 70 else "fair" if score >= 40 else "weak"


# Peers. Each company is compared with the PEER_COUNT companies in its sector whose own numbers are closest on
# what drives a sales or earnings multiple: operating and net margin, free cash flow margin, sales growth over one
# and three years, size (sales) and sales per dollar of assets, with a small preference for its own Nasdaq
# industry (INDUSTRY_GAP). Nothing that depends on the share price is used, so a company's own valuation can't
# pull in expensive or cheap look-alikes. Each business counts once: the company's other share classes are left
# out, and so is a second share class of any peer. Tested leave-one-out on September 2026 data (every company's
# multiple predicted from its peers' median, the company itself left out), against Nasdaq's industry labels:
#   value to sales, profitable companies (1,701): typical miss a factor of 1.45 instead of 1.74 (median absolute
#     log error 0.369 vs 0.552), within 25% for 37% of companies instead of 27%
#   price to earnings (2,051): factor 1.51 instead of 1.60 (0.412 vs 0.471), within 25% for 33% instead of 31%
# Value to sales improved in every sector. Price to earnings got worse in four, none by more than chance could
# explain at 95%: energy (73 companies, 0.49 vs 0.44), telecommunications (32, 0.83 vs 0.67), utilities (85, 0.20
# vs 0.19; midstream pipelines listed as gas distribution are matched with electric utilities) and basic materials
# (15, 0.36 vs 0.35). SEC SIC codes in place of Nasdaq's labels, regression-based multiples and nearest matches
# across the whole market were all less accurate than this.
# Sales per dollar of assets was the figure that helped most: it separates low-margin, fast-turnover businesses
# (lead generation, staffing, distribution) from software companies with similar margins.
PEER_COUNT = 10
PEER_FEATURES = ("op_margin", "net_margin", "fcf_margin", "growth", "growth_3y", "size", "turnover")
# Added to the distance (a mean squared gap in percentile rank) of a candidate from another Nasdaq industry. It
# keeps homebuilders with homebuilders and water utilities with water utilities when their numbers are close, yet
# lets a mislabeled company find its real look-alikes. Chosen on half the companies (split by CIK) and confirmed
# on the other half. On the final September 2026 data it lowered the price to earnings error by 0.008 (95% interval
# -0.009 to +0.022) and the value to sales error by 0.007 (-0.006 to +0.024), so it costs no accuracy, and it raised
# the share of peers from the company's own industry from 28% to 42%. Not used inside SEC groups (below), where it
# made price to sales worse.
INDUSTRY_GAP = 0.01
# Earnings multiple the peers are compared on: "adj_pe" (last year's profit without one-time items, the same basis
# as the company's own estimate) or "pe" (as reported). On September 2026 data, with one-time charges added back as
# well as gains taken out, adjusted peers predicted each company's price more accurately (median absolute log error
# 0.420 vs 0.435 over 1,982 companies, difference -0.016 with a 95% interval of -0.031 to -0.002; the same in each
# half of the companies split by CIK) and with less bias (the median estimate 2.3% below the market, against 3.0%).
# Valuing both the company and its peers on adjusted rather than reported profit was more accurate still for the
# 566 profitable companies whose own profit was adjusted (0.491 vs 0.569, interval -0.123 to -0.030).
PEER_PE = "adj_pe"
# A peer missing a figure the company has counts as this far apart on it (squared gap in percentile rank, 0.3).
_MISSING_GAP = 0.09
# Nasdaq's catch-all groups hold unrelated companies, so they are matched against the whole market outside finance.
_CATCH_ALL = {"Other", "Miscellaneous"}
# Nasdaq's Finance sector mixes banks, insurers, lenders and brokers, whose sales mean different things, so a
# financial company is matched within its SEC industry group (the first two digits of its SIC code) when that
# group has more than _MIN_GROUP members. For price to sales of profitable financial companies whose sales pass
# sales_doubtful() (265), the median absolute log error was 0.34 to 0.38 for 6 to 15 peers, against 0.39 to 0.43
# matching across the whole sector and 0.79 with Nasdaq's industry labels. Four in five of group 61 are SIC 6199,
# "finance services" (card issuers, crypto miners and exchanges, neobanks), hence its long name.
FINANCIAL_GROUPS = {"60": "banks", "61": "lenders and other financial services companies",
                    "62": "brokers, exchanges and asset managers", "63": "insurers",
                    "64": "insurance brokers", "65": "real estate companies", "67": "holding and investment companies"}
_MIN_GROUP = 20
_SECTOR_WORDS = {"Industrials": "industrial companies", "Utilities": "utilities", "Finance": "financial companies"}
# REITs are compared only with REITs, on price to funds from operations (FFO) for those that own property (PEER_FFO) and
# on price to earnings (and book value, _lender) for the rest (reit_kind). Tested leave-one-out on September 2026 data
# (each REIT's price predicted from its peers' median multiple and its own FFO or earnings): for the 94 REITs that own
# property and were valued on price to earnings against their Nasdaq sector or SEC group before, price to FFO against
# REITs missed by a median absolute log error 0.21 lower (95% interval 0.12 to 0.28 lower): 0.225 for all 125 it values
# (a typical factor of 1.25, a median 6% low), against 0.411 on price to earnings (1.51, a median 22% low). It values
# 125 of the 139 instead of 98, since depreciation leaves many with a loss but not negative FFO. Their fair value
# midpoints missed by 0.166 instead of 0.276 on the 97 valued both ways (a difference of -0.11, interval -0.22 to
# -0.05), and came a median 0% from the price instead of 11% below. When first built, on the same data, plain FFO as the
# filings give it did no better than FFO without one-time items, and neither did adding depreciation or FFO margin to
# the matching figures, peer counts from 6 to 15, or leaving REITs out of other companies' candidates, so they stay in.
_MIN_REITS = 12
PEER_FFO = "p_ffo"
# Other methods for a REIT that owns property: a sales multiple ("ev_sales", or None for none) and a cash-flow model on
# its FFO (True or False). On September 2026 data, for the 125 REITs that own property and have positive FFO, peer
# value to sales missed their prices by a typical factor of 1.24 (0.214, on 117 whose debt could be read), and the
# midpoint of the two by 1.22 (0.197, 90% of REITs within a factor of 1.50), against 1.25 (0.225, 90% within 1.77) on
# price to FFO alone, which also called 53 overvalued and 31 undervalued, against 40 and 27. When first built, a
# cash-flow model on average FFO (which is after interest, so debt is not subtracted) ran a median 10% high, as FFO
# leaves out the spending that keeps buildings let, and left the midpoint no more accurate with wider tails, so it is
# not used. The cash-flow model on free cash flow REITs had before missed by 2.6, a median 57% low.
REIT_SALES = "ev_sales"
REIT_CASH_FLOW = False
_REIT_WORDS = {"property": "REITs that own property",
               "mortgage": "REITs that mostly lend or whose leases are booked as loans"}
# Added to the distance of a REIT from a candidate of another property type (reit_types), so peers come from its own
# type first and from the closest others where the type has too few. Chosen on September 2026 data from 0.02 to 0.1
# and strict pools of one type (worse, as some types have few REITs), and checked leave-one-out with the REITs split in
# half by CIK: the fair value midpoints missed the price by a median 0.211 and 0.180 in the two halves, against 0.211
# and 0.193 without it (all 125 valued: 0.197 against 0.203, a difference of -0.007 with a 95% interval of -0.053 to
# +0.021, so no more accurate on the whole). What it fixes is the lasting gap between types, since hotel REITs' sales
# include running the hotels and office REITs trade at half other REITs' multiples of FFO: without it hotel REITs'
# midpoints came a median 50% above the price, office REITs' 37% above and net lease and health care REITs' 11% and 10%
# below, with it 3% above, 28% above, and 5% and 5% below, and 20 REITs rather than 25 hit the 50% limit.
REIT_TYPE_GAP = 0.03
_TYPE_WORDS = {"office": "office REITs", "industrial": "industrial REITs", "retail": "retail REITs",
               "net_lease": "net lease REITs", "residential": "residential REITs", "health": "health care REITs",
               "lodging": "hotel REITs", "storage": "self storage REITs", "tech": "data center and tower REITs",
               "specialty": "specialty REITs", "diversified": "diversified REITs"}


# Banks (is_bank) are compared only with banks, matched on what drives a bank's value: its return on tangible common
# equity (ROTCE) and on assets, its efficiency ratio and its size (revenue). They are valued on their peers' median price
# to tangible book value and price to earnings (on earnings per share left to common shareholders, _bank_eps), as bank
# analysts value them, with no sales multiple and no cash-flow model, since a bank's cash flow mixes its lending and
# deposits. Tested on September 2026 data through this module's own functions, each bank's value worked out from peers
# that leave it out: for the 275 banks valued, the midpoint missed the price by a mean absolute log error of 0.136
# (median 0.099, 80% within 25%), against 0.194 (median 0.155, 66%) for peer price to sales and price to earnings with
# peers matched as other companies are, on the same corrected figures, which is how banks were valued before (a mean
# difference of -0.058, 95% interval -0.075 to -0.042; better for 176 banks, worse for 92; -0.056 and -0.060 in the two
# halves of the banks split by CIK). Alone, for the 263 banks with all three, price to tangible book missed by a mean
# 0.147 and price to earnings by 0.170, against 0.232 for price to sales (-0.085, -0.110 to -0.059), and price to
# tangible book with peers matched as other companies are by 0.184 (-0.037, -0.052 to -0.021). Earnings per share, where
# the share counts allow (_bank_eps), did better than market value over earnings (-0.007, -0.013 to -0.002). Adding the
# share of revenue from fees to the features, or valuing banks that earn mostly fees on earnings alone, did no measurably
# better (FEE_HEAVY). On the reports themselves, for the 110 banks the site valued before and still values, when a
# bank's fee income alone often read as its sales, the midpoints had missed by a mean 0.220 (61% within 25%), against
# 0.140 (78%) now (-0.080, -0.113 to -0.050; better for 73, worse for 35), and 275 banks are valued instead of 114.
# Insurers were tested the same way on price to book, and it did no better than price to sales (a median 0.288 against
# 0.298 for the midpoint with price to earnings over 77 insurers, a difference of -0.010 with a 95% interval of -0.075
# to +0.080), so they keep the methods other financial companies use. Nor can a combined ratio be read for them: of the
# 63 property and casualty or surety insurers (SIC 6331 and 6351), 60 tagged their earned premiums for 2025, 58 their
# losses and 51 their acquisition costs, but only 18 their other underwriting expenses.
BANK_FEATURES = ("rotce", "roa", "efficiency", "size")


class PeerTable(dict):
    """{symbol: row}, plus an index of which companies can be compared with which."""
    index = None


def sales_doubtful(m):
    """True when the sales figure read from the SEC data can't be the whole of the company's sales: operating
    profit, or profit without one-time items, came out larger than sales. In September 2026 data that flagged 90
    companies, 70 of them financial (a bank's fee income alone read as its sales, for example), with few just below
    the line (6 between 80% and 100%). Their sales multiples and margins would be far off, so the
    report neither shows nor uses them."""
    if m.get("bank"):
        # Measured on revenue without one-time securities gains and losses, which the profit leaves out too.
        rev, adj = m.get("adj_revenue"), m.get("adj_net_income")
        return bool(rev and rev > 0 and adj is not None and adj > rev)
    rev = m.get("revenue")
    return bool(rev and rev > 0 and ((m.get("op_margin") or 0) > 1 or (m.get("adj_net_margin") or 0) > 1))


DOUBT_NOTE = ("Its sales figure in the SEC data looks incomplete, since profit came out larger than sales, so it isn't "
              "valued on sales.")
# The same for a REIT whose debt could not be read (fundamentals._reit_debt), whose value to sales, which counts debt,
# fair_value leaves out.
DEBT_VALUE_NOTE = ("Its debt could not be read from its SEC filings, so it isn't valued on value to sales, which counts "
                   "debt.")
# A REIT whose debt could not be read from its SEC data (fundamentals._reit_debt): its debt measures are left out, and
# so is the value to sales method, which counts debt.
DEBT_UNKNOWN_NOTE = ("Not measured. This REIT's debt could not be read from its SEC filings, which list most of its "
                     "liabilities under names this site doesn't read.")
DOUBT_BAR_NOTE = ("Not measured. The sales figure in the company's SEC data looks incomplete, since profit came out "
                  "larger than sales.")
# Any other company whose debt read from its SEC data looks far too small (fundamentals._debt_check, which gives the
# reason as debt_doubt): fair_value leaves out the methods that count its debt (value to sales and the cash-flow model),
# and its health check the debt measures.
DEBT_DOUBT_VALUE_NOTE = ("Its debt could not be read reliably from its filings, so methods that depend on debt are left "
                         "out.")
DEBT_DOUBT_BAR_NOTES = {
    "interest": ("Not measured. Its debt could not be read reliably from its filings: the debt found is far too small "
                 "for the interest it paid last year."),
    "liabilities": ("Not measured. Its debt could not be read reliably from its filings: the debt found is a small part "
                    "of what it owes beyond the next year."),
}


def _debt_bar_note(m):
    """The health check's note for a debt measure left out because the debt could not be read (debt_known False)."""
    return DEBT_DOUBT_BAR_NOTES.get(m.get("debt_doubt"), DEBT_UNKNOWN_NOTE)


# The most a single valuation method may put the value above the price (or below it, as a fraction) before fair_value
# takes it for a data error and leaves it out. On September 2026 data that dropped 142 methods at 108 companies
# (Cheer Holding's value to sales at 165 times its $1.60 price, CISS's price to earnings at 100 times, Live Ventures'
# cash-flow model at 12 times), and left 45 of them with no method at all.
METHOD_SANITY = 10


def _fin_group(code):
    """Peer group key for a financial company's SIC code, or None."""
    return code[:2] if code[:2] in FINANCIAL_GROUPS else None


def peer_table(universe, metrics, sic=None):
    """Ratios and matching figures for every company. `sic` is {cik: SIC code} for financial companies."""
    table = PeerTable()
    sic = sic or {}
    for sym, u in universe.items():
        m = metrics.get(sym)
        if not m or not u.get("mcap"):
            continue
        # A bank's revenue without one-time securities gains and losses (fundamentals.derive), which can take the
        # year's revenue below nothing (Horizon Bancorp's 2025, with a $300M loss on selling securities).
        rev = m.get("adj_revenue" if m.get("bank") else "revenue")
        ni, adj = m.get("net_income"), m.get("adj_net_income")
        fin, kind = financial(u, m), reit_kind(m)
        # The value of the whole business behind the sales. A REIT's market value counts only its listed shares,
        # while its operating partnership's other unitholders own the rest of the sales and debt (_whole).
        whole = u["mcap"] / _whole(m)
        # Unknown where a REIT's debt could not be read (fundamentals._reit_debt).
        ev = None if fin or m.get("net_cash") is None or m.get("debt_known") is False else whole - m["net_cash"]
        doubt = sales_doubtful(m)
        has_rev = bool(rev and rev > 0) and not doubt
        assets = m.get("total_assets")
        code = str(sic.get(u.get("cik")) or "")
        ffo = m.get("adj_ffo") if kind == "property" else None
        bank = bool(m.get("bank"))
        tce, common, eps = m.get("tangible_equity"), m.get("adj_net_income_common"), _bank_eps(u, m)
        # A bank's price to earnings is on its earnings per diluted share left to common shareholders (BANK_FEATURES)
        # where those shares are the ones its price is quoted on, else its market value over those earnings.
        bank_pe = (u["price"] / eps if eps else u["mcap"] / common if common and common > 0 else None) if bank else None
        table[sym] = {
            "name": u.get("name"),
            "cik": u.get("cik"),
            "industry": u["industry"],
            "sector": u["sector"],
            "fin": fin,
            # The listed shares' part of the business (_whole), below 1 where an operating partnership's other
            # unitholders own the rest, so price to sales counts the whole business.
            "owned": _whole(m),
            # A REIT's kind (reit_kind), which sets its peer pool, and for one that owns property its price to funds
            # from operations and the FFO measures its health check ranks against other such REITs.
            "reit": kind,
            # Its property type (reit_types, reit_type), which its peers are preferably drawn from, and its price to
            # book value, which values a REIT that lends.
            "ptype": m.get("reit_type") if kind else None,
            "pb": u["mcap"] / m["equity"] if kind == "mortgage" and (m.get("equity") or 0) > 0 else None,
            "group": _fin_group(code) if is_financial(u) else None,
            "sales_doubt": doubt,
            "ps": whole / rev if has_rev else None,
            "ev_sales": ev / rev if ev and ev > 0 and has_rev else None,
            "pe": u["mcap"] / ni if ni and ni > 0 else None,
            "adj_pe": bank_pe if bank else u["mcap"] / adj if adj and adj > 0 else None,
            "p_ffo": u["mcap"] / ffo if ffo and ffo > 0 else None,
            # A bank (is_bank): its price to tangible book value, and the measures its peers are matched on
            # (BANK_FEATURES, ranked among banks as "bank_rank") and its health check ranks among all banks.
            "bank": bank,
            "ptbv": u["mcap"] / tce if bank and tce and tce > 0 else None,
            "bank_features": {"rotce": m.get("adj_rotce"), "roa": m.get("adj_roa"),
                              "efficiency": m.get("efficiency_ratio"),
                              "size": math.log(rev) if has_rev else None} if bank else None,
            "roe_common": m.get("adj_roe_common") if bank else None,
            "tce_ratio": m.get("tce_ratio") if bank else None,
            "assets": assets if bank else None,
            "ffo_margin": m.get("adj_ffo_margin") if kind == "property" and has_rev else None,
            "ffo_roe": m.get("adj_ffo_roe") if kind == "property" and not _thin_equity(m) else None,
            "fcf_yield": m["fcf"] / u["mcap"] if m.get("fcf") is not None else None,
            "net_margin": m.get("net_margin"),
            "growth": m.get("revenue_growth"),
            # A sales figure that can't be right leaves nothing to match on, so the company falls back to its
            # Nasdaq industry label and is never another company's peer.
            "features": {
                "op_margin": m.get("op_margin") if has_rev else None,
                "net_margin": m.get("adj_net_margin") if has_rev else None,
                "fcf_margin": m["fcf"] / rev if has_rev and m.get("fcf") is not None else None,
                "growth": m.get("revenue_growth") if has_rev else None,
                "growth_3y": m.get("revenue_cagr") if has_rev else None,
                "size": math.log(rev) if has_rev else None,
                "turnover": rev / assets if has_rev and assets and assets > 0 else None,
            },
        }
        if u.get("price") and _share_count_gap(u, m, u["price"]):
            # Its price and its figures describe different share counts, so it has no multiples to compare.
            table[sym].update(ps=None, ev_sales=None, pe=None, adj_pe=None, p_ffo=None, pb=None, fcf_yield=None,
                              ptbv=None, shares_gap=True)
        if m.get("debt_known") is False:
            # A REIT whose debt could not be read (fundamentals._reit_debt), or another company whose debt read looks
            # far too small (fundamentals._debt_check).
            table[sym]["debt_unknown"] = True
    # Each figure becomes a percentile rank across all companies, so margins, growth and size weigh the same
    # and a few extreme values can't dominate the distance. Banks, which are matched among themselves on their own
    # measures (ranked among banks), are left out of the ranks other companies are matched on, so how a bank's figures
    # are read never moves another company's peers. That took out 115 banks whose fee income had read as their sales:
    # of the 2,281 other companies valued on September 2026 data, 22 changed verdict, and their midpoints missed their
    # prices by a mean absolute log error of 0.443, against 0.444 with those banks in (median 0.405 both ways).
    for f in PEER_FEATURES:
        vals = sorted(r["features"][f] for r in table.values() if r["features"][f] is not None and not r["bank"])
        for r in table.values():
            v = r["features"][f]
            r.setdefault("rank", []).append(
                None if v is None else (bisect.bisect_left(vals, v) + bisect.bisect_right(vals, v)) / 2 / len(vals))
    banks = [r for r in table.values() if r["bank"]]
    for f in BANK_FEATURES:
        vals = sorted(r["bank_features"][f] for r in banks if r["bank_features"][f] is not None)
        for r in banks:
            v = r["bank_features"][f]
            r.setdefault("bank_rank", []).append(
                None if v is None else (bisect.bisect_left(vals, v) + bisect.bisect_right(vals, v)) / 2 / len(vals))
    return table


def _index(table):
    idx = getattr(table, "index", None)
    if idx is None:
        idx = {"sector": {}, "group": {}, "industry": {}, "reit": {},
               # The whole market, for companies in Nasdaq's catch-all sectors. Financial companies are left out:
               # their sales mean something else, and no value to sales is worked out for them.
               "market": [s for s, r in table.items() if r["sector"] != FINANCIAL_SECTOR],
               "bank": [s for s, r in table.items() if r.get("bank")]}
        for s, r in table.items():
            idx["sector"].setdefault(r["sector"], []).append(s)
            idx["industry"].setdefault(r["industry"], []).append(s)
            if r.get("group"):
                idx["group"].setdefault(r["group"], []).append(s)
            if r.get("reit"):
                idx["reit"].setdefault(r["reit"], []).append(s)
        if isinstance(table, PeerTable):
            table.index = idx
    return idx


def _pool(me, idx):
    """(candidate symbols, how they were chosen) for one company. A REIT is compared with REITs of its kind
    (reit_kind) when there are more than _MIN_REITS of them, and a bank with banks when there are more than _MIN_GROUP."""
    k = me.get("reit")
    if k and len(idx["reit"].get(k, ())) > _MIN_REITS:
        return idx["reit"][k], ("reit", k)
    if me.get("bank") and len(idx["bank"]) > _MIN_GROUP:
        return idx["bank"], ("bank", None)
    g = me.get("group")
    if g and len(idx["group"].get(g, ())) > _MIN_GROUP:
        return idx["group"][g], ("group", g)
    if me["sector"] in _CATCH_ALL:
        return idx["market"], ("market", None)
    return idx["sector"].get(me["sector"], []), ("sector", me["sector"])


def _one_per_company(symbols, table, own_cik):
    """The symbols in order, skipping the company's own share classes and any second class of a company already
    listed, so no business counts twice."""
    seen, out = {own_cik}, []
    for s in symbols:
        c = table[s]["cik"]
        if c in seen:
            continue
        if c is not None:
            seen.add(c)
        out.append(s)
    return out


def _label_peers(me, table, idx):
    same = _one_per_company(idx["industry"].get(me["industry"], []), table, me["cik"])
    if len(same) >= 8:
        return same, me["industry"]
    return _one_per_company(idx["sector"].get(me["sector"], []), table, me["cik"]), me["sector"]


def peers_for(sym, table, count=PEER_COUNT):
    """(peer symbols, closest first; the group they were drawn from). A profitable company is compared only with
    profitable companies, so every peer has an earnings multiple. A company without sales has nothing to match
    on, so it falls back to Nasdaq's industry label (or its sector when fewer than 8 companies share the label)."""
    me = table.get(sym)
    if not me:
        return [], None
    idx = _index(table)
    if me["features"]["size"] is None:
        return _label_peers(me, table, idx)
    pool, (kind, key) = _pool(me, idx)
    # Profitable as fair_value() counts it: without last year's one-time items, and for a REIT that owns property,
    # with positive funds from operations.
    earn = _earn_key(me)
    profitable = me.get(earn) is not None
    # A bank is matched on its own measures (BANK_FEATURES), ranked among banks.
    ranks = "bank_rank" if kind == "bank" else "rank"
    mine = me.get(ranks) or []
    scored = []
    for s in pool:
        r = table[s]
        if r["cik"] == me["cik"] or r["features"]["size"] is None or (profitable and r.get(earn) is None):
            continue
        d = n = 0.0
        for a, b in zip(mine, r.get(ranks) or []):
            if a is not None:
                d += (a - b) ** 2 if b is not None else _MISSING_GAP
                n += 1
        dist = d / n if n else 1.0
        if r["industry"] != me["industry"] and kind not in ("group", "reit", "bank"):
            dist += INDUSTRY_GAP
        if kind == "reit" and key == "property" and me.get("ptype") and r.get("ptype") != me["ptype"]:
            dist += REIT_TYPE_GAP
        scored.append((dist, s))
    scored.sort()
    group = (_group_name(key).capitalize() if kind == "group" else "All US stocks" if kind == "market"
             else _REIT_WORDS[key][0].upper() + _REIT_WORDS[key][1:] if kind == "reit" else "Banks" if kind == "bank"
             else key)
    return _one_per_company([s for _, s in scored], table, me["cik"])[:count], group


def _earn_key(me):
    """The earnings multiple a company is valued and matched on: price to FFO for a REIT that owns property, else
    price to earnings."""
    return PEER_FFO if me.get("reit") == "property" else PEER_PE


def _group_name(key):
    return FINANCIAL_GROUPS[key]


def peer_basis(sym, table, peers):
    """One plain sentence on how the peers were chosen, or None without peers."""
    me = table.get(sym)
    if not me or not peers:
        return None
    idx = _index(table)
    if me["features"]["size"] is None:
        _, label = _label_peers(me, table, idx)
        why = ("Its sales figure in the SEC data looks incomplete, so it can't be matched on its numbers."
               if me.get("sales_doubt") else "Without sales figures, the company can't be matched on its numbers.")
        return f"Compared with the {len(peers)} other companies Nasdaq lists under {label}. {why}"
    _, (kind, key) = _pool(me, idx)
    who = (_group_name(key) if kind == "group" else "US-listed companies outside finance" if kind == "market"
           else _REIT_WORDS[key] if kind == "reit" else "banks" if kind == "bank"
           else _SECTOR_WORDS.get(key, f"{key.lower()} companies"))
    if kind == "bank":
        f = dict(zip(BANK_FEATURES, me["bank_rank"]))
        traits = [t for t, k in (("return on tangible equity", "rotce"), ("return on assets", "roa"),
                                 ("costs per dollar of revenue", "efficiency"), ("size", "size")) if f[k] is not None]
    else:
        f = dict(zip(PEER_FEATURES, me["rank"]))
        traits = [t for t, keys in (("profit margins", ("op_margin", "net_margin")), ("cash flow", ("fcf_margin",)),
                                    ("sales growth", ("growth", "growth_3y")), ("size", ("size",)),
                                    ("sales per dollar of assets", ("turnover",))) if any(f[k] is not None for k in keys)]
    listed = traits[0] if len(traits) == 1 else ", ".join(traits[:-1]) + " and " + traits[-1]
    earn = _earn_key(me)
    profitable = "" if me.get(earn) is None else "FFO-positive " if earn == PEER_FFO else "profitable "
    prefer = (", with a preference for its own industry" if INDUSTRY_GAP and kind not in ("group", "reit", "bank") else
              f", with a preference for {_TYPE_WORDS[me['ptype']]}" if kind == "reit" and key == "property"
              and me.get("ptype") in _TYPE_WORDS
              else "")
    return f"Compared with the {len(peers)} {profitable}{who} closest to it in {listed}{prefer}."


def peer_list(sym, table, peers, limit=PEER_COUNT):
    """The peers as shown on the report: ticker, name and the multiples their medians come from ("ps" too for a
    non-financial company, whose health check ranks price to sales; "ptbv" and "pe" only for a bank). Empty for a
    company matched only by its industry label, whose peer list is the whole label rather than a closest few."""
    me = table.get(sym)
    if not me or me["features"]["size"] is None:
        return []
    if _bank_peers(me, table):
        # A bank's peers show price to tangible book value ("ptbv") in place of a sales multiple.
        return [{"symbol": s, "name": table[s]["name"], "ptbv": table[s]["ptbv"], "pe": table[s]["adj_pe"]}
                for s in peers[:limit]]
    key, earn = _sales_key(me), _earn_key(me)
    return [{"symbol": s, "name": table[s]["name"], "sales": table[s][key], "pe": table[s].get(earn),
             **({} if key == "ps" else {"ps": table[s]["ps"]})}
            for s in peers[:limit]]


def _sales_key(me):
    # The sales multiple fair_value() uses: price to sales for financial companies, value to sales otherwise.
    return "ps" if me.get("fin", me["sector"] == FINANCIAL_SECTOR) else "ev_sales"


def _bank_peers(me, table):
    """Whether a company's peers were drawn from banks (_pool), which values it the way bank analysts do."""
    return bool(me.get("bank")) and _pool(me, _index(table))[1][0] == "bank"


BANK_TBV_PARTS = "shareholders' equity less goodwill, other intangible assets and preferred stock"
# Where last year's provision for loan losses jumped in an acquisition year (_provision_spike "acquired").
BANK_SPIKE_NOTE = (" Last year's earnings were held down by the allowance for loan losses the bank had to set aside at once "
                   "for the loans it bought.")
# Where the bank's preferred stock can't be read (fundamentals._bank_fields' tce_unread "preferred").
BANK_PREFERRED_NOTE = ("Not measured. The bank pays dividends on preferred stock, but its SEC filings don't give how much "
                       "preferred stock it has, which belongs to preferred shareholders rather than common ones.")
BANK_TBV_WORDS = f"tangible book value ({BANK_TBV_PARTS})"


def _bank_peer_multiples(me, rows):
    """peer_multiples() for a bank: price to tangible book value and price to earnings."""
    profitable = me.get("adj_pe") is not None
    note = ["Price to tangible book compares the share price with " + BANK_TBV_WORDS + ", which is what bank investors "
            "value a bank on. Earnings are per share left to common shareholders, without last year's one-time items."]
    if profitable:
        note.append("The fair value, where one is shown, uses the median of both columns. Banks aren't valued on sales or "
                    "cash flow, which for a bank mix its lending and its customers' deposits.")
    else:
        note.append("The fair value doesn't use them, since the bank lost money last year.")
    gaps = (["in price to tangible book it means tangible book value below zero or not readable"]
            if any(r["ptbv"] is None for r in rows + [me]) else [])
    if me.get("adj_pe") is None or any(r.get("adj_pe") is None for r in rows):
        gaps.append("in price to earnings it means a loss")
    if gaps:
        note.append("Where a cell says n/a, " + " and ".join(gaps) + ".")
    return {
        "median": {"ptbv": _median([r["ptbv"] for r in rows]), "pe": _median([r.get("adj_pe") for r in rows])},
        "ptbv_label": "Price to tangible book",
        "pe_label": "Price to earnings",
        "note": " ".join(note),
        "company": {"ptbv": me["ptbv"], "pe": me.get("adj_pe")},
    }


def peer_multiples(sym, table, peers, dropped=()):
    """Column labels, the company's own multiples and the peer medians fair_value() uses, for the peer list on
    the report, or None without a list. A non-financial company also gets a price to sales column, since its
    health check ranks price to sales while its fair value uses value to sales. A bank gets price to tangible book
    ("ptbv", ptbv_label) and price to earnings, and no sales column or sales_label (_bank_peer_multiples). `dropped`
    names the methods fair_value() left out as data errors (METHOD_SANITY), which the note then says."""
    me = table.get(sym)
    if not me or me["features"]["size"] is None or not peers:
        return None
    if _bank_peers(me, table):
        return _bank_peer_multiples(me, [table[s] for s in peers])
    key, earn = _sales_key(me), _earn_key(me)
    fin = key == "ps"
    reit = earn == PEER_FFO
    profitable = me.get(earn) is not None
    rows = [table[s] for s in peers]
    note = []
    if not fin:
        note.append(("Price to sales, which the health check ranks, counts the whole business, the part its "
                     "operating partnership's other unitholders own included, and not its debt and cash."
                     if me.get("owned", 1.0) < 0.995 else
                     "Price to sales, which the health check ranks, looks at the share price alone.")
                    + " Value to sales, which the fair value uses, also counts each company's debt and cash.")
    if reit:
        note.append("Price to FFO compares the price with funds from operations (FFO), the profit measure REITs "
                    "report: net income with depreciation added back and gains on selling property left "
                    "out, here also without other one-time items.")
    elif PEER_PE == "adj_pe":
        note.append("Earnings leave out last year's one-time items.")
    elif me.get("pe") != me.get("adj_pe"):
        note.append("Peers' earnings are as reported. This company's leave out last year's one-time items, as its "
                    "fair value does.")
    earn_words = "price to FFO" if reit else "price to earnings"
    if profitable and (REIT_SALES or not reit) and me.get("reit") != "mortgage" and not me.get("debt_unknown"):
        note.append("The fair value uses the median of the " + ("price to sales" if fin else "value to sales")
                    + f" and {earn_words} columns.")
    elif profitable:
        note.append(f"The fair value uses the median of the {earn_words} column"
                    + (" and the same REITs' median price to book value, which the table doesn't show."
                       if me.get("reit") == "mortgage" and me.get("pb") and not me.get("ptype") else
                       # fair_value leaves value to sales out where the company's own debt could not be read.
                       f" alone, since {'this REIT' if reit else 'its'} debt could not be read"
                       + ("" if reit else " reliably") + " and value to sales counts debt."
                       if me.get("debt_unknown") and not fin else "."))
    else:
        note.append("These are the companies the price to sales bar in the health check compares it with. The fair "
                    "value doesn't use them, since the company "
                    + ("had negative FFO." if reit else "lost money."))
    # A column the sentence above names whose result fair_value left out as a data error (METHOD_SANITY).
    cols = [c for name, c in (("Peer value to sales", "value to sales"), ("Peer price to sales", "price to sales"),
                              ("Peer price to earnings", "price to earnings"), ("Peer price to FFO", "price to FFO"))
            if profitable and name in dropped and c in note[-1]]
    if cols:
        note.append(f"The {' and '.join(cols)} result{'s' if len(cols) > 1 else ''} came out more than "
                    f"{METHOD_SANITY} times the price or under a tenth of it, which more likely means a misread "
                    f"figure, so {'they are' if len(cols) > 1 else 'it is'} left out of the fair value.")
    # What an n/a cell means. Peers are matched on their sales, so every row has sales; a sales multiple is blank where
    # the share count changed after the company's latest filing (_share_count_gap), and value to sales also where the
    # debt could not be read (a REIT), for a lender (not valued on it) or where cash exceeds the market value.
    gaps, everyone = [], [r for r in rows + [me] if not r.get("shares_gap")]
    if not fin:
        why = [text for text, hit in (
            ("the company holds more cash than its market value",
             any(r["ev_sales"] is None and not (r.get("debt_unknown") or r.get("fin")) for r in everyone)),
            ("its debt could not be read", any(r.get("debt_unknown") for r in everyone)),
            ("it is valued like a lender rather than on sales", any(r.get("fin") for r in everyone))) if hit]
        if why:
            gaps.append("in value to sales it means " + (why[0] if len(why) == 1 else
                                                          "one of these: " + ", ".join(why[:-1]) + " or " + why[-1]))
    if any(r.get(earn) is None for r in everyone):
        gaps.append("in price to FFO it means negative FFO" if reit else "in price to earnings it means a loss")
    if gaps:
        # Several reasons for one column read more clearly as a sentence of their own.
        note.append("Where a cell says n/a, " + (". ".join(g[0].upper() + g[1:] if n else g for n, g in enumerate(gaps))
                                                 if "one of these" in gaps[0] else " and ".join(gaps)) + ".")
    if any(r.get("shares_gap") for r in rows + [me]):
        note.append("A row that is n/a throughout is a company whose share count changed after its latest SEC filing, "
                    "so its price and its figures can't be compared.")
    return {
        "median": {"sales": _median([r[key] for r in rows]), "pe": _median([r.get(earn) for r in rows]),
                   **({} if fin else {"ps": _median([r["ps"] for r in rows])})},
        "sales_label": "Price to sales" if fin else "Value to sales",
        "pe_label": "Price to FFO" if reit else "Price to earnings",
        **({} if fin else {"ps_label": "Price to sales"}),
        "note": " ".join(note),
        "company": {"sales": me[key], "pe": me.get(earn), **({} if fin else {"ps": me["ps"]})},
    }


def _median(values):
    """Median of at least 5 values, else None. (Trimming the same number of values from each end, as this once
    did, never moves a median.)"""
    v = [x for x in values if x is not None]
    return statistics.median(v) if len(v) >= 5 else None


def _ffo_multiple_bar(bar, m, me, peer_rows):
    """The price to FFO bar of a REIT that owns property, ranked against its peers (lower is better)."""
    label = "Price to FFO (the REIT version of price to earnings)"
    if m.get("adj_ffo") is not None and m["adj_ffo"] <= 0:
        return bar("p_ffo", label, "FFO loss", 5, "Its funds from operations were negative last year, so there are no "
                   "earnings to value.")
    x = me.get(PEER_FFO)
    s = _pct_rank(x, [r.get(PEER_FFO) for r in peer_rows], lower_is_better=True)
    return bar("p_ffo", label, multiple(x), s,
               ("Not enough data to compare." if s is None else
                f"You pay less per dollar of FFO than for {s}% of similar REITs." if s >= 50 else
                f"You pay more per dollar of FFO than for {100 - s}% of similar REITs.")
               + " FFO (funds from operations) is the profit measure REITs report: net income with depreciation "
               "added back and gains on selling property left out.")


# Book equity under this share of total assets (a median 45% for REITs that own property) leaves a REIT's return on
# equity unscored (_ffo_profit_bars): on September 2026 data the 15 REITs below it showed returns from -34% to 244%
# (Strawberry Fields, whose total equity is 4% of its assets; Simon Property's 78%), which rank their accounting
# rather than their business.
REIT_THIN_EQUITY = 0.15


def _thin_equity(m):
    e = m.get("ffo_equity_to_assets")
    return e is not None and 0 < e < REIT_THIN_EQUITY


def _ffo_profit_bars(bar, no_sales, m, me, table, doubt):
    """A REIT's profitability bars on funds from operations: FFO per dollar of sales and FFO on shareholders' equity.
    Both are ranked against every REIT that owns property, as a share of them it beats. FFO margins run from about 15%
    for hotel REITs, which run the hotels, to 60% and more for landlords whose tenants pay the property costs (a median
    35% on September 2026 data), well above any company's net margin, so the scales used for other companies would
    score nearly every REIT 100. Book equity is net of years of depreciation, which FFO leaves out, so it flatters every
    REIT's return on equity (a median 11%, against 3% for other companies' net income), and it too is ranked among
    REITs."""
    idx = _index(table)
    if "ffo_margin" not in idx:
        rows = [r for r in table.values() if r.get("reit") == "property"]
        idx["ffo_margin"] = [r["ffo_margin"] for r in rows if r.get("ffo_margin") is not None]
        idx["ffo_roe"] = [r["ffo_roe"] for r in rows if r.get("ffo_roe") is not None]
    fm, roe = me.get("ffo_margin"), me.get("ffo_roe")
    s = _pct_rank(fm, idx["ffo_margin"], lower_is_better=False)
    out = [no_sales("ffo_margin", "FFO margin") if doubt else bar(
        "ffo_margin", "FFO margin", pct(fm), s,
        "No revenue data." if fm is None else
        (f"Keeps {cents(fm)} of FFO from every dollar of sales" if fm >= 0
         else f"Loses {cents(fm)} of FFO on every dollar of sales")
        + ("." if s is None else
           f", {'more' if s >= 50 else 'less'} than {s if s >= 50 else 100 - s}% of REITs that own property."))]
    if _thin_equity(m):
        out.append({**bar("ffo_roe", "Return on equity (FFO)", "n/a", None,
                          f"Not measured. Its book equity is under {REIT_THIN_EQUITY * 100:.0f}% of its assets, worn "
                          "down by years of depreciation and payouts, so a return on it would mean little."),
                    "band": "neutral"})
        return out
    s = _pct_rank(roe, idx["ffo_roe"], lower_is_better=False)
    out.append(bar("ffo_roe", "Return on equity (FFO)", pct(roe), s,
                   "Can't be measured because shareholder equity is zero or negative." if roe is None else
                   "FFO earned on the money shareholders have in the business" + (
                       "." if s is None else f", {'more' if s >= 50 else 'less'} than {s if s >= 50 else 100 - s}% of "
                       "REITs that own property. It is ranked among REITs only, since depreciation shrinks their book "
                       "equity.")))
    return out


# How many banks nearest in size (total assets) a bank's returns, efficiency and capital are ranked among (_bank_bars),
# as bank supervisors and analysts compare banks with others of their size: on September 2026 data the quarter of banks
# with the least assets (under $2.4B) had a median 10.3% of tangible equity to tangible assets and a 70% efficiency
# ratio, the largest quarter 8.4% and 60%, and the 21 banks with over $100B 7.1%, so a large bank ranked among all banks
# would look thinly capitalized for its size alone.
BANK_COHORT = 60


def _bank_cohort(me, table):
    """{measure: values} of the BANK_COHORT banks nearest `me` in total assets, itself left out (_bank_bars)."""
    idx = _index(table)
    if "bank_sizes" not in idx:
        idx["bank_sizes"] = sorted((math.log(table[s]["assets"]), s) for s in idx["bank"]
                                   if (table[s].get("assets") or 0) > 0)
    sizes = idx["bank_sizes"]
    if not me.get("assets") or me["assets"] <= 0:
        rows = [table[s] for s in idx["bank"] if table[s]["cik"] != me["cik"]]
    else:
        x = math.log(me["assets"])
        near = sorted(sizes, key=lambda t: abs(t[0] - x))
        rows = [table[s] for _, s in near if table[s]["cik"] != me["cik"]][:BANK_COHORT]
    return {k: [r[k] if k in r else r["bank_features"][k] for r in rows] for k in
            ("roe_common", "roa", "efficiency", "tce_ratio")}


def _beat(s, better, worse, who):
    """"more than 77% of the 60 banks...", from a percentile rank `s` (_pct_rank): the share of `who` a bank beats, or
    trails where that is under half, and "all" of them at either end rather than 100% or 99%."""
    if s >= 100 or s <= 1:
        return f"{better if s >= 100 else worse} than all {who}"
    return f"{better if s >= 50 else worse} than {s if s >= 50 else 100 - s}% of the {who}"


def _bank_bars(bar, m, me, peer_rows, table, adjusted):
    """A bank's valuation, profitability and balance sheet bars ({group: bars}), which replace the measures that mislead
    for a bank: price to sales and value to sales (a bank's revenue is interest and fees on money it mostly borrowed from
    depositors), free cash flow (which mixes its lending with its deposits), profit margin, and its net cash, debt to
    equity and short-term cushion (deposits are its main borrowing by design, and banks don't sort their balance sheets
    by when things come due). In their place are the measures bank analysts use. Price to tangible book value is ranked
    against the bank's peers, as other multiples are; return on equity and on assets, the efficiency ratio and tangible
    equity to assets are ranked among the banks nearest it in size (BANK_COHORT), as a share of them the bank beats,
    since what is normal for a bank (a return on assets near 1%, equity near a tenth of assets) would score poorly on the
    scales used for other companies. The notes name the median of those banks."""
    pop = _bank_cohort(me, table)
    mid = lambda k: statistics.median([x for x in pop[k] if x is not None]) if any(x is not None for x in pop[k]) else None
    them = f"{BANK_COHORT} banks closest to it in size"
    out = {"Valuation": [], "Profitability": [], "Balance sheet": []}

    x = me.get("ptbv")
    tce = m.get("tangible_equity")
    rivals = [r.get("ptbv") for r in peer_rows if r.get("ptbv") is not None]
    s = _pct_rank(x, rivals, lower_is_better=True)
    what = f" Tangible book value is {BANK_TBV_PARTS}."
    # Why tangible book value is unknown (fundamentals._bank_fields' tce_unread).
    unread = (BANK_PREFERRED_NOTE if m.get("tce_unread") == "preferred" else
              "Not measured. Its tangible book value could not be read from its SEC filings.")
    if tce is not None and tce <= 0:
        out["Valuation"].append(bar("ptbv", "Price to tangible book", "n/a", 5, "Its tangible book value is below zero: "
                                    "goodwill and other intangible assets exceed what common shareholders own." + what))
    elif x is None:
        out["Valuation"].append({**bar("ptbv", "Price to tangible book", "n/a", None, unread + what), "band": "neutral"})
    else:
        out["Valuation"].append(bar("ptbv", "Price to tangible book", multiple(x), s,
                                    ("Not enough data to compare." if s is None else
                                     f"{_beat(s, 'Cheaper', 'Pricier', f'{len(rivals)} similar banks')} for each "
                                     "dollar of tangible book value.") + what))

    one_time = (" Leaves out last year's one-time items." if adjusted else "") + (
        BANK_SPIKE_NOTE if _provision_spike(m) == "acquired" else "")
    roe = me.get("roe_common")
    s = _pct_rank(roe, pop["roe_common"], lower_is_better=False)
    out["Profitability"].append(bar(
        "roe", "Return on equity", pct(roe), s,
        ("Can't be measured because shareholder equity is zero or negative." if (m.get("equity") or 0) <= 0 else
         BANK_PREFERRED_NOTE + " So the equity that belongs to common shareholders is unknown."
         if m.get("tce_unread") == "preferred" else
         "Not measured. Its equity at the end of the year could not be read from its SEC filings.") if roe is None else
        "Profit left to common shareholders on the money they have invested in the bank (its common equity)"
        + ("." if s is None else ", " + _beat(s, "more", "less", them))
        + (f" (their median: {pct(mid('roe_common'))})." if s is not None else "") + one_time))
    roa = me["bank_features"].get("roa")
    s = _pct_rank(roa, pop["roa"], lower_is_better=False)
    out["Profitability"].append(bar(
        "roa", "Return on assets", pct(roa, 2), s,
        "Not enough data." if roa is None else
        "Profit on everything the bank owns, mostly loans and bonds"
        + ("." if s is None else ", " + _beat(s, "more", "less", them))
        + (f" (their median: {pct(mid('roa'), 2)}). A bank earns a thin margin on a large balance sheet, so a "
           "return near 1% is normal." if s is not None else "") + one_time))
    eff = me["bank_features"].get("efficiency")
    s = _pct_rank(eff, pop["efficiency"], lower_is_better=True)
    out["Profitability"].append(bar(
        "efficiency", "Efficiency ratio", pct(eff, 0), s,
        "Not enough data." if eff is None else
        f"Spends {cents(eff)} running the bank for every dollar of revenue"
        + ("." if s is None else ", " + _beat(s, "less", "more", them))
        + (f" (their median: {cents(mid('efficiency'))}). Lower is better." if s is not None else "")))

    if (m.get("equity") or 0) <= 0 and m.get("equity") is not None:
        out["Balance sheet"].append(bar("de", "Debt to equity", "Negative equity", 3,
                                        "The bank owes more than it owns. That is a serious warning sign."))
    ratio = me.get("tce_ratio")
    s = _pct_rank(ratio, pop["tce_ratio"], lower_is_better=False)
    out["Balance sheet"].append(bar(
        "tce_ratio", "Tangible equity to assets", pct(ratio), s,
        unread if ratio is None else
        "Common shareholders' tangible equity as a share of the bank's tangible assets"
        + ("." if s is None else ", " + _beat(s, "more", "less", them))
        + (f" (their median: {pct(mid('tce_ratio'))})." if s is not None else "")
        + " It is the cushion that absorbs losses on loans before depositors and lenders are at risk."))
    return out


BANK_HINTS = {
    "Valuation": "Is the price reasonable for what you get? Banks are valued on their tangible book value and earnings, "
                 "not on sales or cash flow.",
    "Balance sheet": "Could it absorb losses on its loans? A bank borrows from depositors by design, so the debt and cash "
                     "measures used for other companies don't apply.",
}


def health(u, m, price, high52, peers, table, news_overall, insider):
    """Returns grouped bars. Each: key, label, value (display), score 1-100, note (plain English)."""
    mcap = u.get("mcap")
    peer_rows = [table[s] for s in peers]
    me = table.get(u["symbol"], {})
    groups = []

    fin = financial(u, m)

    def bar(key, label, value, score, note, context=False):
        # Context bars are neither good nor bad, so they are drawn in neutral gray.
        return {"key": key, "label": label, "value": value, "score": score,
                "band": "neutral" if context else band(score), "note": note, "context": context}

    def not_scored(key, label, note=FINANCIAL_NOTE):
        return {**bar(key, label, "n/a", None, note), "band": "neutral"}

    # A sales figure that can't be right would put every sales-based bar far off, so those are left out.
    doubt = sales_doubtful(m)
    no_sales = lambda key, label: not_scored(key, label, DOUBT_BAR_NOTE)
    # A bank matched with banks gets the measures bank analysts use (_bank_bars).
    banked = _bank_bars(bar, m, me, peer_rows, table, bool(m.get("one_time"))) if _bank_peers(me, table) else None

    # Valuation
    v = []
    ps = me.get("ps")
    s = _pct_rank(ps, [r["ps"] for r in peer_rows], lower_is_better=True)
    if banked:
        v += banked["Valuation"]
    else:
        v.append(no_sales("ps", "Price to sales") if doubt else bar("ps", "Price to sales", multiple(ps), s,
                     "Not enough data to compare." if s is None else
                     f"Cheaper than {s}% of similar companies for each dollar of sales." if s >= 50 else
                     f"Pricier than {100 - s}% of similar companies for each dollar of sales."))
    pe = me.get("adj_pe")
    adjusted = bool(m.get("one_time"))
    reit = reit_kind(m) == "property"
    # A bank's earnings are its common shareholders' (fundamentals._bank_fields).
    earned = m.get("adj_net_income_common" if banked else "adj_net_income")
    if reit:
        v.append(_ffo_multiple_bar(bar, m, me, peer_rows))
    elif earned is not None and earned <= 0:
        v.append(bar("pe", "Price to earnings", "Loss", 5,
                     "Without last year's one-time items the company lost money, so there are no earnings to value."
                     if adjusted and m["net_income"] > 0 else "The company lost money last year, so there are no earnings to value."))
    else:
        rivals = [r[PEER_PE] for r in peer_rows if r[PEER_PE] is not None]
        s = _pct_rank(pe, rivals, lower_is_better=True)
        v.append(bar("pe", "Price to earnings", multiple(pe), s,
                     ("Not enough data to compare." if s is None else
                      f"You pay {_beat(s, 'less', 'more', f'{len(rivals)} similar banks')} for each dollar of profit."
                      if banked else
                      f"You pay less per dollar of profit than for {s}% of peers." if s >= 50 else
                      f"You pay more per dollar of profit than for {100 - s}% of peers.")
                     + ((" Uses earnings per share left to common shareholders"
                         + (", without last year's one-time items." if adjusted else ".")) if banked and pe is not None
                        else " Uses profit without last year's one-time items." if adjusted and pe is not None else "")
                     + (BANK_SPIKE_NOTE if banked and pe is not None and _provision_spike(m) == "acquired" else "")))
    fy = me.get("fcf_yield")
    if not banked:
        v.append(not_scored("fcf_yield", "Free cash flow yield") if fin else bar("fcf_yield", "Free cash flow yield", pct(fy), None if fy is None else _lerp(fy, [(-0.05, 1), (0, 15), (0.05, 55), (0.10, 85), (0.15, 100)]),
                 "Cash left after running the business, as a share of the company's price. Higher is better." if fy is None or fy > 0 else
                 "The business used more cash than it brought in last year."))
    shares_off = bool(price and _share_count_gap(u, m, price))
    if shares_off:
        # Every ratio to the price compares the new share count's price with the old company's figures.
        v = [not_scored(x["key"], x["label"], SHARES_BAR_NOTE) for x in v]
    elif banked and _bank_stale(m):
        v = [not_scored(x["key"], x["label"], BANK_STALE_NOTE) for x in v]
    groups.append({"name": "Valuation", "hint": BANK_HINTS["Valuation"] if banked else
                   "Is the price reasonable for what you get?", "bars": v})

    # Profitability
    p = _ffo_profit_bars(bar, no_sales, m, me, table, doubt) if reit else banked["Profitability"] if banked else []
    nm = m.get("adj_net_margin")
    reported = lambda x: f" That leaves out last year's one-time items. With them it was {pct(x)}." if adjusted and x is not None else ""
    if not reit and not banked:
        p.append(no_sales("net_margin", "Net profit margin") if doubt else bar("net_margin", "Net profit margin", pct(nm), None if nm is None else _lerp(nm, [(-0.2, 1), (0, 20), (0.08, 55), (0.15, 80), (0.25, 100)]),
                     ("No revenue data." if nm is None else f"Keeps {cents(nm)} of profit from every dollar of sales." if nm >= 0 else
                      f"Loses {cents(nm)} on every dollar of sales.") + reported(m.get("net_margin"))))
        roe = m.get("adj_roe")
        p.append(bar("roe", "Return on equity", pct(roe), None if roe is None else _lerp(roe, [(-0.1, 1), (0, 15), (0.10, 55), (0.20, 85), (0.30, 100)]),
                     "Profit earned on the money shareholders have in the business. 15% or more is strong." + reported(m.get("roe")) if roe is not None else
                     "Can't be measured because shareholder equity is zero or negative."))
    fm = (m["fcf"] / m["revenue"]) if m.get("fcf") is not None and m.get("revenue") else None
    if not banked:
        p.append(not_scored("fcf_margin", "Cash conversion") if fin else no_sales("fcf_margin", "Cash conversion") if doubt else bar("fcf_margin", "Cash conversion", pct(fm), None if fm is None else _lerp(fm, [(-0.1, 1), (0, 20), (0.08, 60), (0.15, 85), (0.25, 100)]),
                 "Share of each sales dollar that turns into real cash. Profits backed by cash are more trustworthy."))
    if banked and _bank_stale(m):
        p = [not_scored(x["key"], x["label"], BANK_STALE_NOTE) for x in p]
    groups.append({"name": "Profitability", "hint": "Does the business make money?", "bars": p})

    # Growth
    g = []
    gr = m.get("revenue_growth")
    # A bank has revenue rather than sales: interest income less interest paid, plus fees, without one-time gains or
    # losses on investment securities (fundamentals.derive).
    sales, what = ("Revenue", "Revenue (interest earned less interest paid, plus fees)") if banked else ("Sales", "Sales")
    g.append(no_sales("growth", f"{sales} growth (1 year)") if doubt else bar("growth", f"{sales} growth (1 year)", pct(gr), None if gr is None else _lerp(gr, [(-0.2, 1), (-0.05, 25), (0, 40), (0.10, 70), (0.25, 100)]),
                 "Not enough history." if gr is None else f"{what} {'grew' if gr >= 0 else 'shrank'} {abs(gr) * 100:.1f}% in the last fiscal year."))
    n, up = m.get("revenue_years", 0), m.get("revenue_up_years", 0)
    cons = None if n < 3 else _lerp(up / (n - 1), [(0, 10), (0.5, 45), (1, 90)]) + (10 if (m.get("revenue_cagr") or 0) > 0.05 else 0)
    g.append(no_sales("consistency", "Growth consistency") if doubt else bar("consistency", "Growth consistency", f"{up} of {n - 1} yrs" if n >= 2 else "n/a", None if cons is None else min(cons, 100),
                 "Not enough history." if n < 3 else f"{sales} rose in {up} of the last {n - 1} years. Steady growth is easier to trust."))
    if reit:
        py, yc = m.get("ffo_positive_years", 0), m.get("ffo_years_checked", 0)
        g.append(bar("ffo_record", "FFO track record", f"{py} of {yc} yrs" if yc else "n/a", None if not yc else _lerp(py / yc, [(0, 5), (0.5, 40), (0.67, 65), (1, 95)]),
                     "Not enough history." if not yc else f"Funds from operations were positive in {py} of the last {yc} years."))
    else:
        py, yc = m.get("profitable_years", 0), m.get("years_checked", 0)
        g.append(bar("profit_record", "Profit track record", f"{py} of {yc} yrs" if yc else "n/a", None if not yc else _lerp(py / yc, [(0, 5), (0.5, 40), (0.67, 65), (1, 95)]),
                     "Not enough history." if not yc else f"Profitable in {py} of the last {yc} years"
                     + (f", not counting one-time items. With them it was {m['profitable_years_reported']}." if m.get("profitable_years_reported", py) != py else ".")))
    if banked and _bank_stale(m):
        g = [not_scored(x["key"], x["label"], BANK_STALE_NOTE) for x in g]
    groups.append({"name": "Growth", "hint": "Is the business getting bigger and steadier?", "bars": g})

    # Balance sheet
    b = banked["Balance sheet"] if banked else []
    # Missing cash and debt tags both read as zero, which is no data rather than a balance.
    nc = m.get("net_cash") if m.get("cash") or m.get("total_debt") else None
    ncr = nc / (mcap / _whole(m)) if nc is not None and mcap else None
    if banked:
        pass
    elif fin:
        b.append(not_scored("net_cash", "Net cash vs. price"))
    elif m.get("debt_known") is False:
        b.append(not_scored("net_cash", "Net cash vs. price", _debt_bar_note(m)))
    elif shares_off:
        b.append(not_scored("net_cash", "Net cash vs. price", SHARES_BAR_NOTE))
    else:
        b.append(bar("net_cash", "Net cash vs. price", pct(ncr), None if ncr is None else _lerp(ncr, [(-0.6, 1), (-0.2, 30), (0, 55), (0.15, 80), (0.35, 100)]),
                     "Cash and debt are not reported." if nc is None else
                     "Cash minus all debt, as a share of the company's price." + (" It has more cash than debt." if nc > 0 else " It owes more than it holds in cash.")))
    de = m.get("lt_debt_to_equity")
    if banked:
        pass
    elif m.get("equity") is not None and m["equity"] <= 0:
        b.append(bar("de", "Debt to equity", "Negative equity", 3, "The company owes more than it owns. That is a serious warning sign."))
    elif m.get("debt_known") is False:
        b.append(not_scored("de", "Debt to equity", _debt_bar_note(m)))
    else:
        b.append(bar("de", "Debt to equity", multiple(de), None if de is None else _lerp(de, [(0, 100), (0.25, 85), (0.5, 65), (1, 40), (2, 10)]),
                     "Not enough data." if de is None else "No long-term debt." if de == 0 else
                     "Long-term debt compared with what shareholders own. Under 0.5 is comfortable."))
    cr = m.get("current_ratio")
    if not banked:
        b.append(bar("current", "Short-term cushion", multiple(cr), None if cr is None else _lerp(cr, [(0.5, 5), (1, 35), (1.5, 65), (2, 85), (3, 100)]),
                 ("Not reported. REITs, like banks and insurers, don't sort their assets and bills by when they come due."
                  if reit else "Not reported (common for banks and insurers).") if cr is None else
                 "Assets it can turn into cash within a year, divided by bills due within a year. Above 1.5 is comfortable."))
    groups.append({"name": "Balance sheet", "hint": BANK_HINTS["Balance sheet"] if banked else
                   "Could it survive a rough patch?", "bars": b})

    # Shareholders
    sh = []
    d = m.get("dilution")
    sh.append(bar("dilution", "Share count change", "n/a" if d is None else f"{d * 100:+.1f}%", None if d is None else _lerp(-d, [(-0.15, 5), (-0.05, 40), (0, 75), (0.03, 90), (0.06, 100)]),
                  "Not enough history." if d is None else
                  "Fewer shares than a year ago, so each share owns more of the company." if d < -0.002 else
                  "About the same number of shares as a year ago." if d <= 0.01 else
                  "More shares than a year ago, so each share owns a smaller piece."))
    if insider:
        tier = insider["tier"]
        sc = 95 if tier == 1 else 80 if tier == 2 else 62
        sh.append(bar("insiders", "Insider buying", f"${insider['total_value'] / 1e6:.1f}M" if insider["total_value"] >= 1e5 else f"${insider['total_value']:,.0f}", sc,
                      f"Insiders bought shares on the open market in the last {insider['window']} days."))
    else:
        sh.append(bar("insiders", "Insider buying", "None", 40, "No open-market insider purchases in the scanned window. Common and not a bad sign by itself."))
    groups.append({"name": "Shareholders", "hint": "Are owners being treated well?", "bars": sh})

    # Market mood (shown for context, excluded from the overall health score)
    mk = []
    dd = (1 - price / high52) if high52 and price else None
    mk.append(bar("drawdown", "Below 52-week high", pct(dd, 0), None if dd is None else _lerp(dd, [(0, 10), (0.1, 35), (0.25, 65), (0.5, 90), (0.7, 100)]),
                  "Higher score means further below its 1-year high: more out of favor. That can mean opportunity or real trouble.", context=True))
    mk.append(bar("news", "News tone", news_overall["label"].title() if news_overall["label"] != "none" else "No news", news_overall["score"],
                  news_overall["text"], context=True))
    groups.append({"name": "Market mood", "hint": "How the market feels right now. Shown for context, not counted in health.", "bars": mk})

    scores = [x["score"] for grp in groups for x in grp["bars"] if x["score"] is not None and not x["context"]]
    return groups, (round(sum(scores) / len(scores)) if scores else None)


def _discounted(base, g, r=0.10, tg=0.025):
    """Present value of a cash flow `base` growing `g` a year for 5 years and 2.5% after, discounted at 10%."""
    pv, cf = 0.0, base
    for year in range(1, 6):
        cf *= 1 + g
        pv += cf / (1 + r) ** year
    return pv + cf * (1 + tg) / (r - tg) / (1 + r) ** 5


# The cash-flow model (_cash_flow_model): a discounted cash flow value of the whole business, less its net debt.
# Measured on September 2026 data, the model it replaced (free cash flow after interest, growing at the company's sales
# growth for 5 years and 2.5% after, discounted at 10% for every company, less net debt) came out a median 34% below the
# price, and 33% below the peer estimates. The gap grew with debt, since free cash flow was counted after interest and
# the debt was subtracted as well (the model came to 0.80 of the peer estimates where interest paid was under 2% of free
# cash flow, 0.45 where it was 50% to 100%), and with size, since one rate served every company (0.87 of the peer
# estimates under $300M of market value, 0.51 over $50B), and growth stopped dead after year 5. The parameters come from
# published practice and from measured risk, not from today's prices:
# - The discount rate is a cost of capital by size. Damodaran's January 2026 figures put the average US company outside
#   finance at 7.7% (cost of equity 8.4%: a 4.0% risk-free rate plus a 4.4% equity risk premium), so the largest
#   companies get 8%. Smaller ones are riskier: over the year to September 2026 their weekly price swings ran at a
#   median 64% a year under $300M of market value, 49% at $300M to $2B, 41% at $2B to $10B and 30% to 36% above, and
#   valuation practice adds a size premium for them (Kroll's CRSP size deciles), so the rate rises a point a tier.
# - Growth is the average of the company's sales growth last year and its yearly rate over the last 3 years, each held
#   between 0% and 12% (fast growth rarely lasts: Chan, Karceski and Lakonishok, 2003), for 5 years, then it moves
#   evenly to 2.5% by year 10 and stays there, the usual three-stage shape (Damodaran's valuation models use a 5-year
#   high-growth stage and a 5-year transition). The 3-year rate alone counts a rebound from a bad first year as growth
#   (Carnival's 30% a year from its 2022 trough, against 6% last year), and misses a recent change of pace: valued on
#   it, companies whose growth last year ran 5 points or more below their 3-year rate came to 1.16 of the peer
#   estimates, those 5 points or more above it 0.79, and the rest 0.92. On the average of the two, the three groups come
#   to 0.96, 0.92 and 0.93 (on the slower of the two, 0.86, 0.79 and 0.90). A rate whose years include a sales jump
#   (CF_JUMP) is left out, as the figures then show no steady trend (General Mills' sales read as $2.0B for 2023 and
#   $19.5B for 2024).
# - Free cash flow is counted as the model values it: before interest, which is added back after tax because debt is
#   subtracted at the end (a median 19% of free cash flow where paid), but only as much as the debt read could cost
#   (CF_INTEREST_RATE); after stock-based pay (a median 15% where paid, 50% or more for a fifth of companies), a real
#   cost to shareholders that operating cash flow adds back and earnings, which the peer price to earnings uses, already
#   count; and after capital spending reported on its own line (software, construction; fundamentals.DURATION). Its
#   level is the middle of the last three years' free cash flow as a share of sales, applied to the latest year's sales,
#   which damps a one-off year (a swing in working capital, a one-off receipt) without lagging a growing company's size.
#   Where the sales figures can't carry it (CF_SALES_BAND, CF_JUMP, sales_doubtful) it is the middle year's cash flow
#   itself, and where the cash flow tops the sales (CF_MARGIN_MAX) the model is left out.
# - That cash flow must be positive in every year read. The old model needed only a positive average, which let in cash
#   flows no level describes: the middle of years of both signs can land on a weak one (MYR Group: -0.6%, 0.1% and 6.0%
#   of sales, so 0.1%), and their average counts an outlying year in full (Kodak: 0.7%, -5.8% and then 42% in 2025). The
#   212 companies this leaves out that a positive total and mostly positive years would let in came out at a median 0.56
#   of the peer estimates, typically 2.4 times above or below them, against 0.93 and 1.5 times for the rest.
# Against the peer estimates the value to price now comes to a median 0.93 of theirs (0.67 before) and lands within 1.5x
# of them for 58% of companies (39% before). Their rank correlation is about unchanged: 0.602 against 0.581 before for
# the 994 companies valued both ways by both models, a gain inside the noise of a paired bootstrap (95% interval -0.015
# to +0.061). It still comes out a median 11% below the price, since it allows no growth above 12%, nor above 2.5% after
# year 10.
CF_HIGH_YEARS = 5  # years at the company's own growth rate
CF_YEARS = 10  # by which growth has faded to CF_TERMINAL
CF_GROWTH_CAP = 0.12
CF_TERMINAL = 0.025
CF_RATES = ((10e9, 0.08), (2e9, 0.09), (3e8, 0.10))  # (least market value, discount rate), largest first
CF_SMALL_RATE = 0.11
CF_TAX = FEDERAL_TAX  # for adding back interest where the company's normal tax rate is unknown
CF_PARTS = ("interest_paid", "sbc", "software_capex", "construction_capex")  # fundamentals.CASH_FLOW_METRICS
# Equity is the business's value less its net debt, so where the debt is large, a small error in the business value
# becomes a large one in the shares, and heavy debt brings a risk of default that a rate by size doesn't price. So the
# model is left out when net debt passes the company's market value (half its value with the debt counted), or half the
# business value the model finds, which happens mostly when the cash flow is thin (the reason given then says so). On
# September 2026 data that left out 186 companies (130 and 56), 41 of which it would have valued at or below nothing,
# and the thin ones at a median 0.17 of their price; with them in, the rank correlation above fell from 0.599 to 0.586
# and the share within 1.5x of the peer estimates from 58% to 56%. Since the values left out are low ones, the guard
# drops more "overvalued" verdicts than "undervalued" ones (53 and 24).
CF_DEBT_SHARE = 0.5
# The most interest per dollar of the debt read from the latest balance sheet that is added back each year. Interest on
# debt the reader missed (a finance arm's borrowing, a dealer's floor plan, a balance sheet that tags its debt under
# names it doesn't read) must stay counted as a cost, or the model would count the cash flow before interest without
# subtracting the debt: General Motors' $6.6B, with no debt read, put its value at 5.1 times its price, against 3.1 with
# the limit. On September 2026 data, the 1,453 companies outside finance with $50M or more of debt read paid a median
# 5.1% of it in interest last year, and 11% paid more than 10%, some of them because their debt fell during the year.
CF_INTEREST_RATE = 0.10
# A free cash flow margin above which the cash flow and the sales can't describe the same business, so the model is left
# out rather than valuing the cash flow alone. On September 2026 data that left out 6 companies. For most, one of the
# two figures doesn't reflect the business: Array Digital's cash flow for 2023 and 2024 evidently still included the
# wireless business it sold, against sales of about $100M, a median margin of 833%, and valuing its cash flow alone put
# it at 7.4 times its price; Wise's came to 363%. Royalty Pharma's (129%) and Natural Resource Partners' (103%) are
# real, as royalty receipts and partnership payouts their sales figures leave out, and lose the model too.
CF_MARGIN_MAX = 1.0
CF_SALES_BAND = (0.4, 2.5)  # latest sales, against the middle of the model years', within which the margin is applied
CF_JUMP = 4  # a year's sales more than this many times the year before's, or less than a quarter, is no trend


def cash_flow_rate(mcap):
    """The cash-flow model's discount rate for a company of market value `mcap`."""
    return next((r for least, r in CF_RATES if (mcap or 0) >= least), CF_SMALL_RATE)


def _size_words(mcap):
    """The market value tier of CF_RATES a company of market value `mcap` falls in, in words."""
    bounds = [least for least, _ in CF_RATES]
    i = next((k for k, least in enumerate(bounds) if (mcap or 0) >= least), len(bounds))
    if i == 0:
        return f"worth {money(bounds[0])} or more"
    if i == len(bounds):
        return f"worth under {money(bounds[-1])}"
    return f"worth {money(bounds[i])} to {money(bounds[i - 1])}"


def _year_list(years):
    """Years in words: "2025", "2024 and 2025", "2023, 2024 and 2025"."""
    ys = [str(y) for y in years]
    return ys[0] if len(ys) == 1 else ", ".join(ys[:-1]) + " and " + ys[-1]


def _middle_of(years):
    """Where the middle of a figure over `years` comes from, in words: "in 2024", "on average in 2024 and 2025", "at the
    middle of 2023, 2024 and 2025"."""
    return (f"in {years[0]}" if len(years) == 1 else f"on average in {_year_list(years)}" if len(years) == 2
            else f"at the middle of {_year_list(years)}")


def _steady(flows):
    """Whether yearly cash flows `flows` are steady enough to value: above zero in every year."""
    return all(f > 0 for f in flows)


def _sales_jump(a, b):
    """Whether sales moving from `a` to `b` in a year is too sharp a change to count as growth (CF_JUMP)."""
    return a <= 0 or b <= 0 or b > CF_JUMP * a or b * CF_JUMP < a


def _growth_inputs(m):
    """The sales growth rates the cash-flow model averages, as [(years spanned, yearly rate)], and the first sales
    jump (CF_JUMP) that left one out, as (year, sales, next year, sales), or None."""
    revs = [(h["year"], h["revenue"]) for h in m.get("history") or [] if h.get("revenue")]
    jumps = [(ya, a, yb, b) for (ya, a), (yb, b) in zip(revs, revs[1:]) if _sales_jump(a, b)]
    rates = []
    cagr, g1 = m.get("revenue_cagr"), m.get("revenue_growth")
    if cagr is not None and len(revs) >= 3 and not jumps:
        rates.append((revs[-1][0] - revs[0][0], cagr))
    if g1 is not None and not (jumps and jumps[-1][2] == revs[-1][0]):
        rates.append((1, g1))
    return rates, (jumps[0] if jumps else None)


def _cash_flow_model(u, m, shares, price):
    """The cash-flow model's method (as fair_value lists them) or None, and the reason it is left out, a clause to
    follow "because" (None where there is nothing to say). See the notes above CF_HIGH_YEARS."""
    mcap = u.get("mcap") or shares * price
    listed = m.get("cash_flow_years")
    # Only years whose figures were all read (a failed SEC frame leaves one unknown), and never without the newest,
    # since older years alone would value the company on stale figures. Metrics from before the model read these
    # figures have none.
    years = [y for y in listed or [] if all(y.get(k) is not None for k in CF_PARTS)]
    if not listed:
        return None, None
    if not years or years[-1] is not listed[-1]:
        return None, ("the SEC's figures for its interest paid, stock-based pay or capital spending in "
                      f"{listed[-1]['year']} could not all be read in this run")
    t = m.get("normal_tax_rate")
    t = CF_TAX if t is None else t
    debt = m.get("total_debt") or 0
    added = [min(y["interest_paid"], CF_INTEREST_RATE * debt) for y in years]
    before = [y["fcf"] - y["software_capex"] - y["construction_capex"] + a * (1 - t) for y, a in zip(years, added)]
    flows = [f - y["sbc"] for f, y in zip(before, years)]
    n, when = len(years), _year_list([y["year"] for y in years])
    if not _steady(flows):
        short = _year_list([y["year"] for f, y in zip(flows, years) if f <= 0])
        if _steady(before):
            why = (f"its free cash flow in {short} was positive only before stock-based pay, which the model counts as "
                   "a cost")
        else:
            why = f"its free cash flow was not positive in {short}"
        return None, why + ", so there is no steady cash flow to value"

    # The level: the middle year's cash flow as a share of sales, applied to the latest sales, unless the sales
    # figures can't carry it. Then the middle year's cash flow itself.
    rev = m.get("revenue")
    sized = [(f / y["revenue"], y) for f, y in zip(flows, years) if y.get("revenue") and y["revenue"] > 0]
    margin = statistics.median(x for x, _ in sized) if sized and rev and rev > 0 else None
    unsized = None
    if margin is not None:
        sales = [y["revenue"] for _, y in sized]
        if sales_doubtful(m):
            unsized = "its sales figure in the SEC data looks incomplete"
        elif margin > CF_MARGIN_MAX:
            return None, (f"its free cash flow came out larger than its sales ({margin * 100:.0f}% of them "
                          f"{_middle_of([y['year'] for _, y in sized])}), so the two figures don't describe the same "
                          "business, as the model needs")
        elif not CF_SALES_BAND[0] <= rev / statistics.median(sales) <= CF_SALES_BAND[1] \
                or max(sales) > CF_JUMP * min(sales):
            unsized = "its sales figures in the SEC data swing too much from year to year to measure it by"
    if unsized:
        margin = None
    base = margin * rev if margin is not None else statistics.median(flows)
    if base <= 0:
        return None, (f"its free cash flow as a share of its sales was not positive "
                      f"{_middle_of([y['year'] for _, y in sized])}, so there is no steady cash flow to value")

    # A sales figure that looks incomplete (sales_doubtful) shows no trend either.
    rates, jump = ([], None) if sales_doubtful(m) else _growth_inputs(m)
    held = [max(0.0, min(CF_GROWTH_CAP, x)) for _, x in rates]
    g = statistics.fmean(held) if held else 0.0
    r = cash_flow_rate(mcap)
    pv, cf = 0.0, base
    fade = CF_YEARS - CF_HIGH_YEARS
    for year in range(1, CF_YEARS + 1):
        cf *= 1 + (g if year <= CF_HIGH_YEARS else g + (CF_TERMINAL - g) * (year - CF_HIGH_YEARS) / fade)
        pv += cf / (1 + r) ** year
    business = pv + cf * (1 + CF_TERMINAL) / (r - CF_TERMINAL) / (1 + r) ** CF_YEARS
    nc = m.get("net_cash") or 0
    swing = "so the value left for shareholders would swing widely with small changes in the forecast"
    if -nc > mcap:
        return None, f"its debt less its cash, {money(nc)}, is more than the company's stock market value, {swing}"
    if -nc > CF_DEBT_SHARE * business:
        return None, (f"its free cash flow of {money(base)} a year values the whole business at only "
                      f"{money(business)}, less than twice its debt less its cash of {money(nc)}, {swing}")

    also = [what for key, what in (("software_capex", "software it builds for its own use"),
                                   ("construction_capex", "construction")) if any(y[key] for y in years)]
    paid = any(y["interest_paid"] for y in years)
    start = (f"Starts from free cash flow of {money(base)} a year: cash from operations less capital spending"
             + (f" ({' and '.join(also)} included)" if also else "")
             + (" and stock-based pay" if any(y["sbc"] for y in years) else ""))
    if paid and not debt:
        start += ". Interest stays counted as a cost, since no debt was read from its latest balance sheet to subtract."
    elif any(a < y["interest_paid"] for a, y in zip(added, years)):
        start += (", with interest added back, after tax, since the debt is subtracted at the end. Only interest of up "
                  f"to {CF_INTEREST_RATE * 100:.0f}% of that debt a year is added back: it paid more, which the debt "
                  "read from its latest balance sheet can't account for, so the rest stays counted as a cost.")
    elif paid:
        start += ", with interest added back, after tax, since the debt is subtracted at the end."
    else:
        start += "."
    ys = [y["year"] for _, y in sized]
    if margin is not None:
        start += (f" That is {margin * 100:.1f}% of sales, "
                  + (f"its share in {ys[0]}" if len(ys) == 1 else f"the average of {_year_list(ys)}" if len(ys) == 2
                     else f"the middle of {_year_list(ys)}") + f", applied to last year's sales of {money(rev)}.")
    else:
        start += ((f" That is its figure for {when}" if n == 1 else
                   f" That is the {'average' if n == 2 else 'middle'} of its figures for {when}")
                  + (f", rather than a share of sales, since {unsized}." if unsized else "."))

    parts, verb = [], None
    for span, x in sorted(rates):
        v = "grew" if x >= 0 else "fell"
        parts.append(("" if v == verb else f"{v} ") + f"{abs(x) * 100:.0f}%"
                     + (" last year" if span == 1 else f" a year over the last {span} years"))
        verb = v
    seen = (f"Sales {' and '.join(parts)}." if rates else "" if jump else
            ("For the same reason, its sales can't show a trend." if unsized else "Its sales figure in the SEC "
             "data looks incomplete, so it can't show a trend.") if sales_doubtful(m) else
            "There is no sales history to show a trend.")
    if jump:
        ya, a, yb, b = jump
        seen += (f"{' The longer trend is left out, as its' if rates else 'Its'} sales in the SEC data jump from "
                 f"{money(a)} in {ya} to {money(b)} in {yb}, too sharp a change to show a steady trend.")
    cap = f"{CF_GROWTH_CAP * 100:.0f}%"
    counted = [c for c, test in ((f"counting growth above {cap} as {cap}, the most the model allows",
                                  any(x > CF_GROWTH_CAP for _, x in rates)),
                                 ("counting a decline as no growth", any(x < 0 for _, x in rates))) if test]
    basis = ("the average of the two" + "".join(f", {c}" for c in counted) if len(rates) == 2 else
             "the most the model allows" if counted and rates[0][1] > 0 else "as sales did")
    if round(g * 100) >= 1:
        pace = f" The cash flow grows {g * 100:.0f}% a year for {CF_HIGH_YEARS} years, {basis}"
    elif rates and all(x <= 0 for _, x in rates):
        pace = f" The model assumes no decline, so the cash flow stays flat for {CF_HIGH_YEARS} years"
    elif rates:
        pace = f" The cash flow stays about flat for {CF_HIGH_YEARS} years, {basis}"
    else:
        pace = f" The cash flow stays flat for {CF_HIGH_YEARS} years"
    grows = (seen + pace + f", then its growth moves evenly to {CF_TERMINAL * 100:.1f}% by year {CF_YEARS} and stays "
             "there, about the pace of inflation.").strip()
    top, bottom = CF_RATES[0][1], CF_SMALL_RATE
    rate = (f"Cash further off counts for less, so it is discounted at {r * 100:.0f}% for each year of waiting, the "
            f"rate used here for companies {_size_words(mcap)}"
            + (f" (smaller, riskier ones get up to {bottom * 100:.0f}%)." if r == top else
               f", the riskiest (the largest get {top * 100:.0f}%)." if r == bottom else
               f" ({top * 100:.0f}% for the largest, {bottom * 100:.0f}% for the smallest and riskiest)."))
    net = (f"Its net cash of {money(nc)} is added." if nc > 0 else
           f"Its net debt of {money(nc)} is subtracted." if nc < 0 else "")
    return {"name": "Cash-flow model", "value": (business + nc) / shares,
            "note": " ".join(x for x in (start, grows, rate, net) if x),
            "model": {"cash_flow": base, "margin": margin, "growth": g, "discount_rate": r,
                      "terminal_growth": CF_TERMINAL, "net_cash": nc}}, None


# The most the share count behind a REIT's or a bank's market value may differ from the one in its latest SEC filing
# before its fair value is withheld (_share_count_gap), along with every ratio to its price.
SHARES_GAP = 1.25
SHARES_BAR_NOTE = ("Not measured. The share count changed after the company's latest SEC filing, so its price and its "
                   "reported figures describe different companies until it files again.")
# Ratios of listed to filed shares that a stock split or an American depositary share gives (_split_ratio).
SPLIT_RATIOS = (1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def _filed_shares(m):
    """The share count in a company's latest SEC filing: for a bank, the common shares outstanding on its latest balance
    sheet's date where tagged (fundamentals.BANK_SHARES), which is what its tangible book value is for, else the count on
    a filing's cover. A cover can postdate the balance sheet by a share sale or a merger that the balance sheet doesn't
    have yet (Richmond Mutual's August 2026 cover: 16.8M shares, against 10.5M at June 30), or predate one it has."""
    return (m.get("bank_shares") if m.get("bank") else None) or m.get("shares_out")


def _split_ratio(r):
    """Whether `r`, the share count behind the market value over the one in the latest SEC filing, is within 1% of a
    stock split's ratio or an American depositary share's (ServisFirst's 2-for-1 split in August 2026, after its last
    filing; HDFC Bank's depositary shares, each three of its shares)."""
    return any(abs(x / k - 1) <= 0.01 for x in (r, 1 / r) for k in SPLIT_RATIOS)


def _share_count_gap(u, m, price):
    """Why a REIT's or a bank's fair value is withheld, or None: its market value divided by its price implies a share
    count more than SHARES_GAP away from the one its latest SEC filing gives, so the shares changed after its last
    filing (a merger, a share sale or a reverse split) and its figures and its price describe different companies, so
    its price ratios are left out too (health). On September 2026 data that withholds six REITs (GIPR, JBGS, MKZR, NHP,
    VMRK, WHLR), among them Vivmark (Equity Residential, merged with AvalonBay: 788M shares priced, 375M filed, with only
    Equity Residential's FFO filed) and Wheeler (a reverse split), and six banks (BAFN, BCBP, ESQ, FSUN, RBKB, RMBI),
    among them Richmond Mutual (16.8M shares priced, 10.5M on its June 2026 balance sheet) and FirstSun Capital (47M
    priced, 28M on its last cover that the frames have, though its June balance sheet already holds First Foundation,
    which it bought). A bank's whole-company figures still describe what its price does after a stock split or for
    depositary shares (_split_ratio), and its diluted share count can stand in for a cover-page count of one class of
    its shares (Mechanics Bancorp's 19M, beside 209M diluted)."""
    listed, filed = (u.get("mcap") or 0) / price, _filed_shares(m)
    if not (m.get("reit") or m.get("bank")) or not listed or not filed or filed <= 0:
        return None
    if abs(math.log(listed / filed)) <= math.log(SHARES_GAP):
        return None
    if m.get("bank"):
        diluted = m.get("diluted_shares")
        if diluted and abs(math.log(listed / diluted)) <= math.log(SHARES_GAP) or _split_ratio(listed / filed):
            return None
    return (f"No fair value is shown. The stock's market value implies about {listed / 1e6:,.0f}M shares, but its "
            f"latest SEC filing counts {filed / 1e6:,.0f}M, so the share count has changed since then (a merger, a share "
            "sale or a reverse split). Its financial figures and its price describe different companies until it files "
            "again.")


def _lender(m):
    """Whether a REIT earns mostly interest on loans (reit_kind "mortgage"), rather than being a landlord whose leases are
    booked as loans (VICI Properties, Safehold, which reit_types lists), and has book equity. Its peers' price to book,
    the usual yardstick for a lender, joins their price to earnings: on September 2026 data, for the 11 profitable
    lenders, the midpoint of the two missed their prices by a median 0.150 on the log scale, against 0.165 for price to
    earnings alone, and it no longer called lenders trading below book value far too dear on one year's earnings
    (Cherry Hill Mortgage at 0.45x book: overvalued by 45% on earnings, fair on the midpoint). Book equity includes any
    preferred stock, which flatters it for lenders that issued some. For the two landlords price to book missed by 0.46
    and 0.54, against 0.14 and 0.04 on earnings, so they keep earnings alone."""
    return reit_kind(m) == "mortgage" and not m.get("reit_type") and (m.get("equity") or 0) > 0


def _bank_eps(u, m):
    """A bank's earnings per diluted share left to common shareholders (fundamentals._bank_fields) where they are per
    share of the stock its price is quoted on, else None, and its price to earnings is its market value over those
    earnings instead. Per share keeps a bank that merged after its fiscal year on its own earnings for each of its shares
    (Fifth Third, which bought Comerica in 2026). That needs the shares behind its market value to be within SHARES_GAP
    of its latest filing's, which a stock split since (ServisFirst) or depositary shares (HDFC Bank's, each three of its
    shares) are not, and its diluted share count to be on the scale of its filed count, within a factor of 2, which a
    tag in thousands or with three zeros too many is not (Princeton Bancorp's 6,859 for 6.9M; Landmark Bancorp's
    6.1 billion for 6.1M)."""
    eps, diluted, filed, price = m.get("adj_eps"), m.get("diluted_shares"), _filed_shares(m), u.get("price")
    if not eps or eps <= 0 or not diluted or not price or not u.get("mcap"):
        return None
    if abs(math.log(u["mcap"] / price / (filed or diluted))) > math.log(SHARES_GAP):
        return None
    return eps if not filed or 0.5 <= diluted / filed <= 2 else None


def _bank_methods(u, m, peer_rows, shares):
    """A profitable bank's fair value methods (BANK_FEATURES): its peers' median price to tangible book value times its
    own tangible book value, and their median price to earnings times its earnings per share (_bank_eps, else its
    earnings over the shares behind its market value)."""
    methods = []
    tce, eps = m.get("tangible_equity"), _bank_eps(u, m)
    common = m.get("adj_net_income_common")
    med = _median([r.get("ptbv") for r in peer_rows])
    if med and tce and tce > 0:
        methods.append({"name": "Peer price to tangible book", "value": med * tce / shares,
                        "note": f"Similar banks trade at {med:.2f}x their tangible book value, what common shareholders "
                                "own once goodwill and other intangible assets are left out: "
                                f"{money(tce)}, or {dollars(tce / shares)} a share."})
    med = _median([r.get("adj_pe") for r in peer_rows])
    per_share = eps or common / shares
    if med:
        methods.append({"name": "Peer price to earnings", "value": med * per_share,
                        "note": f"Similar banks trade at {med:.1f}x earnings. Uses last year's earnings of "
                                f"{dollars(per_share)} a share left to common shareholders"
                                + (", without one-time items." if m.get("one_time") else ".")})
    return methods


# The longest a bank's newest fiscal year in the SEC data may have ended before its latest balance sheet for it to be
# valued (_bank_withheld): 15 months, which a year to June still meets with the June balance sheet a year on. Magyar
# Bancorp's newest year with both revenue and earnings tagged ended in September 2023, 33 months before it.
BANK_STALE_DAYS = 456
BANK_STALE_NOTE = ("Not measured. The bank's newest full year of results in its SEC data ended more than a year before "
                   "its latest balance sheet, so it says too little about the bank today.")


def _bank_stale(m):
    """Whether a bank's newest fiscal year ended more than BANK_STALE_DAYS before its latest balance sheet."""
    end, at = m.get("fiscal_year_end"), m.get("balance_as_of")
    return bool(end and at and (dt.date.fromisoformat(at) - dt.date.fromisoformat(end)).days > BANK_STALE_DAYS)


def _bank_withheld(m, profitable):
    """Why a bank's fair value is withheld, or None: its newest year is stale (_bank_stale), an acquisition's loan-loss
    allowance swamped its earnings (_provision_spike), or it lost money, when the earnings and tangible book value of
    similar profitable banks, which banks are valued on (_bank_methods), say little about it (a bank losing money often
    trades far below its tangible book value)."""
    if _bank_stale(m):
        end = dt.date.fromisoformat(m["fiscal_year_end"])
        return (f"No fair value is shown. The newest full year of results in the bank's SEC data ended in "
                f"{end:%B %Y}, more than a year before its latest balance sheet, so it says too little about the bank "
                "today.")
    spike = _provision_spike(m)
    if spike == "acquired":
        return (f"No fair value is shown. Last year the bank's provision for loan losses rose to "
                f"{money(m['provision'])} from {money(m['provision_prior'])}, by more than its whole pretax income, in a "
                "year it made an acquisition, when a bank must set aside at once an allowance for the loans it takes "
                "over. Its earnings, and the similar banks matched on them, say too little about its value until a "
                "year without that charge.")
    if profitable:
        return None
    why = ("The bank lost money last year" if (m.get("net_income") or 0) <= 0 else
           "Without last year's one-time items the bank lost money" if (m.get("adj_net_income") or 0) <= 0 else
           "After its preferred dividends, nothing was left for common shareholders last year")
    return (f"No fair value is shown. {why}, and banks are valued here on the earnings and tangible book value of "
            "similar profitable banks, which say little about one that is losing money.")


# How much a bank's provision for loan losses must jump, as a multiple of the year before's and in dollars as a share of
# the year's pretax income, for its fair value to be taken loosely (_provision_spike). The one acquisition-year case on
# September 2026 data is Capital One's 2025, when the allowance it set up at once for Discover's loans, $8.8B by its
# 10-K, took its provision from $11.7B to $20.7B and its earnings to $2.88 a share without one-time items.
SPIKE_RATIO = 1.5
SPIKE_SHARE = 0.25


def _provision_spike(m):
    """How a bank's newest year's earnings were held down by a jump in its provision for loan losses, or None: "acquired"
    where the jump (at least SPIKE_RATIO times the year before's) exceeded its whole pretax income in a year its
    acquisitions added goodwill, which is mostly the allowance a bank must set up at once for loans it buys and is not
    a cost of its own lending (_bank_withheld); "jump" where the rise came to at least SPIKE_SHARE of its pretax income
    otherwise, which may be a one-time charge or a lasting rise in its loan losses (fair_value's confidence)."""
    p, before, pretax = m.get("provision"), m.get("provision_prior"), m.get("pretax_income")
    if p is None or before is None or not pretax or pretax <= 0 or p < SPIKE_RATIO * max(before, 0):
        return None
    rise = p - max(before, 0)
    if rise > pretax and (m.get("goodwill_acquired") or 0) > 0:
        return "acquired"
    return "jump" if rise >= SPIKE_SHARE * pretax else None


# The share of a bank's revenue that fees may reach before its fair value is taken loosely (_bank_caveat): on
# September 2026 data the 7 valued banks at 50% or more (custody banks such as BNY, State Street and Northern Trust, and
# banks with large brokerage or mortgage businesses) missed their prices by a mean absolute log error of 0.32, against
# 0.13 for the others, and neither matching on fee share nor valuing them on earnings alone did measurably better.
FEE_HEAVY = 0.5


def _bank_caveat(m):
    """Why a bank's fair value is taken loosely, or None: fees make up FEE_HEAVY or more of its revenue, or last year's
    earnings were held down by a jump in its provision for loan losses (_provision_spike)."""
    rev, fees = m.get("revenue"), m.get("noninterest_income")
    if rev and rev > 0 and fees is not None and fees / rev >= FEE_HEAVY:
        return (f"Fees make up {fees / rev * 100:.0f}% of the bank's revenue, from businesses such as custody, asset "
                "management, brokerage or mortgage banking, while most banks it is compared with earn mostly interest on "
                "loans.")
    if _provision_spike(m) == "jump":
        return (f"Last year's earnings were held down by a jump in its provision for loan losses, to "
                f"{money(m['provision'])} from {money(m['provision_prior'])}, which may or may not last.")
    return None


def fair_value(u, m, price, peers, table):
    """Peer multiples plus a cash-flow model (_cash_flow_model). Returns methods, range and verdict, or None when no
    reliable estimate exists (with a "withheld" reason where the report's general one would be wrong). The cash-flow
    model's method also carries its inputs ("model"), and the confidence note says why it is left out where it is.
    Earnings leave out last year's one-time items. A REIT that owns property is valued on
    its funds from operations (FFO) instead of its earnings (REIT_SALES and REIT_CASH_FLOW say which other methods
    apply to it), and a bank on price to tangible book value and price to earnings alone (_bank_methods)."""
    if not price or price <= 0:
        return None
    # The share count behind the market cap, so estimates line up with the peer multiples and the P/S bar.
    shares = u["mcap"] / price if u.get("mcap") else m.get("shares_out")
    if not shares or shares <= 0:
        return None
    fin = financial(u, m)
    reit = reit_kind(m) == "property"
    withheld = _share_count_gap(u, m, price)
    if withheld:
        return {"methods": [], "verdict": None, "withheld": withheld}
    peer_rows = [table[s] for s in peers]
    bank = _bank_peers(table.get(u["symbol"]) or {}, table)
    rev, ni = m.get("revenue"), m.get("adj_ffo" if reit else "adj_net_income_common" if bank else "adj_net_income")
    profitable = ni is not None and ni > 0
    withheld = _bank_withheld(m, profitable) if bank else None
    if withheld:
        return {"methods": [], "verdict": None, "withheld": withheld}
    nc = m.get("net_cash") or 0
    methods = _bank_methods(u, m, peer_rows, shares) if bank and profitable else []
    doubt = sales_doubtful(m)

    # A sales multiple assumes the company can earn what its peers earn on each dollar of sales, which says
    # little about one that loses money, so that company is valued on its cash flow alone. Nor is it used when
    # the sales figure itself can't be right.
    if doubt or bank or reit and not REIT_SALES or reit_kind(m) == "mortgage" or m.get("debt_known") is False and not fin:
        pass
    elif rev and rev > 0 and profitable and fin:
        med_ps = _median([r["ps"] for r in peer_rows])
        if med_ps:
            methods.append({"name": "Peer price to sales", "value": med_ps * rev / shares,
                            "note": f"Similar companies trade at {med_ps:.1f}x sales."})
    elif rev and rev > 0 and profitable:
        # Enterprise value, so a peer's debt is not priced in as if it were sales. This company's own net
        # debt is then subtracted (or net cash added) to get back to what the shares are worth.
        med_ev = _median([r["ev_sales"] for r in peer_rows])
        own = _whole(m)
        value = (med_ev * rev + nc) * own / shares if med_ev else 0
        if value > 0:
            methods.append({"name": "Peer value to sales", "value": value,
                            "note": f"Similar {'REITs' if reit else 'companies'} are valued at {med_ev:.1f}x sales, counting their debt and cash."
                                    + (f" This company's net cash of {money(nc)} is added." if nc > 0 else
                                       f" This company's net debt of {money(nc)} is subtracted." if nc < 0 else "")
                                    + (f" Its shares own {own * 100:.0f}% of the business, the rest belonging to holders "
                                       "of its operating partnership's units." if own < 0.995 else "")})
    med_pe = _median([r.get(PEER_FFO if reit else PEER_PE) for r in peer_rows])
    if med_pe and profitable and reit:
        plain = m.get("ffo")
        methods.append({"name": "Peer price to FFO", "value": med_pe * ni / shares,
                        "note": f"Similar REITs trade at {med_pe:.1f}x funds from operations (FFO), the profit measure "
                                f"REITs report. Uses last year's FFO of {money(ni)}"
                                + (f", without one-time items ({profit(plain)} with them)."
                                   if plain is not None and abs(plain - ni) > 0.01 * abs(ni) else ".")})
    elif med_pe and profitable and not bank:
        methods.append({"name": "Peer price to earnings", "value": med_pe * ni / shares,
                        "note": f"Similar companies trade at {med_pe:.1f}x earnings."
                                + (f" Uses last year's profit without one-time items: {money(ni)} instead of {profit(m['net_income'])}."
                                   if m.get("one_time") else "")})
    if _lender(m) and profitable:
        med_pb = _median([r.get("pb") for r in peer_rows])
        if med_pb:
            methods.append({"name": "Peer price to book", "value": med_pb * m["equity"] / shares,
                            "note": f"Similar REITs that lend trade at {med_pb:.2f}x their book value (their loans and "
                                    "other assets as their books carry them, less what they owe), the usual yardstick "
                                    "for a lender."})

    if reit:
        # FFO is what is left after interest, so the model values the shares directly, without taking off debt again.
        ffos = [h["adj_ffo"] for h in (m.get("history") or [])[-3:] if h.get("adj_ffo") is not None]
        base = sum(ffos) / len(ffos) if ffos and REIT_CASH_FLOW else None
        if base and base > 0:
            g = max(0.0, min(0.12, m.get("revenue_cagr") or m.get("revenue_growth") or 0.0))
            value = _discounted(base, g) / shares
            methods.append({"name": "Cash-flow model", "value": value,
                            "note": f"Average FFO of ${base / 1e6:,.0f}M growing {g * 100:.0f}% a year for 5 years, then "
                                    "2.5%, discounted at 10%. FFO is counted after interest, so debt is not subtracted."})
    left_out = None
    debt_doubt = m.get("debt_known") is False and not fin and not reit
    if not reit and not fin and not debt_doubt:
        # The model subtracts the debt, so it is left out where the debt could not be read (DEBT_DOUBT_VALUE_NOTE).
        model, left_out = _cash_flow_model(u, m, shares, price)
        if model:
            methods.append(model)

    # A method that puts the value more than METHOD_SANITY times the price, or less than a METHOD_SANITY-th of it,
    # rests on a misread figure, so it is left out.
    wild = [x for x in methods if not price / METHOD_SANITY <= x["value"] <= price * METHOD_SANITY]
    sanity = None
    if wild:
        methods = [x for x in methods if x not in wild]
        sanity = " ".join(
            f"{x['name']} came out at {dollars(x['value'])} a share, "
            + (f"more than {METHOD_SANITY} times the price" if x["value"] > price else
               f"less than a {'tenth' if METHOD_SANITY == 10 else f'{METHOD_SANITY}th'} of the price")
            + ". A gap that large more likely means a figure read from its filings is wrong, so it is left out."
            for x in wild)
        if not methods:
            return {"methods": [], "verdict": None, "dropped": [x["name"] for x in wild],
                    "withheld": f"There is no fair value estimate. {sanity}"
                                + (f" {DEBT_DOUBT_VALUE_NOTE}" if debt_doubt else "")}
    if not methods and debt_doubt:
        return {"methods": [], "verdict": None,
                "withheld": f"There is no fair value estimate. {DEBT_DOUBT_VALUE_NOTE[:-1]}, and "
                            + ("the company lost money last year, not counting one-time items, so it can't be valued "
                               "on its earnings either." if not profitable else
                               "no price to earnings of similar companies was available to value it on instead.")}

    if not methods:
        if left_out and not profitable:
            # Why, which the report's general explanation doesn't say (a free cash flow that is positive only before
            # stock-based pay, or heavy debt). And, since such a company goes without a verdict however richly it is
            # priced (on September 2026 data, 116 companies the old model rated overvalued, among them Cloudflare at
            # 59 times sales), how its price compares with its peers' on sales, the one yardstick left.
            lost = ("Without last year's one-time items the company lost money" if (m.get("net_income") or 0) > 0
                    else "The company lost money last year")
            med_ps = _median([r["ps"] for r in peer_rows])
            ps = (table.get(u["symbol"]) or {}).get("ps")  # as the report's price to sales bar shows it
            versus = (f" For comparison, its stock trades at {ps:.1f} times its sales, against a median of "
                      f"{med_ps:.1f} times for similar companies." if ps and med_ps else "")
            return {"methods": [], "verdict": None,
                    "withheld": f"There is no fair value estimate. {lost}, so only the cash-flow model could apply, "
                                f"and it is left out because {left_out}.{versus}"}
        return None
    vals = sorted(x["value"] for x in methods)
    mid = statistics.median(vals)
    # A value more than 50% away from the price needs two independent estimates behind it, so one can't stretch
    # the midpoint past that. The peer estimates count as one: they come from the same similar companies, matched
    # on profit margins, so their sales and earnings multiples mostly agree by construction (on September 2026
    # data they landed within 1.5x of each other for 57% of companies, against 31% with Nasdaq's industry peers,
    # while the share of each within 1.5x of the cash-flow model rose only from 37% to 39% and from 33% to 35%).
    # So the midpoint passes 50% only when the cash-flow model and the peer estimates (their geometric mean) are
    # both past it. Then, as before, three methods keep their median, which always has a second method on its
    # side, and two keep the nearer one. A company valued on peer multiples alone (every financial company, and
    # others without positive average cash flow) therefore stays within 50% of the price.
    peer_vals = [x["value"] for x in methods if x["name"].startswith("Peer")]
    other = [x["value"] for x in methods if not x["name"].startswith("Peer")]
    kinds = sorted(([math.exp(statistics.fmean(math.log(v) for v in peer_vals))] if peer_vals else []) + other)
    top = max(price * 1.5, vals[-2]) if len(kinds) > 1 and kinds[0] > price * 1.5 else price * 1.5
    bottom = min(price * 0.5, vals[1]) if len(kinds) > 1 and kinds[-1] < price * 0.5 else price * 0.5
    held = "above" if mid > top else "below" if mid < bottom else None
    mid = min(max(mid, bottom), top)
    limit = None
    if held:
        far = f"more than 50% {held} the price"
        stop = f"so the midpoint stops at 50% {held}"
        past = (lambda v: v > price * 1.5) if held == "above" else (lambda v: v < price * 0.5)
        if len(vals) == 1:
            limit = f"Only one method could be used, and a value {far} needs a second method to agree, {stop}."
        elif len(kinds) == 1:
            limit = (f"Both estimates come from similar companies' multiples, and a value {far} needs an independent "
                     f"method to agree, {stop}.")
        elif len(vals) == 2 and all(past(v) for v in vals):
            limit = (f"Both methods put the value {far}, so the midpoint is the {'lower' if held == 'above' else 'higher'} "
                     "of the two rather than their average.")
        elif len(vals) == 2:
            limit = f"Only one of the two methods puts the value {far}. A gap that large needs both to agree, {stop}."
        elif past(other[0]):
            limit = (f"Only the cash-flow model puts the value {far}, not the similar companies' multiples taken "
                     f"together. A gap that large needs both to agree, {stop}.")
        else:
            limit = (f"Only the similar companies' multiples put the value {far}, not the cash-flow model. They "
                     f"count as one estimate, since they come from the same companies, and a gap that large needs "
                     f"a second one to agree, {stop}.")
    if len(vals) == 1:
        low, high = vals[0] * 0.85, vals[0] * 1.15
    elif len(vals) == 2:
        # Span both estimates, so the range never excludes a method listed beneath it.
        low, high = vals
    else:
        low, high = max(vals[0], mid * 0.75), min(vals[-1], mid * 1.25)
    low, high = min(low, mid * 0.9), max(high, mid * 1.1)
    verdict = "undervalued" if price < low else "overvalued" if price > high else "fair"

    spread = vals[-1] / vals[0]
    apart = f"within {max(5, math.ceil(round((spread - 1) * 20, 6)) * 5)}% of each other"
    pair = reit and not REIT_CASH_FLOW  # a REIT's two methods both come from its peers
    suits = "bank" if bank else "REIT" if pair else "financial company"
    of = f"the two methods that suit a {suits}" if pair or fin else "the three methods"
    if len(vals) == 1 and not profitable:
        confidence, note = "low", (("Without last year's one-time items the company lost money"
                                    if (m.get("net_income") or 0) > 0 else "The company lost money last year")
                                   + ", so only the cash-flow model applies. Treat this range loosely.")
    elif len(vals) == 1 and reit_kind(m) == "mortgage":
        confidence, note = "low", (("A REIT whose leases are booked as loans is valued on its peers' price to earnings "
                                    "alone" if m.get("reit_type") else "Only its peers' price to earnings could be used")
                                   + ", since their price to sales says little about it, so treat this range loosely.")
    elif len(vals) == 1:
        confidence, note = "low", f"Only one of {of} could be used, so treat this range loosely."
    elif spread > 2:
        confidence, note = "low", (f"The methods disagree widely (lowest {dollars(vals[0])}, highest {dollars(vals[-1])}), "
                                   "so treat this range loosely.")
    elif len(vals) == 3:
        confidence, note = "higher" if spread <= 1.5 else "moderate", f"All three methods were available and land {apart}."
    elif fin or pair:
        confidence, note = "moderate", (f"Both methods that suit a {suits} were available and land {apart}, though "
                                        f"both rest on the same similar {'REITs' if pair else 'banks' if bank else 'companies'}.")
    elif len(peer_vals) == 2:
        confidence, note = "moderate", (f"Two of the three methods could be used, and they land {apart}, though both "
                                        "rest on the same similar companies.")
    else:
        confidence, note = "moderate", f"Two of the three methods could be used, and they land {apart}."
    if doubt and profitable:
        note = f"{DOUBT_NOTE} {note}"
    elif debt_doubt:
        note = f"{DEBT_DOUBT_VALUE_NOTE} {note}"
    elif m.get("debt_known") is False and profitable and not fin:
        note = f"{DEBT_VALUE_NOTE} {note}"
    if sanity:
        note = f"{sanity} {note}"
    if left_out:
        note = f"{note} The cash-flow model is left out because {left_out}."
    caveat = _bank_caveat(m) if bank else None
    if caveat:
        confidence, note = "low", f"{caveat} {note}" + ("" if "loosely" in note else " Treat this range loosely.")

    for x in methods:
        x["value"] = round(x["value"], 2)
    return {
        "methods": methods,
        # The methods left out as data errors (METHOD_SANITY), for peer_multiples(); build takes it off the JSON.
        "dropped": [x["name"] for x in wild],
        "low": round(low, 2),
        "mid": round(mid, 2),
        "high": round(high, 2),
        "upside": mid / price - 1,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_note": note,
        "limit_note": limit,
    }
