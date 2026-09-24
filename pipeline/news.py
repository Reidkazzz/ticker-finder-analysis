"""Recent headlines per ticker, each labeled bullish, bearish or neutral with a one-line reason.

Labels come from transparent phrase rules, not a black box: every label names the phrase that triggered it.
"""
import datetime as dt
import email.utils
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .net import web_get

# (pattern, weight, reason). Positive weight = bullish. First strong match sets the reason.
RULES = [
    (r"\b(beats?|tops?|surpass(es)?|exceeds?)\b.*\b(estimates?|expectations|forecasts?|consensus)\b", 2, "Results came in better than analysts expected."),
    (r"\b(miss(es|ed)?|falls? short of|below)\b.*\b(estimates?|expectations|forecasts?|consensus)\b", -2, "Results came in worse than analysts expected."),
    (r"\b(raises?|raised|lifts?|boosts?|hikes?|ups)\b.*\b(guidance|outlook|forecast|full-year)\b", 2, "The company raised its outlook."),
    (r"\b(cuts?|lowers?|lowered|slashes?|trims?|withdraws?)\b.*\b(guidance|outlook|forecast)\b", -2, "The company lowered its outlook."),
    (r"\bupgrade[sd]?\b", 1.5, "An analyst upgraded the stock."),
    (r"\bdowngrade[sd]?\b", -1.5, "An analyst downgraded the stock."),
    (r"\b(price target)\b.*\b(raise[sd]?|boost(s|ed)?|lift(s|ed)?|hike[sd]?|increase[sd]?)\b|\b(raise[sd]?|boost(s|ed)?|lift(s|ed)?|hike[sd]?)\b.*\bprice target\b", 1, "An analyst raised their price target."),
    (r"\b(price target)\b.*\b(cut|lower(s|ed)?|slash(es|ed)?|reduce[sd]?)\b|\b(cut|lower(s|ed)?|slash(es|ed)?|reduce[sd]?)\b.*\bprice target\b", -1, "An analyst lowered their price target."),
    (r"\b(buyback|repurchase)\b", 1, "A share buyback reduces the share count."),
    (r"\b(raises?|increases?|hikes?|boosts?)\b.*\bdividend\b|\bspecial dividend\b", 1.5, "The dividend is going up."),
    (r"\b(cuts?|suspends?|eliminates?|slashes?)\b.*\bdividend\b", -2, "The dividend is being cut."),
    (r"\brecord (revenue|sales|profit|earnings|quarter|results)\b", 1.5, "The company reported record results."),
    (r"\b(fda|regulatory) (approval|approves|clears|clearance)\b|\bapproved by (the )?fda\b", 2, "A regulator approved a product."),
    (r"\b(fda|regulator)\b.*\b(rejects?|declines?|complete response letter)\b|\bcomplete response letter\b", -2, "A regulator turned down a product."),
    (r"\b(wins?|awarded|secures?|lands?)\b.*\b(contract|order|deal)\b", 1.5, "The company won new business."),
    (r"\b(to acquire|agrees? to buy|to be acquired|takeover|buyout|merger agreement)\b", 1, "Deal news, often a premium for shareholders of the target."),
    (r"\b(insiders?|ceo|cfo|director|chair(man)?)\b.*\b(buys?|bought|purchases?|purchased)\b.*\b(shares|stock)\b", 1.5, "An insider bought shares with their own money."),
    (r"\b(insiders?|ceo|cfo|director)\b.*\b(sells?|sold|dumps?)\b.*\b(shares|stock)\b", -0.5, "An insider sold shares, which is often routine."),
    (r"\b(public offering|stock offering|share offering|secondary offering|at-the-market|dilut)", -1.5, "New shares are being sold, which dilutes current owners."),
    (r"\b(lawsuit|sued|class action|securities fraud)\b", -1.5, "The company faces legal action."),
    (r"\b(sec|doj|ftc|federal) (probe|investigation|subpoena|charges?)\b|\binvestigat(ion|ing)\b", -1.5, "The company is under investigation."),
    (r"\b(bankruptcy|chapter 11|going concern|default(s|ed)?)\b", -3, "The company is in serious financial distress."),
    (r"\b(layoffs?|job cuts|restructuring|plant closure)\b", -1, "The company is cutting costs or jobs."),
    (r"\b(recall(s|ed)?)\b", -1.5, "A product recall costs money and trust."),
    (r"\b(short seller|short report)\b", -1.5, "A short seller published a negative report."),
    (r"\b(delist(ing|ed)?|nasdaq notice|non-compliance)\b", -2, "The listing is at risk."),
    (r"\b(ceo|cfo|chief executive)\b.*\b(resigns?|steps down|departs?|ousted|fired)\b", -1, "A top executive is leaving."),
    (r"\b(partnership|partners with|collaboration|teams up)\b", 0.5, "A new partnership could add business."),
    (r"\b(settle[sd]?|settlement)\b", -1, "The company is paying to settle a legal dispute."),
    (r"\b(worries|worried|concerns?|fears?|warns?|warning|headwinds?)\b", -1, "The story focuses on a risk or concern."),
    (r"\b(stock|shares)\b.*\b(rises?|gains?|climbs?|rebounds?|pops?|higher)\b", 1, "The stock is moving up."),
    (r"\b(stock|shares)\b.*\b(falls?|fell|drops?|dropped|slides?|slid|slips?|lower|declines?)\b", -1, "The stock is moving down."),
    (r"\b(soar(s|ed|ing)?|surge[sd]?|jump(s|ed)?|rall(y|ies|ied)|skyrocket(s|ed)?)\b", 1, "Something is moving sharply higher."),
    (r"\b(plunge[sd]?|tumble[sd]?|sink(s)?|sank|slump(s|ed)?|crash(es|ed)?|tank(s|ed)?)\b", -1, "Something is moving sharply lower."),
    (r"^[\w.&'-]+( [\w.&'-]+)? (slips|falls|drops|sinks|slides|tumbles|dips)\b", -1, "The stock is moving down."),
    (r"^[\w.&'-]+( [\w.&'-]+)? (rises|gains|climbs|jumps|rallies|pops)\b", 1, "The stock is moving up."),
    (r"\b(growth|grows|jumps|rises)\b.*\b(revenue|sales|profit|earnings)\b|\b(revenue|sales|profits?|earnings|eps)\b.*\b(rise|rises|grow|grows|jump|jumps|climb|climbs|soar|soars|surge|surges)\b", 1.5, "Revenue or profit is growing."),
    (r"\b(boosts?|lifts?|drives?|expands?)\b.*\b(margins?|profits?|sales|revenue|earnings)\b", 1, "Something is lifting sales or profit."),
    (r"\b(revenue|sales|profit|earnings)\b.*\b(decline|declines|fall|falls|drop|drops|shrink|shrinks)\b|\b(loss widens|wider loss)\b", -1, "Revenue or profit is shrinking."),
]
_COMPILED = [(re.compile(p, re.I), w, r) for p, w, r in RULES]
_LISTICLE = re.compile(r"\b(\d+|top|best) (stocks?|reasons|things)\b|\bstocks to (buy|watch)\b|\bshould you buy\b|\?$", re.I)


