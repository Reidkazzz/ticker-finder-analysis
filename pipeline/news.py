"""Recent headlines per ticker, each labeled bullish, bearish or neutral with a one-line reason.

Labels come from transparent phrase rules, not a black box: every label names the phrase that triggered it. The rules
read the headline with the company's own name and ticker masked out (_mask), so a name that is itself a word ("Five
Below", "Growth", "UPS") can't set a label, and where a headline names other companies too, only the part about this one
counts (_own_part).
"""
import collections
import datetime as dt
import email.utils
import html
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .net import web_get

# What the company's masked name and ticker read as in a headline (_mask): one word, so rules anchored on a short
# subject at the start of a headline see the same subject length whatever the company is called.
SELF = "Xco"

# (pattern, weight, reason). Positive weight = bullish. The reason shown is the strongest hit of the label's own sign
# (classify).
RULES = [
    (r"\b(beats?|tops?|surpass(es)?|exceeds?)\b.*\b(estimates?|expectations|forecasts?|consensus)\b", 2, "Results came in better than analysts expected."),
    # Zacks words a miss as lagging estimates (131 of its 133 such headlines in September 2026 were read as neutral).
    (r"\b(miss(es|ed)?|falls? short of|lags?|lagged|trails?|trailed|below)\b.*\b(estimates?|expectations|forecasts?|consensus)\b", -2, "Results came in worse than analysts expected."),
    # Verb first ("raises guidance") or noun first ("Guidance Climbs", "Full-Year Outlook Raised").
    (r"\b(raises?|raised|lifts?|lifted|boosts?|boosted|hikes?|hiked|increases?|increased|ups)\b.*\b(guidance|outlook|forecast|full-year)\b"
     r"|\b(guidance|outlook|forecast)\b.{0,25}\b(raised|lifted|increased|boosted|climbs?|rises?|hiked)\b", 2, "The company raised its outlook."),
    (r"\b(cuts?|lowers?|lowered|slashes?|slashed|trims?|trimmed|reduces?|reduced|withdraws?|withdrew)\b.*\b(guidance|outlook|forecast)\b"
     r"|\b(guidance|outlook|forecast)\b.{0,25}\b(cut|lowered|slashed|trimmed|reduced|withdrawn)\b", -2, "The company lowered its outlook."),
    (r"\bupgrade[sd]?\b|\b(raises?|lifts?|ups)\b \w+ to (buy|outperform|overweight)\b", 1.5, "An analyst upgraded the stock."),
    (r"\binitiat\w*\b.{0,30}\b(buy|outperform|overweight|strong buy)\b", 1, "An analyst started covering the stock with a Buy."),
    (r"\bdowngrade[sd]?\b|\b(cuts?|lowers?)\b (\w+ )?to (sell|underperform|underweight|neutral|hold|equal[- ]weight)\b", -1.5,
     "An analyst downgraded the stock."),
    (r"\b(price targets?|pt)\b.*\b(raise[sd]?|boost(s|ed)?|lift(s|ed)?|hike[sd]?|increase[sd]?)\b"
     r"|\b(raise[sd]?|boost(s|ed)?|lift(s|ed)?|hike[sd]?|increase[sd]?)\b.*\b(price targets?|pt)\b"
     # "Morgan Stanley raises AMETEK stock target from USD 245.00 to USD 275.00", "UBS cuts target for Crown Holdings stock".
     r"|\b(raise[sd]?|lift(s|ed)?|boost(s|ed)?)\b.{0,40}\b(stock target|target for .{0,40}\bstock)\b", 1, "An analyst raised their price target."),
    (r"\b(price targets?|pt)\b.*\b(cuts?|trims?|lower(s|ed)?|slash(es|ed)?|reduce[sd]?)\b"
     r"|\b(cuts?|trims?|lower(s|ed)?|slash(es|ed)?|reduce[sd]?)\b.*\b(price targets?|pt)\b|\btargets? cuts?\b"
     r"|\b(cuts?|trims?|lower(s|ed)?)\b.{0,40}\b(stock target|target for .{0,40}\bstock)\b", -1,
     "An analyst lowered their price target."),
    (r"\b(buyback|repurchase)\b", 1, "A share buyback reduces the share count."),
    (r"\b(raises?|increases?|hikes?|boosts?)\b.*\b(dividend|distribution)\b", 1.5, "The dividend is going up."),
    # A one-time payment, not a higher regular dividend ("VerifyMe Declares Special Dividend").
    (r"\bspecial (cash )?dividend\b(?!.*\bcvrs?\b)", 1, "The company declared a one-time special dividend."),
    (r"\b(cuts?|suspends?|eliminates?|slashes?)\b.*\bdividend\b", -2, "The dividend is being cut."),
    # "Record Q1 Fiscal 2027 Revenue", "Record Preliminary Third Quarter Revenue", "Record High Production" (Aura Minerals'),
    # but not a record low, loss or date ("Announces Record Date for Third Quarter Distribution").
    (r"\brecord (?!(low|lows|loss|losses|date)\b)((?!(low|loss|losses|date)\b)\w+ ){0,4}(revenue|sales|profit|earnings|quarter"
     r"|results|production)\b", 1.5, "The company reported record results."),
    # Not a filing that seeks approval or clearance ("BioCardia Files Revised De Novo Pre-Submission for FDA Approval",
    # "Submits 510(k) Seeking FDA Clearance"), nor shareholders', a court's or an exchange's approval ("Receives
    # Stockholder Approval for Reverse Stock Split"); an approval abroad counts ("Novocure's Optune Pax Receives Approval
    # in Japan").
    (r"^(?!.*\b(seeks?|seeking|submits?|submitted|files?|filed|pre-submission|applies|applied|application)\b.{0,40}\b(fda|regulatory)"
     r" (approval|clearance))(?!.*\bfor (fda|regulatory) (approval|clearance)\b)(?!.*\b(stockholder|shareholder|court|board"
     r"|nasdaq|nyse|tsxv?|listing|exchange|lender|creditor)s?['’]? approval)(.*\b(fda|regulatory) (approval|approves|clears"
     r"|clearance)\b|.*\bapproved by (the )?fda\b|.*\breceives? (\w+ ){0,2}approval\b(?! process))", 2,
     "A regulator approved a product."),
    (r"\b(fda|regulator)\b.*\b(rejects?|declines?|complete response letter)\b|\bcomplete response letter\b", -2, "A regulator turned down a product."),
    # Trial results: positive data only beside a trial word, so a miner's "Positive Drilling Results" isn't one.
    (r"(?=.*\b(phase|pivotal|topline|trial|study)\b).*\bpositive\b.*\b(data|results|readout)\b"
     r"|\b(met|meets|achieved|achieves|hits?)\b.*\bprimary endpoint\b|\bphase (iii|3)\b.{0,25}\b(win|success|succeeds?|met)\b",
     2, "A drug trial succeeded."),
    (r"\b(miss(es|ed)?|fail(s|ed)?)\b.*\b(primary )?endpoint\b|\bphase \w+ (trial )?failure\b|\btrial fail(s|ed|ure)\b", -2, "A drug trial failed."),
    # Won business: plural contracts and orders, "receives" (L3Harris's $6B THAAD contract in September 2026), and a
    # project only when won or awarded. Not "award" alone, which catches prizes.
    (r"\b(wins?|won|awarded|secures?|secured|lands?|landed|receives?|received|selected for)\b.*\b(contracts?|orders?|deals?)\b"
     r"|\b(wins?|won|awarded)\b.*\bprojects?\b(?! finance)", 1.5, "The company won new business."),
    # Not where the company is the buyer ("Xco to Acquire Eagle Ford Assets from Devon Energy", Crescent Energy's), whose
    # shareholders get no premium.
    # The buyer is the company named just before the verb, a few words at most ("Xco & Everview Partners to Acquire
    # Slate Grocery REIT"), so "Xco Receives Proposal from Rival Corp to Acquire the Company" stays a target's news.
    (r"^(?!.*\bXco\b(\W+\w+){0,3}?\W+(to acquire|agrees? to (buy|acquire)|agreement to acquire|acquires|to buy|completes? "
     r"(the )?acquisition|closes? (the )?acquisition|(launches|makes|submits) .{0,30}\b(takeover|tender) (bid|offer) for)\b)"
     r"(?=.*\b(to acquire|agrees? to buy|to be acquired|takeover|buyout|merger agreement)\b)", 1,
     "Deal news, often a premium for shareholders of the target."),
    (r"\b(insiders?|ceo|cfo|director|chair(man)?)\b.*\b(buys?|bought|purchases?|purchased)\b.*\b(shares|stock)\b", 1.5, "An insider bought shares with their own money."),
    # Not a planned sale ("A director plans to sell 1,593 EverQuote (EVER) shares", from a Form 144).
    (r"^(?!.*\b(plans?|intends?|proposes?|may) to sell\b).*\b(insiders?|ceo|cfo|director)\b.*\b(sells?|sold|dumps?)\b.*\b(shares|stock)\b",
     -0.5, "An insider sold shares, which is often routine."),
    (r"\b(lawsuit|sued|class action|securities fraud)\b", -1.5, "The company faces legal action."),
    (r"\b(sec|doj|ftc|federal) (probe|investigation|subpoena|charges?)\b|\binvestigat(ion|ing)\b", -1.5, "The company is under investigation."),
    (r"\b(bankruptcy|chapter 11|going concern|default(s|ed)?)\b", -3, "The company is in serious financial distress."),
    (r"\b(layoffs?|job cuts|restructuring|plant closure)\b", -1, "The company is cutting costs or jobs."),
    (r"\b(recall(s|ed)?)\b", -1.5, "A product recall costs money and trust."),
    (r"\b(short seller|short report)\b", -1.5, "A short seller published a negative report."),
    # Not a voluntary delisting, which the company chose (WALD's in October 2026).
    (r"^(?!.*\b(voluntar\w*|regain\w*)\b)(.*\b(delist(ing|ed)?|nasdaq notice|non-compliance)\b"
     r"|.*\b(deficiency|minimum bid)\b.{0,40}\b(notice|notification|letter)\b|.*\bnot in compliance\b"
     # "Receives NYSE American Notice Regarding Stockholders' Equity Requirement" (Micropolis'), not a Japanese 6-K's
     # "Notice Regarding Listing of Shares on Prime Market" or "Notice Regarding Equity Compensation".
     r"|.*\bnotice regarding ([\w’']+ ){0,3}((stock|share)holders[’']? equity|continued listing|listing (standards?|"
     r"requirements?|rules?)|minimum bid|bid price|market value of)\b|.*\b(receiv\w*|receipt)\b.{0,40}"
     r"\b(delinquen\w*|deficien\w*|non-?complian\w*)\b)", -2, "The listing is at risk."),
    (r"\bpriority review\b|\bbreakthrough therapy designation\b|\bfast track designation\b", 1.5,
     "A regulator is speeding up its review of a product."),
    (r"\b(record|all-time) highs?\b(?! (production|revenue|sales|output|quarterly|annual))", 1, "The stock hit a record high."),
    (r"\b(q[1-4]|quarter|earnings|sales|revenues?|eps)\b.{0,30}\bbeats?\b(?!.*\bmiss)|\bbeats?\b.{0,15}\bon "
     r"(earnings|revenue|sales)\b", 2, "Results came in better than analysts expected."),
    (r"\b(ceo|cfo|chief executive)\b.*\b(resigns?|steps down|departs?|ousted|fired)\b", -1, "A top executive is leaving."),
    (r"\b(partnership|partners with|collaboration|teams up)\b", 0.5, "A new partnership could add business."),
    # Settling, not blocking a settlement (Exxon Mobil's "Blocks $5.2B Kashagan fine settlement").
    (r"^(?!.*\b(blocks?|rejects?|challenges?|appeals?)\b.{0,30}\bsettlement).*\b(settles?|settled|agrees? to (pay|settle)"
     r"|reach(es|ed)? (a |an )?\$?[\w.]* ?settlement)\b", -1, "The company is paying to settle a legal dispute."),
    (r"\b(worries|worried|concerns?|fears?|warns?|warning|headwinds?)\b", -1, "The story focuses on a risk or concern."),
    (r"\b(growth|grows|jumps|rises)\b.*\b(revenue|sales|profit|earnings)\b|\b(revenue|sales|profits?|earnings|eps)\b.*\b(rise|rises|grow|grows|jump|jumps|climb|climbs|soar|soars|surge|surges)\b"
     # "Genius Group Announces 140% Increase in Revenue", "Revenues to Increase by $1.5 Million", "Revenue Up 30%".
     r"|\bincrease in (revenue|sales|profit|earnings)\b|\b(revenues?|sales) (to )?(increase|grow|up)\b", 1.5, "Revenue or profit is growing."),
    (r"\b(boosts?|lifts?|drives?|expands?)\b.*\b(margins?|profits?|sales|revenue|earnings)\b", 1, "Something is lifting sales or profit."),
    (r"\b(revenue|sales|profit|earnings)\b.*\b(decline|declines|fall|falls|drop|drops|shrink|shrinks)\b|\b(loss widens|wider loss)\b", -1, "Revenue or profit is shrinking."),
]
_COMPILED = [(re.compile(p, re.I), w, r) for p, w, r in RULES]
_FAIR_VALUE = re.compile(r"\bfair value\b", re.I)
# A market-research release that lists the company among an industry's vendors, not news about it ("... Global
# Strategic Business Report ... Surge").
_REPORT = re.compile(r"\b(strategic business report|market size|market report|market research|market (is )?(projected|"
                     r"expected|forecast|set) to)\b", re.I)
