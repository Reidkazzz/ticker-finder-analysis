# Ticker Finder & Analysis

Find undervalued US stocks and understand exactly why. Three tools, built on SEC filings and written for people who are new to the market.

| Tool | Question it answers |
|---|---|
| **Sector screener** | Which small US companies are cheap, profitable and low on debt right now? |
| **Insider buying** | Who inside a company is buying its stock with their own money? |
| **Ticker report** | Is this one stock healthy, and is its price fair? |

The site is static HTML on GitHub Pages. A GitHub Actions job refreshes all the data every weekday evening, so there are no servers to run and no API keys to manage.

## The three tools

### 1. Sector screener

A company passes only if it clears every check:

| Check | Rule |
|---|---|
| Price to sales | under 2 |
| Market value | under $3B (bonus points under $2B) |
| Debt | long-term debt / equity under 0.5. Best: no debt. Next best: more cash than debt |
| Net profit margin | 8% or more |
| Location | headquartered **and** incorporated in a US state (rules out ADRs and offshore shells) |
| Viability | equity above zero, free cash flow above zero, profitable in 2 of the last 3 years, sales not down more than 5% |

Flags, not rules: **Out of favor** (25%+ below the 52-week high) and **Undiscovered** (two or fewer analysts). Passing companies are ranked by a 0 to 100 score, and every point is itemized on the page.

### 2. Insider buying

- Sources: SEC **Form 4** (executives, directors, 10%+ owners) and **Schedule 13D** (new 5%+ holders who may seek influence), read from EDGAR's daily index.
- Only open-market purchases (transaction code `P`) count. Option exercises, grants and sales are excluded.
- Shows who bought, their role, the dollar amount, the price paid, and how today's price compares.
- **Tier 1:** $10M+ bought in the last 30 days. **Tier 2:** $1M+ total, or any buyer whose stake grew 20%+.
- Sorted newest filing first. Each weekday's filings are analyzed that evening.

### 3. Ticker report

- **News:** recent headlines, each labeled bullish, bearish or neutral with the reason, plus an overall read. Labels come from transparent phrase rules (see `pipeline/news.py`), not a black box.
- **Health:** 16 measures scored 1 to 100. Raw amounts are scored through the ratio that gives them meaning (cash becomes net cash as a share of market value, share count becomes dilution, revenue becomes growth and consistency). Valuation bars rank the company against its industry peers.
- **Verdict:** undervalued, fair or overvalued, with an estimated fair-value range from peer price-to-sales, peer price-to-earnings and a simple 5-year cash-flow model. It is an estimate, not a price target, and not investment advice.

## Data sources

| Data | Source |
|---|---|
| Financial statements | SEC XBRL frames API (`data.sec.gov`) |
| Incorporation state, HQ | SEC company submissions |
| Form 4 and 13D filings | EDGAR daily form index |
| Listings, sector, industry, market value | Nasdaq's public stock screener |
| Prices, 52-week range, analyst count | Yahoo Finance public endpoints |
| Headlines | Yahoo Finance RSS, Google News RSS as a fallback |

Financials use each company's latest fiscal year and latest balance sheet.

## Automatic updates

`.github/workflows/update.yml` runs at 03:15 UTC Tuesday through Saturday. That is 11:15pm New York time in summer and 10:15pm in winter, Monday through Friday, after EDGAR stops accepting filings at 10pm. Each run:

1. analyzes that day's Form 4 and 13D filings and rechecks the previous day for late arrivals,
2. refreshes prices, financials, the screener, headlines and every ticker report,
3. publishes the site to GitHub Pages.

Filings and results are cached between runs, so pushing a design change redeploys in about a minute without redoing the analysis. To force a fresh analysis, open **Actions**, choose **Analyze and publish**, then **Run workflow**. The first run backfills 30 days of filings and takes about 90 minutes. Later runs take about 45 minutes.

**SEC contact (recommended):** the SEC asks automated tools to identify themselves with a contact email. Add a repository secret named `SEC_USER_AGENT` with a value like `Your Name you@yourdomain.com` (Settings, then Secrets and variables, then Actions). The SEC rejects github.com addresses. Without the secret, the pipeline sends a placeholder contact.

GitHub pauses scheduled workflows in public repositories after 60 days with no commits. Any push turns them back on.

## Run it locally

Requires Python 3.10+. No packages to install.

```bash
python -m pipeline.build --limit 300      # quick sample of 300 stocks
python -m pipeline.build                  # everything, about 45 minutes after the first run
python -m http.server 8123 --directory site
```

Then open http://localhost:8123.

## Project layout

```
pipeline/            Python, standard library only
  build.py           runs every step and writes site/data/
  universe.py        US-listed common stocks
  fundamentals.py    SEC financial statements -> ratios
  market.py          prices, 52-week range, analyst count
  screener.py        tool 1
  insiders.py        tool 2
  news.py            headline labeling rules
  report.py          tool 3: health scores and fair value
site/                static site served by GitHub Pages
  index.html         cover page
  screener.html, insiders.html, report.html
  assets/            CSS and JavaScript, no build step
```

## Disclaimer

Educational research tool. Nothing here is investment advice, a recommendation or a price target. Data can be late, incomplete or wrong. Always read the underlying filings and do your own research.