def classify(title):
    # "Will X beat estimates?" is speculation, not an event, whatever words it uses.
    if title.rstrip().endswith("?"):
        return "neutral", "A question or opinion piece, not news of an actual event.", 0.0
    hits = [(w, r) for rx, w, r in _COMPILED if rx.search(title)]
    if not hits:
        if _LISTICLE.search(title):
            return "neutral", "General commentary, not company news.", 0.0
        return "neutral", "No clear good or bad news in the headline.", 0.0
    score = sum(w for w, _ in hits)
    lead = max(hits, key=lambda h: abs(h[0]))
    if score >= 1:
        return "bullish", lead[1], score
    if score <= -1:
        return "bearish", lead[1], score
    return "neutral", "Mixed signals: " + lead[1][0].lower() + lead[1][1:], score


def _parse_rss(raw, source_default):
    items = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src = it.find("source")
        source = (src.text if src is not None and src.text else source_default).strip()
        if source_default == "Google News" and " - " in title:
            title, source = title.rsplit(" - ", 1)
        try:
            when = email.utils.parsedate_to_datetime(pub).astimezone(dt.timezone.utc) if pub else None
        except (TypeError, ValueError):
            when = None
        if title and link:
            items.append({"title": title, "url": link, "source": source, "date": when})
    return items