_LISTICLE = re.compile(r"\b(\d+|top|best) (stocks?|reasons|things)\b|\bstocks to (buy|watch)\b|\bshould you buy\b|\?$", re.I)

# New shares sold (_dilution). The sale of new stock in any form: offerings, at-the-market programs, registered directs,
# private placements, warrant inducements, equity lines and convertible notes, which can turn into shares.
_DILUTION = re.compile(r"\b(public offering|stock offering|share offering|follow-on offering|at-the-market|registered direct"
                       r"|private placement|warrant inducement|equity line|convertible (senior )?notes?|dilut(es|ing|ion|ive))\b", re.I)
# Not new shares, or not bad news for current owners: a company's own IPO, which has no current public owners to dilute;
# a bond, note or preferred deal (unless convertible, which the pattern above names); an offering called off or
# redeemed; funding called non-dilutive; and earnings per diluted share (SYBT's, FCBM's results in September 2026).
_NOT_DILUTION = re.compile(r"\b(initial public offering|ipo|non-?dilutive|diluted|minimal dilution|dilution adjustment"
                           r"|terminat(es|ed|ion)|withdraw(s|n|al)|cancel(s|led|lation)|redemption|redeems?)\b", re.I)
_DEBT_DEAL = re.compile(r"\b(senior notes|notes due|bonds?|debt offering|preferred (stock|shares))\b", re.I)
_CONVERTIBLE = re.compile(r"\bconvertible\b", re.I)
# Shares sold by existing holders: a fall in the price is likely, but no new shares are made.
_HOLDERS_SELL = re.compile(r"\b(secondary offering|block trade|selling (stock|share)holders?)\b(?!.*\bprimary\b)", re.I)

