"""Insider purchases on a past date, from the SEC's insider transaction data sets (data.py downloads them).

The pipeline reads each Form 4 filed in its window (pipeline/insiders.py: parse_form4 on the filing's XML, then build,
which merges co-filers and tiers each company). The data sets hold the same forms as tables, one row per reporting
owner, transaction and holding, with the footnote ids each field cites. So each Form 4 filed in the window is turned
back into the XML parts parse_form4 reads (issuer, reporting owners and their roles, the non-derivative transactions
and holdings in the form's order, with their ownership footnote ids), and the pipeline's own parse_form4 and build do
the rest, exactly as they do every night: a company's tier, average price paid and total value come from the same
code, thresholds and merging rules.
"""
import csv
import datetime as dt
import io
import os
import zipfile
from xml.sax.saxutils import escape

from . import FORM345_DIR

WINDOW_DAYS = 30  # build.py's default --insider-days


def _date(s):
    try:
        return dt.datetime.strptime(s, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _quarters(first, last):
    """The data sets ("2025q3") covering the days from `first` to `last`."""
    out, d = [], first
    while d <= last:
        q = f"{d.year}q{(d.month - 1) // 3 + 1}"
        if q not in out:
            out.append(q)
        d += dt.timedelta(days=1)
    return out


def _table(z, name):
    return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8", "replace")), delimiter="\t",
                               quoting=csv.QUOTE_NONE))


def _v(tag, value):
    return f"<{tag}><value>{escape(value or '')}</value></{tag}>" if value not in (None, "") else ""


def _nature(r):
    notes = "".join(f'<footnoteId id="{escape(n.strip())}"/>' for n in (r.get("NATURE_OF_OWNERSHIP_FN") or "").split(",")
                    if n.strip())
    return ("<ownershipNature>" + _v("directOrIndirectOwnership", r.get("DIRECT_INDIRECT_OWNERSHIP"))
            + f"<natureOfOwnership><value>{escape(r.get('NATURE_OF_OWNERSHIP') or '')}</value>{notes}</natureOfOwnership>"
            + "</ownershipNature>")


def _post(r):
    return ("<postTransactionAmounts>" + _v("sharesOwnedFollowingTransaction", r.get("SHRS_OWND_FOLWNG_TRANS"))
            + "</postTransactionAmounts>")


def form4_xml(sub, owners, trans, holds):
    """The parts of a Form 4's ownershipDocument that insiders.parse_form4 reads, from the data sets' rows."""
    x = ["<ownershipDocument><issuer>", f"<issuerCik>{escape(sub['ISSUERCIK'])}</issuerCik>",
         f"<issuerName>{escape(sub['ISSUERNAME'])}</issuerName>",
         f"<issuerTradingSymbol>{escape(sub['ISSUERTRADINGSYMBOL'])}</issuerTradingSymbol></issuer>"]
    for o in owners:
        rel = o.get("RPTOWNER_RELATIONSHIP") or ""  # "Director,Officer", "TenPercentOwnerOther", ...
        x.append("<reportingOwner><reportingOwnerId>"
                 f"<rptOwnerCik>{escape(o['RPTOWNERCIK'])}</rptOwnerCik><rptOwnerName>{escape(o['RPTOWNERNAME'])}</rptOwnerName>"
                 "</reportingOwnerId><reportingOwnerRelationship>"
                 + ("<isDirector>1</isDirector>" if "Director" in rel else "")
                 + ("<isOfficer>1</isOfficer>" if "Officer" in rel else "")
                 + (f"<officerTitle>{escape(o.get('RPTOWNER_TITLE') or '')}</officerTitle>" if o.get("RPTOWNER_TITLE") else "")
                 + ("<isTenPercentOwner>1</isTenPercentOwner>" if "TenPercentOwner" in rel else "")
                 + ("<isOther>1</isOther>" if "Other" in rel else "")
                 + (f"<otherText>{escape(o.get('RPTOWNER_TXT') or '')}</otherText>" if o.get("RPTOWNER_TXT") else "")
                 + "</reportingOwnerRelationship></reportingOwner>")
    x.append("<nonDerivativeTable>")
    for t in trans:
        day = _date(t.get("TRANS_DATE"))
        x.append("<nonDerivativeTransaction>" + _v("securityTitle", t.get("SECURITY_TITLE"))
                 + _v("transactionDate", day.isoformat() if day else "")
                 + f"<transactionCoding><transactionCode>{escape(t.get('TRANS_CODE') or '')}</transactionCode>"
                   "</transactionCoding>"
                 + "<transactionAmounts>" + _v("transactionShares", t.get("TRANS_SHARES"))
                 + _v("transactionPricePerShare", t.get("TRANS_PRICEPERSHARE"))
                 + _v("transactionAcquiredDisposedCode", t.get("TRANS_ACQUIRED_DISP_CD")) + "</transactionAmounts>"
                 + _post(t) + _nature(t) + "</nonDerivativeTransaction>")
    for h in holds:
        x.append("<nonDerivativeHolding>" + _v("securityTitle", h.get("SECURITY_TITLE")) + _post(h) + _nature(h)
                 + "</nonDerivativeHolding>")
    x.append("</nonDerivativeTable></ownershipDocument>")
    return "".join(x)