_GENERIC = {"the", "first", "american", "united", "national", "global", "international", "new", "general", "great", "big"}


def _name_key(name):
    """The distinctive word of a company name: 'Weyco Group Inc.' -> 'weyco'."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'-]+", name) if w.lower() not in _GENERIC and len(w) > 2]
    return words[0].lower() if words else None


def headlines(sym, name, max_items=10, max_age_days=90):
    items = []
    try:
        items = _parse_rss(web_get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym.replace('.', '-')}&region=US&lang=en-US"), "Yahoo Finance")
    except Exception:
        items = []
    if len(items) < 4:
        q = urllib.parse.quote(f'"{sym}" {name.split(",")[0]} stock')
        try:
            items += _parse_rss(web_get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"), "Google News")
        except Exception:
            pass
    now = dt.datetime.now(dt.timezone.utc)
    # Ticker feeds mix in general market stories. Prefer headlines that name the company.
    key_word = _name_key(name)
    sym_rx = re.compile(rf"\b{re.escape(sym)}\b")
    about = lambda t: bool(sym_rx.search(t)) or (key_word is not None and key_word in t.lower())
    fresh = [i for i in items if not i["date"] or (now - i["date"]).days <= max_age_days]
    relevant = [i for i in fresh if about(i["title"])]
    pool = relevant if len(relevant) >= 3 else relevant + [i for i in fresh if not about(i["title"])]
    seen, out = set(), []
    for it in sorted(pool, key=lambda x: x["date"] or now - dt.timedelta(days=999), reverse=True):
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        label, reason, score = classify(it["title"])
        out.append({
            "title": it["title"], "url": it["url"], "source": it["source"],
            "date": it["date"].strftime("%Y-%m-%d") if it["date"] else None,
            "label": label, "reason": reason, "score": score,
        })
        if len(out) >= max_items:
            break
    return out


def overall(items):
    if not items:
        return {"label": "none", "score": 50, "text": "No recent headlines found for this company."}
    bull = sum(1 for i in items if i["label"] == "bullish")
    bear = sum(1 for i in items if i["label"] == "bearish")
    net = sum(max(-2, min(2, i["score"])) for i in items)
    score = round(max(1, min(100, 50 + net / max(len(items), 1) * 25)))
    n, rest = len(items), len(items) - bull - bear
    # One good headline among nine neutral ones is not a positive news flow: require a real share of the headlines.
    tilt = lambda a, b: (a - b >= 2 and a >= n / 4) or (a > b and a >= 0.4 * n)
    if tilt(bull, bear):
        word = "Mostly positive" if bull >= n / 2 else "Leaning positive"
        label, text = "bullish", f"{word}: {bull} good, {bear} bad and {rest} neutral headlines."
    elif tilt(bear, bull):
        word = "Mostly negative" if bear >= n / 2 else "Leaning negative"
        label, text = "bearish", f"{word}: {bear} bad, {bull} good and {rest} neutral headlines."
    else:
        label, text = "neutral", f"Mixed or quiet: {bull} good, {bear} bad and {rest} neutral headlines."
    return {"label": label, "score": score, "text": text}


def all_headlines(pairs, workers=6):
    """pairs: [(symbol, name)] -> {symbol: [items]}"""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip([p[0] for p in pairs], pool.map(lambda p: headlines(*p), pairs)))
