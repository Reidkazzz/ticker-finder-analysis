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
- **Verdict:** undervalued, fair or overvalued, with an estimated fair-value range from peer price-to-sales, peer price-to-earnings and a cash-flow model. It is an estimate, not a price target, and not investment advice.
- **Cash-flow model:** free cash flow counted after stock-based pay (and after software and construction spending some companies report on their own lines) and before interest, which is added back after tax on up to 10% of the debt read from the latest balance sheet, since that debt is subtracted at the end. It must be positive in each of the last three years it can read. Its level is the middle of those years' share of sales applied to the latest sales, or the middle year's cash flow itself when the sales figures look incomplete or jump from year to year. It grows at the average of last year's sales growth and the yearly rate over the last three years, each kept between 0% and 12%, for 5 years, then moves evenly to 2.5% by year 10, and is discounted at 8% for companies worth $10B or more, 9% from $2B, 10% from $300M and 11% below, the riskiest. Net debt is then subtracted. It is left out when net debt is more than the company's market value or half the business value the model finds, since the value left for shareholders would then swing on small changes in the forecast, and when free cash flow comes out larger than sales. A company that lost money and has no cash-flow value gets no verdict; its report says why and compares its price to sales with its peers'.
- **REITs** are valued on funds from operations (FFO), the profit measure REITs report: net income left to common shareholders plus depreciation and amortization, less gains on selling property, plus property write-downs. It is worked out from SEC filings, so it can differ from the FFO a REIT reports. A REIT that owns property is compared only with other such REITs, preferably of its own property type, and valued on peer price to FFO and peer value to sales, with no cash-flow model; its health check scores FFO margin, return on equity on FFO and its FFO track record. A REIT that mostly lends, or whose leases are booked as loans, is valued on peer price to earnings, and a lender also on peer price to book. Timber REITs are valued as other companies are. A REIT whose debt can't be read from its filings gets no debt measures and no value to sales, and a REIT or a bank whose share count changed after its latest SEC filing gets no fair value.
- **Banks** are handled the way bank analysts handle them. Revenue is net interest income plus noninterest income. They are compared only with other banks, matched on return on tangible equity, return on assets, efficiency and size, and valued on peer price to tangible book value and peer price to earnings, with no sales multiple or cash-flow model. Their health check scores return on equity, return on assets, the efficiency ratio and tangible equity to assets instead of profit margin, cash flow and debt measures. A bank gets no fair value when it lost money, when its newest full year of results is more than a year old, when its share count changed after its latest balance sheet, or when an acquisition's loan-loss allowance swamped last year's earnings, and its tangible book value is left blank when it pays preferred dividends but its filings don't give the amount of its preferred stock.

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
  reit_types.py      REIT property types by SEC company number, for picking REIT peers
site/                static site served by GitHub Pages
  index.html         cover page
  screener.html, insiders.html, report.html
  assets/            CSS and JavaScript, no build step
```

## Disclaimer

Educational research tool. Nothing here is investment advice, a recommendation or a price target. Data can be late, incomplete or wrong. Always read the underlying filings and do your own research.