# Price moves (_move): the direction of the first move verb whose subject is the stock itself, not the market or an
# index, so "Stock Sinks As Market Gains" is a fall. Before September 2026 only a few verb forms counted ("Plunging" and
# "Is Falling" didn't), and falls were labeled bearish 37% of the time against 70% for rises.
# "Advances" counts only as Zacks words a gain ("Advances While Market Declines", "Advances More Than Market"), not a
# company advancing a project or drug (213 headlines read as rises before October 2026); "outpaces" as its "Outpaces
# Stock Market Gains".
_UP = (r"ris(e|es|ing)|rose|gain(s|ed|ing)?|climb(s|ed|ing)?|jump(s|ed|ing)?|soar(s|ed|ing)?|surg(e|es|ed|ing)|rall(y|ies|ied|ying)"
       r"|pop(s|ped|ping)?|spik(e|es|ed|ing)|skyrocket(s|ed|ing)?|advanc(es|ed)(?= (while|more than|as )|\s+\d)|outpac(es|ed|ing)"
       r"|rebound(s|ed|ing)?|tick(s|ed)? up|(inch|edg|nudg)(e|es|ed|ing) (up|higher)|(is|are) up|up \d+(\.\d+)?%|higher")
_DOWN = (r"fall(s|ing)?|fell|drop(s|ped|ping)?|slid(e|es|ing)?|slip(s|ped|ping)?|sink(s|ing)?|sank|plung(e|es|ed|ing)"
         r"|plummet(s|ed|ing)?|tumbl(e|es|ed|ing)|slump(s|ed|ing)?|crash(es|ed|ing)?|tank(s|ed|ing)?|declin(e|es|ed|ing)"
         r"|dip(s|ped|ping)?|retreat(s|ed|ing)?|eas(e|es|ed|ing)|tick(s|ed)? down|(inch|edg|nudg)(e|es|ed|ing) (down|lower)"
         r"|(is|are) down|down \d+(\.\d+)?%|lower|shed(s|ding)?|pull(s|ed|ing)? back|weeks? of (declines|losses)")
# Ends at anything but a letter or digit, so "Up 7%" and "Down 16% in a Month" match (a word boundary after "%" never
# does before a space).
_MOVE = re.compile(rf"\b(?:(?P<up>{_UP})|(?P<down>{_DOWN}))(?![A-Za-z0-9])", re.I)
# A move word used as a noun or adjective, or for something other than the stock: "Surging Demand", "Rising Costs",
# "Chip Rally", "Pixel Install Spike", "Reimbursement Rebound", "Fall Flavors", "Lower Extremity", "Implied Volatility
# Surging"; and a move only forecast ("Could Surge 40%", analysts' price targets rather than a move).
_NOT_MOVE_AFTER = re.compile(r"^\W*(demand|sales|costs?|rates?|prices?|interest|volatility|margins?|flavors?|menu|season"
                             r"|collection|extremity|body|revenue|profitab\w*|expectations|attention|tide|tensions|competition"
                             r"|inflation|expenses?|risks?|debt|losses|competition|orders|bookings|traffic|adoption|use"
                             r"|momentum|traction|ground|approval|access|steam|attention|interest)\b", re.I)
_NOT_MOVE_BEFORE = re.compile(r"\b(chip|install|pixel|demand|volume|price|sector|ai|crypto|reimbursement|sales|order|traffic"
                              r"|volatility|last|could|may|might|set to|poised to|expected to|seen|likely to|can"
                              r"|profits?|earnings|ends|snaps|halts|breaks|stops)\W*$", re.I)
_IMPLIED_VOL = re.compile(r"\bimplied volatility\b", re.I)
# Short interest rising or falling is a bet against the stock changing, not a move in its price.
_SHORT_INTEREST = re.compile(r"\bshort interest\b", re.I)
_SUBJECT_AFTER = re.compile(rf"^\W*({SELF}|stocks?|shares?)\b", re.I)
_PERCENT_AFTER = re.compile(r"^\W*(\w+\W+){0,3}\d+(\.\d+)?\s?%")
# A forecast rather than an event, taken out before the rules read a headline: "Earnings Expected to Grow", "Could
# Rise 28%", "Might Be Poised for a Surge" (52 headlines read as news in October 2026).
_FORECAST = re.compile(r"\b(could|might|may|poised (for|to)|set to|expected to|likely to|seen|forecast to|projected to)\b"
                       r"(\W+\w+){0,3}?\W+(surge|soar|rally|jump|rise|climb|grow|gain|fall|drop|plunge|sink|decline|slide)\w*",
                       re.I)
# Verbs that only mean a price move when the stock is their subject; the sharp ones (soar, plunge) count anywhere but
# after the market or an index.
_SUBJECT_ONLY = re.compile(r"^(ris|rose|gain|climb|advanc|rebound|pop|tick|inch|edg|nudg|is|are|up|down|higher|lower|fall|fell"
                           r"|drop|slid|slip|declin|dip|retreat|eas|shed|jump)", re.I)
_SUBJECT = re.compile(rf"\b({SELF}|stocks?|shares?)\b(\W+\w+){{0,3}}\W*$", re.I)
_MARKET = re.compile(r"\b(market|markets|index|indexes|indices|s&p|dow|nasdaq|sector|futures|wall street|peers|rivals|bitcoin"
                     r"|(oil|gold)(?!\s+(shares|stocks?|reserves|production|output|miners?))|treasur(y|ies)|yields?)\b"
                     r"(\W+\w+){0,2}\W*$", re.I)