def form4_cache(insiders, day, window=WINDOW_DAYS):
    """An insiders.update_cache-style cache ({"form4": [...], "13d": [], "days_done": []}) of the Form 4s filed in the
    `window` days before `day` (not on it: the backtest's day ends before that evening's filings) that record an
    open-market purchase, each parsed by the pipeline's own insiders.parse_form4. Amendments (4/A) are left out, as the
    pipeline's daily scan reads form type 4 only."""
    first, last = day - dt.timedelta(days=window), day - dt.timedelta(days=1)
    records, forms = [], 0
    for q in _quarters(first, last):
        path = os.path.join(FORM345_DIR, f"{q}_form345.zip")
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path}: run python -m backtest.data form345")
        with zipfile.ZipFile(path) as z:
            subs = {}
            for s in _table(z, "SUBMISSION.tsv"):
                filed = _date(s["FILING_DATE"])
                if s["DOCUMENT_TYPE"] == "4" and filed and first <= filed <= last:
                    subs[s["ACCESSION_NUMBER"]] = (s, filed)
            trans = {}
            for t in _table(z, "NONDERIV_TRANS.tsv"):
                if t["ACCESSION_NUMBER"] in subs:
                    trans.setdefault(t["ACCESSION_NUMBER"], []).append(t)
            buys = {a for a, ts in trans.items() if any(t["TRANS_CODE"] == "P" for t in ts)}
            owners, holds = {}, {}
            for o in _table(z, "REPORTINGOWNER.tsv"):
                if o["ACCESSION_NUMBER"] in buys:
                    owners.setdefault(o["ACCESSION_NUMBER"], []).append(o)
            for h in _table(z, "NONDERIV_HOLDING.tsv"):
                if h["ACCESSION_NUMBER"] in buys:
                    holds.setdefault(h["ACCESSION_NUMBER"], []).append(h)
        forms += len(subs)
        key = lambda r, k: int(r.get(k) or 0)
        for a in sorted(buys):
            s, filed = subs[a]
            xml = form4_xml(s, owners.get(a, []),
                            sorted(trans[a], key=lambda r: key(r, "NONDERIV_TRANS_SK")),
                            sorted(holds.get(a, []), key=lambda r: key(r, "NONDERIV_HOLDING_SK")))
            try:
                found = insiders.parse_form4(xml)
            except Exception:  # the pipeline counts an unreadable filing as having no purchases
                found = []
            cik = int(s["ISSUERCIK"])
            for p in found:
                records.append({**p, "path": f"edgar/data/{cik}/{a}.txt", "filed": filed.strftime("%Y%m%d")})
    return {"form4": records, "13d": [], "days_done": []}, forms


def insiders_at(insiders, day, uni, window=WINDOW_DAYS):
    """insiders.build's payload for `day` over the listings in `uni`, and the number of Form 4s read."""
    cache, forms = form4_cache(insiders, day, window)
    return insiders.build(cache, uni, window, day), forms