# Another company named in the headline (_own_part): a ticker in parentheses or after an exchange name, which marks a
# roundup or a story about someone else.
_OTHER_TICKER = re.compile(r"\((?:NASDAQ|NYSE|NYSE American|AMEX|OTC)?:?\s*([A-Z][A-Z.]{0,5})\)|\b(?:NASDAQ|NYSE):\s*([A-Z][A-Z.]{0,5})\b")
_CLAUSE = re.compile(r"\s*(?:[,;]|\s-\s|\s–\s|:\s|\bwhile\b|\bas\b|\bbut\b|\bamid\b|\bafter\b|\bwhereas\b)\s*", re.I)
# A company's own call on another company's stock (a broker, rating agency or adviser acting): a rating verb, or a
# price target changed, followed by the other company's ticker. The event is the other company's, not this one's
# (Jefferies upgrading another bank, S&P Global rating another issuer); the company's own news that merely mentions
# another ticker ("Nucor raises guidance ahead of Steel Dynamics (STLD) results") isn't one.
_ACTS_ON = re.compile(rf"^{SELF}\b.{{0,40}}?\b(upgrades?|downgrades?|initiates?|reiterates?|maintains?|rates?"
                      r"|(raises|cuts|lowers|boosts|lifts|trims)\b.{0,40}\bprice targets?)\b(?P<after>.*)$", re.I)

# Words that end a company's name, before its legal form or share class. Not "of", "and" or "&", which join a name
# ("Bank of America", "Central Garden & Pet").
_LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited", "plc", "holdings",
          "holding", "group", "nv", "sa", "ag", "se", "lp", "llc", "trust", "class", "common", "stock", "shares",
          "ordinary", "adr", "ads", "sponsored"}
# Single words too common to stand for a company on their own when a name's full run isn't in a headline (_mask), as
# "Bank" for Bank of America or "Target" for Target: only the first appearance is masked even when a name is found.
_COMMON_WORD = {"bank", "target", "block", "snap", "match", "gap", "first", "american", "united", "national", "global",
                "general", "great", "big", "best", "energy", "capital", "financial", "health", "medical", "power",
                "resources", "systems", "technologies", "therapeutics", "international", "new", "public", "service",
                "trade", "home", "data", "digital", "life", "solar", "water", "gas", "oil", "real", "estate", "west", "east",
                "north", "south", "community", "citizens", "peoples", "central", "pacific", "atlantic", "southern",
                "northern", "western", "eastern", "security", "insurance", "investors", "partners", "income", "credit",
                "stock", "canadian", "tower", "open", "quantum", "royal", "liberty", "texas", "renew", "core", "bitcoin",
                "industrial", "gold", "u.s", "us", "china", "world", "smart", "digital", "cloud", "green", "silver"}
# Names a company is known by in headlines besides its listing's name: its former name or a trade name.
ALIASES = {"PSKY": ["Skydance"], "SHFS": ["Safe Harbor"], "RTB": ["Roundtable"], "PHG": ["Philips"],
           "BNS": ["Scotiabank"], "JPM": ["JPMorgan"], "SLMT": ["Solmate"], "SHOT": ["Bonk"]}
# Tickers that are also ordinary words or abbreviations, matched as the company only in parentheses or after an exchange
# name, never bare: IT (Gartner) in "IT spending rises", AI (C3.ai) in "AI stocks rise", A (Agilent) as an article.
_WORD_TICKERS = {"A", "AI", "ALL", "ANY", "ARE", "BE", "BIG", "CAN", "CAR", "EAT", "FOR", "FUN", "GO", "HAS", "IT", "KEY",
                 "LOW", "MAN", "NOW", "ON", "ONE", "OUT", "PLAY", "RUN", "SO", "TV", "UP", "WELL", "WIN", "YOU", "USA", "EV"}


def _name_words(name):
    """The words of a company name before any legal suffix or share class, without a leading "The": 'Five Below, Inc.'
    -> ['Five', 'Below'], 'Alphabet Inc. Class C Capital Stock' -> ['Alphabet'], 'The Cigna Group' -> ['Cigna'],
    'Bank of America Corporation' -> ['Bank', 'of', 'America'], though never ending at the first word ('Stock Yards
    Bancorp')."""
    out = []
    # A "doing business as" prefix isn't part of the name ("D/B/A Compass Diversified Holdings").
    plain = re.sub(r"^\s*d/b/a\s+", "", (name or "").split("(")[0], flags=re.I)
    for w in re.findall(r"&|[A-Za-z0-9][A-Za-z0-9'.&-]*", plain):  # "AT&T" and "B&G" stay one word
        if not out and w.lower() == "the":
            continue
        if out and w.lower().strip(".") in _LEGAL:
            break
        # A web address's ending isn't said in headlines ("Amazon.com" is "Amazon"), nor are initials ("J.M. Smucker"
        # is "Smucker", "A. O. Smith" is "Smith").
        w = re.sub(r"\.(com|ai|io|net)$", "", w.rstrip("."), flags=re.I)
        if re.fullmatch(r"([A-Za-z]\.)+[A-Za-z]?", w + ".") and not out:
            continue
        out.append(w)
    while out and out[-1].lower() in ("of", "and", "&"):
        out.pop()
    return out


def _mask(title, sym, name):
    """The headline with the company's own name and ticker each replaced by one neutral word (SELF), so the rules read
    only what is said about it: the longest leading run of the name's words found, as one phrase, at its first
    appearance (so Target's "price target" stays), and the ticker as an exact, case-sensitive word (UPS, not the verb
    'ups'), with the company's other share class beside its name ("Alphabet (GOOGL)" on GOOG's page) as the company too.
    Without this, Five Below's 'Below' read as a miss and UPS as a raised outlook, and Growth, Boost, TOP and SOAR in
    names set labels (CTO's, RWAY's and TPVG's news tone in September 2026)."""
    text = title
    words = _name_words(name)
    for n in range(len(words), 0, -1):
        if n == 1 and (len(words[0]) < 3 or words[0].lower() in _COMMON_WORD and len(words) > 1):
            break
        phrase = r"\s+".join(re.escape(w) for w in words[:n])
        pat = re.compile(rf"(?<![A-Za-z0-9]){phrase}(?:'s)?(?:,?\s+(?:Inc|Corp|Co|Ltd|plc|Holdings|Group)\.?)?(?![A-Za-z0-9])", re.I)
        if pat.search(text):
            text = pat.sub(SELF, text, count=1)
            break
    if sym:
        text = re.sub(rf"\(\s*(?:(?:NASDAQ|NYSE|NYSE American|AMEX|OTC)\s*:\s*)?{re.escape(sym)}\s*\)", SELF, text)
        text = re.sub(rf"\b(?:NASDAQ|NYSE)\s*:\s*{re.escape(sym)}\b", SELF, text)
        if sym not in _WORD_TICKERS:
            text = re.sub(rf"(?<![A-Za-z0-9$]){re.escape(sym)}(?![A-Za-z0-9])", SELF, text)
    # A ticker in parentheses right after the masked name is the company's own, whatever its class.
    text = re.sub(rf"{SELF}\s*\(\s*(?:(?:NASDAQ|NYSE|NYSE American|AMEX|OTC)\s*:\s*)?[A-Z][A-Z.]{{0,5}}\s*\)", SELF, text)
    return re.sub(rf"{SELF}(\s+{SELF})+", SELF, text)


def _own_part(text):
    """The part of a masked headline that is about this company, where the headline names other companies by ticker
    too: the clauses that name it (a market roundup's 'Xco slips, ABC (ABC) jumps' is a fall for Xco). The whole
    headline where it names no other company, or doesn't name this one, or where its own clause says nothing beyond its
    name ('Xco, Microsoft (MSFT) beat estimates')."""
    if SELF not in text or not _OTHER_TICKER.search(text):
        return text
    parts = [p for p in _CLAUSE.split(text) if p and SELF in p]
    own = " ".join(parts)
    return own if len(own.split()) >= 2 else text


def _move(text):
    """(weight, reason) of the headline's price move, or None: the last move verb in the company's own clause (_own_clause)
    whose subject is the stock, this company, 'stock' or 'shares' within a few words before it or just after it
    ("Plunging Shares of Xco"), or that a percentage follows ("Soars 12%"), so "Profits Are Soaring", "Gains Momentum"
    and a body shop called Crash Champions aren't price moves. The last, so "Popped, Then Dropped" is a fall."""
    if _IMPLIED_VOL.search(text) or _SHORT_INTEREST.search(text):
        return None
    text = _own_clause(text)
    found = None
    for m in _MOVE.finditer(text):
        before, after = text[:m.start()], text[m.end():]
        if _MARKET.search(before) or _NOT_MOVE_BEFORE.search(before) or _NOT_MOVE_AFTER.match(after):
            continue
        if not (_SUBJECT.search(before) or _SUBJECT_AFTER.match(after) or _PERCENT_AFTER.match(after)
                or re.match(r"(is|are) (up|down)|(up|down) \d", m.group(0), re.I)):
            continue
        found = (1, "The stock is moving up.") if m.group("up") else (-1, "The stock is moving down.")
    return found


def _own_clause(text):
    """The clause of a headline that names this company, where it has several ("Humana Soars, Xco Sinks"), for its
    price move; the whole headline where none or every clause names it."""
    parts = [p for p in _CLAUSE.split(text) if p]
    mine = [p for p in parts if SELF in p]
    # Only where another clause has a subject of its own, a name ("Humana Soars"), not "Then Dropped".
    others = [p for p in parts if SELF not in p and re.match(r"(?!(Then|And|Also|Later|Now|Still|Yet|Before|After)\b)[A-Z]", p.strip())]
    return " ".join(mine) if mine and others else text


def _dilution(text):
    """(weight, reason) for new shares or shares sold by holders, or None (_DILUTION, _NOT_DILUTION)."""
    if _NOT_DILUTION.search(text) or _DEBT_DEAL.search(text) and not _CONVERTIBLE.search(text):
        return None
    if _HOLDERS_SELL.search(text):
        return -1, "Existing holders are selling shares, which can weigh on the price."
    if _DILUTION.search(text):
        return -1.5, "New shares are being sold, which dilutes current owners."
    return None


def classify(title, sym=None, name=None):
    """(label, reason, score) of a headline about the company `sym` named `name`."""
    text = _mask(title, sym, name)
    question = ("neutral", "A question or opinion piece, not news of an actual event.", 0.0)
    # "Will X beat estimates?" is speculation, not an event, whatever words it uses. A headline that states an event
    # and then asks about it ("Xco Beats Estimates: Time to Buy?") is read on its statement, and stays a question where
    # that says nothing ("IART or PEN: Which Is the Better Value Stock?").
    asked = text.rstrip().endswith("?")
    if asked:
        head = re.split(r":\s|\s[-–|]\s", text, maxsplit=1)
        if len(head) < 2 or head[0].rstrip().endswith("?"):
            return question
        text = head[0]
    if _REPORT.search(text):
        return "neutral", "General commentary, not company news.", 0.0
    text = _own_part(text)
    acts = _ACTS_ON.search(text)
    if acts and _OTHER_TICKER.search(acts.group("after")):
        return "neutral", "The company's own call on another company's stock, not news about it.", 0.0
    text = _FORECAST.sub(" ", text)
    hits = [(w, r) for rx, w, r in _COMPILED if rx.search(text)]
    if _FAIR_VALUE.search(text):
        # An analyst's fair value going up is their view, not the company raising its own outlook (IOVA's "fair value
        # boost").
        hits = [h for h in hits if h[1] != "The company raised its outlook."]
    hits += [h for h in (_move(text), _dilution(text)) if h]
    if not hits and asked:
        return question
    if not hits:
        if _LISTICLE.search(text):
            return "neutral", "General commentary, not company news.", 0.0
        return "neutral", "No clear good or bad news in the headline.", 0.0
    score = sum(w for w, _ in hits)
    # The reason shown is the strongest hit of the label's own sign, so it never argues against the label (TSM's, AMC's
    # and IMAX's in September 2026 did).
    if score >= 1:
        return "bullish", max((h for h in hits if h[0] > 0), key=lambda h: h[0])[1], score
    if score <= -1:
        return "bearish", min((h for h in hits if h[0] < 0), key=lambda h: h[0])[1], score
    lead = max(hits, key=lambda h: abs(h[0]))
    return "neutral", "Mixed signals: " + lead[1][0].lower() + lead[1][1:], score


# Google News sources that rewrite price data into machine-written posts, often citing years-old quarters as news, and
# tag each with a random trailing label ("- Price Surge Stocks"): in September 2026 they supplied 954 of the 2,241
# Google headlines on 252 reports.
BLOCKED_SOURCES = {"dars.gov.et", "careplusvn.com", "siam.in",
                   # Sites whose pages for a stock are its quote, forecast or chart, not news ("Zeo Energy Corporation
                   # Stock Price Today | ZEO Live Chart & News", "Stoke Therapeutics Stock Price Forecast. Should You Buy
                   # STOK?"). Sites that write news by machine from filings or prices (Stock Titan, AD HOC NEWS) stay: for
                   # many small companies they are the only ones to report a reverse split or a chief executive's
                   # dismissal, and PER_PUBLISHER keeps them from filling a list.
                   "tradersunion.com", "stockinvest.us", "intellectia.ai", "heygotrade.com", "moomoo.com", "tikr.com",
                   "valueresearchonline.com", "chartmill.com", "stocktradersdaily.com", "revelio.com", "reveliolabs.com"}


def _parse_rss(raw, source_default):
    items = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src = it.find("source")
        source = (src.text if src is not None and src.text else source_default).strip()
        site = (urllib.parse.urlparse(src.get("url") or "").hostname or "") if src is not None else ""
        if source_default == "Google News" and " - " in title:
            title, source = title.rsplit(" - ", 1)
        try:
            when = email.utils.parsedate_to_datetime(pub).astimezone(dt.timezone.utc) if pub else None
        except (TypeError, ValueError):
            when = None
        if site.removeprefix("www.") in BLOCKED_SOURCES:
            continue
        if title and link:
            items.append({"title": title, "url": link, "source": source, "date": when, "site": site.removeprefix("www.")})
    return items


def _untagged(title):
    """A headline without a trailing ' - tag' or ' | tag' that a site adds ('... Rises 3% - Price Surge Stocks'), where
    what is left is a headline of its own (45 characters or more), so one story with different tags is one story."""
    m = re.match(r"^(.{45,}?)\s+[-|–]\s+[^-|–]{1,60}$", title)
    return m.group(1) if m else title


_GENERIC = {"the", "first", "american", "united", "national", "global", "international", "new", "general", "great", "big"}


def _name_key(name):
    """What a headline must say to name the company (_about): its name's first word, 'Weyco Group Inc.' -> 'weyco', or
    where that is a common word, the name up to its first distinctive word, 'AI Financial Corp' -> 'ai financial',
    'Bank of America' -> 'bank of america', 'The Trade Desk' -> 'trade desk', so a headline about AI, banks or trade
    isn't taken for one about the company. Never a legal suffix (HP Inc.'s 'inc' matched every 'Income') nor a lone
    word of one or two letters, so HP is found by its ticker."""
    words = _name_words(name)
    connector = ("&", "of", "and")
    for i, w in enumerate(words):
        if w.lower() in connector or w.lower() in _GENERIC or w.lower() in _COMMON_WORD or len(w) < 3:
            continue
        run = words[:i + 1]
        if i == 0 or all(x.lower() in connector for x in run[:-1]):
            return w.lower()
        return " ".join(run).lower()
    if len(words) == 1:
        return words[0].lower() if len(words[0]) > 2 else None  # a one-word name is its own key (Target)
    return " ".join(words).lower() if words else None


def _about(title, sym, key):
    """Whether a headline names the company: its ticker as an exact word, or its name's distinctive word at the start of a
    word, so HCA doesn't match 'Healthcare' and Eli Lilly's 'eli' doesn't match 'pipeline', while L3Harris, Five9 and
    Life360 still match their own names. A ticker that is also a word (_WORD_TICKERS: IT, AI, A) counts only in
    parentheses or after an exchange name."""
    tick = re.escape(sym)
    # Never beside "&" or "-" (T in "T-Mobile", P in "S&P").
    bare = rf"(?<![A-Za-z0-9$&-]){tick}(?![A-Za-z0-9&-])"
    marked = rf"\(\s*(?:[A-Z ]+:\s*)?{tick}\s*\)|\b(?:NASDAQ|NYSE)\s*:\s*{tick}(?![A-Za-z0-9])|\${tick}\b|\b{tick} (?i:stock)\b"
    if re.search(marked if sym in _WORD_TICKERS or len(sym) <= 1 else bare, title):
        return True
    # The name's key and any other name it goes by (ALIASES), each as whole words ("royal" isn't "Royalties").
    for k in [key] * bool(key) + [a.lower() for a in ALIASES.get(sym, [])]:
        phrase = r"\s+".join(re.escape(w) for w in k.split())
        if re.search(rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])", title, re.I):
            return True
    return False


# A list draws on three sources: the company's own press releases from its SEC filings (pipeline/press.py), Yahoo
# Finance's feed for the ticker, and Google News, which carries thousands of publishers. Press releases have room kept
# for their newest (PRESS_MOST). Google's headlines join a list in two ways. Where Yahoo's feed has fewer relevant
# headlines than GOOGLE_BELOW, any publisher's (bar the blocked and filtered ones) top the list up, so a list of three or
# four doesn't miss the stories only Google carries (Chesapeake Utilities', ADRX's and KTF's in October 2026). Where
# Yahoo's is full, only an established news publisher's (NEWS_SITES) take room, up to GOOGLE_MOST and PER_PUBLISHER
# each: in a blind review of 60 lists in October 2026, taking any publisher's in every list made more of them worse
# than better, with filing stubs, holdings and moving-average posts, quote pages and one event told four times.
PRESS_MOST = 3
GOOGLE_BELOW = 6
GOOGLE_MOST = 4
PER_PUBLISHER = 2
NEWS_SITES = {"reuters.com", "bloomberg.com", "wsj.com", "cnbc.com", "ft.com", "barrons.com", "marketwatch.com",
              "seekingalpha.com", "investing.com", "benzinga.com", "theglobeandmail.com", "apnews.com", "forbes.com",
              "fortune.com", "businessinsider.com", "morningstar.com", "fool.com", "thestreet.com", "investors.com",
              "nasdaq.com", "nytimes.com", "washingtonpost.com", "axios.com", "techcrunch.com", "theverge.com",
              "fiercebiotech.com", "fiercepharma.com", "fiercehealthcare.com", "endpts.com", "statnews.com",
              "biospace.com", "americanbanker.com", "bizjournals.com", "financialpost.com", "bnnbloomberg.ca",
              "mining.com", "utilitydive.com", "insurancejournal.com", "spglobal.com", "theinformation.com"}
# A Google headline that is a filing's stub rather than news of it ("A director's buy is corrected to 50,000 shares",
# "Stock Titan '10-Q Filings' index", tax-withholding and award-vesting notices), or a price note written from a
# template ("... stock sits below 200-day line", "holds near USD 9.60 after market cap update"); or a page of quotes or
# figures ("Options Overview History", "Advanced Charts", "Number of Employees", "Live Quotes & Charts").
_GOOGLE_NOISE = re.compile(r"\b(form [345]|form 144|tax[- ]withholding|withh(eld|olding)\b.{0,20}\bshares|rsus?\b.{0,20}\bvest"
                           r"|vesting|restricted (stock|share) units?|shares held|holdings? correction|\d+-[kq] filings"
                           r"|sec filing \(|stock (sits|stays|holds|trades) (near|below|above|at)\b|after market cap update"
                           r"|percent below (its )?(yearly|52-week) high|options (chain|overview|volatility|activity)"
                           r"|advanced charts?|number of employees|live quotes|latest stock news and headlines"
                           r"|stock forecast\b|invested \d+ years ago|\$\d+ invested\b|most actively traded)\b", re.I)
# Publishers whose feed titles are cut at a fixed length, often mid-word ("... Moderate Short In", "... (BR) Af").
_CUT_TITLES = {"gurufocus.com", "pluang.com"}

# Pages that show a stock's quote or data rather than news ("... Stock Price, News & Analysis", "... Financials",
# "Should I Buy ...", "Precision Trading with ... Risk Zones"): about 940 of the shown headlines in October 2026.
_QUOTE_PAGE = re.compile(r"\bstock price(,| &| and)? (news|quote|history|chart|trades|analysis)|\bstock quote\b"
                         r"|\bfinancials\b\s*[-–|:]|\bkey financial ratios\b|\bpeer comparison\b|\bshould i buy\b"
                         r"|\brisk zones\b|\binstitutional ownership\b|\bprice prediction\b|\b(dividend history|earnings history"
                         r"|historical prices|holding history|shareholding pattern|stock grades|liquidity mapping|technical signals"
                         r"|rotational strategy|algorithmic entry|price action is critical|press releases about"
                         r"|statistics [-–]|financial statements [-–]|share price, .{0,20}stock news|latest stock price)\b"
                         r"|\bstock (forecasts?|price|analysis)\s*$|\bforecast & analyst predictions\b"
                         r"|[-–]\s*(NASDAQ|NYSE\w*|AMEX)\s*:\s*[A-Z.]+\s*$|\binsider trading\s*$|^(ebit|pretax income|net income) of\b"
                         r"|\bdividend analysis\s*$|\bholds steady at \$"
                         r"|\binteractive stock chart\b|\bearnings date,? (reports|& estimates|and estimates)\b"
                         r"|\bactuals & estimates\b|\bstock price today\b|\blive chart\b|\bwhat is (the )?market cap of\b"
                         r"|\bstock price forecast\b|\bcompetitors( \d{4})?\s*$|\bstock chart\s*$|\bresearch & ratings\b"
                         r"|^\[(?:\d{1,2}-k(?:/a)?|[0-9]{1,2}|sc 13[dg](?:/a)?|s-\d|def 14a|20-f|40-f)\]\s"
                         r"|\bcurrent report \(foreign issuer\)|\|\s*price:|\bsec filing - form\b|\b\d{1,2}-k filings -"
                         r"|\bstock analysis report\b|\bratings, financials\b"
                         r"|\bsec \d{1,2}-k\s*$", re.I)
# Posts a site writes from a template on a price or a consensus, not news ("... Hits New 52-Week Low - What's Next?",
# "... Shares Gap Up - Here's What Happened", "... Stock Price Breaks Above 200-Day Moving Average - Time to Sell?",
# "... Stock Has Average Price Target of $32.71", "... Rated 'Reduce' by Sell-Side Brokerages"): MarketBeat's,
# GuruFocus's and others', about 2,000 of the Google headlines that named their company in October 2026. Only Google's:
# the same words head real stories in Yahoo's feed ("Acme stock jumped today. Here's why", from The Motley Fool).
_TEMPLATED = re.compile(r"\bwhat'?s next\W*$|\bhere'?s (what happened|why)\W*$|\btime to (buy|sell)\W*$"
                        r"|\b(hits?|reaches|sets|touches|marks) (a )?(new )?(52|twelve|12)[- ]week (high|low)\b"
                        r"|\b(out|under)performs? (its )?competitors\b"
                        r"|\b(crosses|breaks|moves|trades|passes) (above|below) .{0,30}\bmoving average\b"
                        r"|\baverage (price target|rating|recommendation)\b|\bconsensus (rating|recommendation)\b"
                        r"|\brated .{0,30}\bby (wall street|sell-side|brokerages|analysts)\b|\bshares gap (up|down)\b"
                        r"|\bgf (value|score)\b|\btrading (up|down) [\d.]+%\s*$|\bconsensus price target\b"
                        r"|\bwould have made\b|\bwhat'?s going on with\b", re.I)
# A fund's filing of its holdings, as MarketBeat words them ("XYZ Advisors Acquires New Position in ..."): at most one
# per list, so they don't crowd out news (Amazon's showed nine).
_HOLDINGS = re.compile(r"\b(acquires|buys|sells|trims|raises|lowers|boosts|increases|decreases|reduces|grows|cuts|takes|"
                       r"initiates|makes|has|lifts|purchases)\b.{0,30}\b(new )?(position|stake|holdings?)\b.{0,10}\b(in|of)\b"
                       r"|\b(stock|shares|stake|position|holdings)\b.{0,25}\b(sold|acquired|purchased|bought|increased|decreased"
                       r"|raised|lowered|trimmed|lessened|boosted|cut)\b.{0,5}\bby\b", re.I)
# A company's filing of insider trades or share-award vesting: several in two days are one story (USGO showed five).
_FILING = re.compile(r"\b(form 4|rsus?|restricted stock units?|vesting|insider (buy|sell|purchase|sale)s?)\b", re.I)
# One earnings call is one story, whether a site posts its transcript, its summary or its highlights.
_CALL = re.compile(r"\b(earnings call|conference call|call transcript|call highlights|call summary)\b", re.I)


def _same_story(it, kept, sym, name):
    """Whether a headline tells a story already kept, published within two days of it: half or more of its words
    (with the company's name and ticker taken out) in the other's (CRGY's and Devon's sides of one merger, one Eni
    result reworded), or another post on the same earnings call."""
    words = lambda t: {w for w in re.findall(r"[a-z0-9]+", _mask(t, sym, name).lower()) if len(w) > 2 and w != SELF.lower()}
    mine = words(it["title"])
    for k in kept:
        if it["date"] and k["date"] and abs((it["date"] - k["date"]).days) > 2:
            continue
        if any(rx.search(it["title"]) and rx.search(k["title"]) for rx in (_CALL, _FILING)):
            return True
        theirs = words(k["title"])
        if mine and theirs and len(mine & theirs) >= 0.5 * min(len(mine), len(theirs)) and len(mine & theirs) >= 4:
            return True
    return False


def _ny_date(when):
    """A headline's date in New York, as the site's own 'Updated' date is: a story published at 9pm in New York is that
    day's, not the next day's in UTC (about one headline in ten in September 2026)."""
    try:
        from zoneinfo import ZoneInfo
        local = when.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Without the time zone database (a Windows machine without tzdata): Eastern Daylight Time from 2am on the
        # second Sunday of March to 2am on the first Sunday of November, Eastern Standard Time otherwise.
        y = when.year
        first = lambda month: dt.datetime(y, month, 1, tzinfo=dt.timezone.utc)
        sunday = lambda d, nth: d + dt.timedelta(days=(6 - d.weekday()) % 7 + 7 * (nth - 1))
        start, end = sunday(first(3), 2) + dt.timedelta(hours=7), sunday(first(11), 1) + dt.timedelta(hours=6)
        local = when - dt.timedelta(hours=4 if start <= when < end else 5)
    return local.strftime("%Y-%m-%d")


def _feed(url, source):
    """A feed's items, or none where it can't be fetched or read (counted in FAILED, which build reports)."""
    try:
        return _parse_rss(web_get(url), source)
    except Exception:
        with _failed_lock:
            FAILED[source] += 1
        return []


FAILED = collections.Counter()  # feeds that couldn't be fetched or read this run, by source
_failed_lock = threading.Lock()


def headlines(sym, name, max_items=10, max_age_days=90, press=()):
    """Up to `max_items` headlines about the company from its press releases (`press`, from pipeline/press.py), Google
    News and Yahoo Finance, each list's room shared among them (PRESS_MOST, GOOGLE_MOST), newest first."""
    yahoo = _feed(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym.replace('.', '-')}&region=US&lang=en-US",
                  "Yahoo Finance")
    q = urllib.parse.quote(f'"{sym}" {name.split(",")[0]} stock')
    google = _feed(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", "Google News")
    key = _name_key(name)
    now = dt.datetime.now(dt.timezone.utc)
    by_date = lambda xs: sorted(xs, key=lambda x: x["date"] or now - dt.timedelta(days=999), reverse=True)
    # Only fresh headlines that name the company count: a ticker feed mixes in general market stories and other
    # companies' news, which used to fill out a short list (no headline on NVDA's or DKS's list in September 2026 was
    # about them). A press release always names it (press.release).
    relevant = lambda xs: by_date([i for i in xs if (not i["date"] or (now - i["date"]).days <= max_age_days)
                                   and _about(i["title"], sym, key) and not _QUOTE_PAGE.search(i["title"])])
    # Nor, from Google's publishers, a post written from a template (_TEMPLATED) or a question ("Can Occidental Petroleum
    # Keep Surging as Oil-and-Gas Shares Gather Pace?", "Is Bit Digital the Penny Stocks Stock Everyone's Talking About
    # Today?"), which asks rather than reports.
    press, yahoo = relevant(press), relevant(yahoo)
    google = [i for i in relevant(google) if not _TEMPLATED.search(i["title"]) and not _GOOGLE_NOISE.search(i["title"])
              and not i["title"].rstrip().endswith("?")
              and not (i.get("site") in _CUT_TITLES and not re.search(r"[.!?)%'\"]$", i["title"].rstrip()))]
    # A full Yahoo list takes only established publishers' headlines from Google (NEWS_SITES). Full in distinct stories:
    # one story told twice, or a run of fund-holdings filings (one of which a list keeps), doesn't fill it.
    stories = {re.sub(r"\W+", "", _untagged(i["title"]).lower())[:60] for i in yahoo if not _HOLDINGS.search(i["title"])}
    if len(stories) + any(_HOLDINGS.search(i["title"]) for i in yahoo) >= GOOGLE_BELOW:
        google = [i for i in google if ".".join((i.get("site") or "").split(".")[-2:]) in NEWS_SITES]

    seen, kept, filler, publishers = set(), [], [0], collections.Counter()

    def take(it, cap=None):
        """Keeps a headline unless it tells a story already kept (the first copy stays: a press release over the
        wire's or Yahoo's copy of it), is a fund's holdings filing past the first, or, from Google (`cap` given), its
        publisher already has `cap` there (Google's own "Yahoo Finance" articles aren't counted with Yahoo's feed)."""
        key60 = re.sub(r"\W+", "", _untagged(it["title"]).lower())[:60]
        if len(kept) >= max_items or key60 in seen or _same_story(it, kept, sym, name) \
                or cap and publishers[it["source"]] >= cap:
            return False
        if _HOLDINGS.search(it["title"]):
            filler[0] += 1
            if filler[0] > 1:
                return False
        seen.add(key60)
        kept.append(it)
        if cap:
            publishers[it["source"]] += 1
        return True

    for it in press[:PRESS_MOST]:
        take(it)
    # Yahoo's newest, leaving room for Google's (as many as it has from different publishers, up to GOOGLE_MOST).
    room = max_items - min(GOOGLE_MOST, len({i["source"] for i in google}) * PER_PUBLISHER, len(google))
    for it in yahoo:
        if len(kept) >= room:
            break
        take(it)
    shown = len(kept)
    for it in google:
        if len(kept) - shown >= GOOGLE_MOST:
            break
        take(it, PER_PUBLISHER)
    # Then whatever room is left, newest first from all three.
    for it in by_date(yahoo + google + press):
        take(it, PER_PUBLISHER if it in google else None)
    out = []
    for it in kept:
        label, reason, score = classify(it["title"], sym, name)
        out.append({
            "title": it["title"], "url": it["url"], "source": it["source"],
            "date": _ny_date(it["date"]) if it["date"] else None,
            "label": label, "reason": reason, "score": score,
        })
    return sorted(out, key=lambda x: x["date"] or "", reverse=True)


# The fewest headlines that make an overall news mood (overall): with one or two, one story would decide it.
MOOD_MIN = 3


def overall(items):
    if not items:
        return {"label": "none", "score": 50, "text": "No recent headlines found for this company."}
    bull = sum(1 for i in items if i["label"] == "bullish")
    bear = sum(1 for i in items if i["label"] == "bearish")
    n, rest = len(items), len(items) - bull - bear
    net = sum(max(-2, min(2, i["score"])) for i in items)
    # A short list moves the score less, so one headline can't put it at 1 or 100.
    score = round(max(1, min(100, 50 + net / max(n, 5) * 25)))
    if n < MOOD_MIN:
        kinds = ["good"] * bull + ["bad"] * bear + ["neutral"] * rest
        if n == 1:
            said = f"the one headline is {kinds[0]}"
        elif kinds[0] == kinds[1]:
            said = f"both headlines are {kinds[0]}"
        else:
            said = f"one headline is {kinds[0]} and one {kinds[1]}"
        return {"label": "neutral", "score": 50, "text": f"Too few headlines to judge: {said}."}
    # One good headline among nine neutral ones is not a positive news flow: require a real share of the headlines,
    # and at least two of the winning kind.
    tilt = lambda a, b: a >= 2 and ((a - b >= 2 and a >= n / 4) or (a > b and a >= 0.4 * n))
    if tilt(bull, bear):
        word = "Mostly positive" if bull >= n / 2 else "Leaning positive"
        label, text = "bullish", f"{word}: {bull} good, {bear} bad and {rest} neutral headlines."
    elif tilt(bear, bull):
        word = "Mostly negative" if bear >= n / 2 else "Leaning negative"
        label, text = "bearish", f"{word}: {bear} bad, {bull} good and {rest} neutral headlines."
    else:
        label, text = "neutral", f"Mixed or quiet: {bull} good, {bear} bad and {rest} neutral headlines."
    return {"label": label, "score": score, "text": text}


def all_headlines(pairs, workers=6, press=None):
    """pairs: [(symbol, name)], press: {symbol: [press releases]} -> {symbol: [items]}"""
    press = press or {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip([p[0] for p in pairs], pool.map(lambda p: headlines(*p, press=press.get(p[0], ())), pairs)))
