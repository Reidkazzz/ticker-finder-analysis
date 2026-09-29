import { attachSearch, chrome, date, esc, load, meter, money, pct, price, searchMarkup, signClass } from "./common.js";

chrome();

const host = document.querySelector("[data-report]");
const searchHost = document.getElementById("report-search");
searchHost.innerHTML = searchMarkup("report-q", "Search another ticker or company");
attachSearch(searchHost.querySelector("form"), {
  onPick: (sym) => {
    history.pushState(null, "", `?t=${encodeURIComponent(sym)}`);
    show(sym);
  },
});
window.addEventListener("popstate", () => route());
// Peer links open that company's report in place, like a search pick. A jump to the peer list scrolls without
// touching the address, since a #fragment there would re-run the router and reload the report.
host.addEventListener("click", (e) => {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const jump = e.target.closest("[data-jump]");
  if (jump) {
    e.preventDefault();
    document.getElementById(jump.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const peer = e.target.closest("a[data-sym]");
  if (peer) {
    e.preventDefault();
    history.pushState(null, "", `?t=${encodeURIComponent(peer.dataset.sym)}`);
    show(peer.dataset.sym);
  }
});

const VERDICT = {
  undervalued: { word: "Undervalued", tag: "tag-up" },
  fair: { word: "Fairly valued", tag: "tag-mid" },
  overvalued: { word: "Overvalued", tag: "tag-down" },
};
const NEWS_TAG = { bullish: "tag-up", bearish: "tag-down", neutral: "", none: "" };
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
const CONFIDENCE = {
  higher: "All three methods were available. They can still disagree, so compare the three estimates above before leaning on the range.",
  moderate: "Two methods were available.",
  low: "Only one method was available, so treat this range loosely.",
};

function chart(points) {
  if (!points || points.length < 4) return `<p class="faint" style="margin-top:18px;font-size:14px">No price history available.</p>`;
  const W = 600, H = 190, pad = 4;
  const ys = points.map((p) => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const x = (i) => pad + (i / (points.length - 1)) * (W - pad * 2);
  const y = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - pad * 2);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const up = ys[ys.length - 1] >= ys[0];
  const col = up ? "var(--up)" : "var(--down)";
  const first = new Date(points[0][0] * 1000).toISOString().slice(0, 10);
  const change = ys[ys.length - 1] / ys[0] - 1;
  return `<div class="chart">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Weekly closing price over the last year, ${pct(change, 1, true)}">
      <defs><linearGradient id="fade" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".18"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
      <path d="${line}L${x(points.length - 1)},${H}L${x(0)},${H}Z" fill="url(#fade)"/>
      <path d="${line}" fill="none" stroke="${col}" stroke-width="1.75" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    </svg>
    <div class="chart-foot"><span>${esc(date(first, { month: "short", year: "numeric" }))}</span><span class="${signClass(change)}">${pct(change, 1, true)} over the year</span><span>Now</span></div>
  </div>`;
}

function rangeViz(v, px) {
  const lo = Math.min(v.low, px) * 0.85, hi = Math.max(v.high, px) * 1.15;
  const at = (n) => ((n - lo) / (hi - lo)) * 100;
  const pLeft = at(px);
  return `<div class="range" role="img" aria-label="Estimated fair value ${price(v.low)} to ${price(v.high)}, current price ${price(px)}">
    <div class="band" style="left:${at(v.low)}%;width:${at(v.high) - at(v.low)}%"></div>
    <div class="mark" style="left:${pLeft}%"></div>
    <div class="mark-label" style="left:${Math.min(Math.max(pLeft, 8), 92)}%">Price ${price(px)}</div>
    <div class="edge" style="left:${at(v.low)}%">${price(v.low)}</div>
    <div class="edge" style="left:${at(v.high)}%">${price(v.high)}</div>
  </div>`;
}

// Shown near the verdict and the profit bars when last year's profit included one-time items, and for a REIT, how its
// funds from operations (FFO) come from its net income.
function oneTimeNote(r, style = "margin-top:16px") {
  const n = r.fundamentals?.one_time_note;
  // A paragraph rather than a div, so the first bar after it keeps its :first-of-type styling.
  return n ? `<p class="notice" style="${style}"><i class="ph ph-info" aria-hidden="true"></i><span>${esc(n)}</span></p>` : "";
}

// The model's track record by kind of company, from a point-in-time backtest of this code on four past dates (the
// README's "How accurate is it?"): rerun on the filings public then, against each stock's next 12 months.
const TESTED = "We re-ran this model as it would have run in late September of 2022, 2023, 2024 and 2025, using only the SEC filings public at the time, and followed each stock's return over the next 12 months, dividends included.";
const LISTED = "The test could only include companies still listed today, and four years is a short record.";
const TRACK_RECORD = {
  bank: `${TESTED} For banks it helped: in each of the four years, the banks it called undervalued did better than those it called overvalued, by 5.5 to 16 percentage points, about 9 on average. ${LISTED}`,
  financial: `${TESTED} For financial companies other than banks the record is mixed: the ones it called undervalued did better than those it called overvalued in three of the four years, by 3 to 19 percentage points, but worse in the other, by 9.5. ${LISTED}`,
  reit: `${TESTED} For REITs the verdicts did not help: in three of the four years, the REITs it called undervalued did worse than those it called overvalued. Read the verdict as how the price compares with similar REITs, not as a forecast. ${LISTED}`,
  other: `${TESTED} For companies outside finance, most of those valued here, the verdicts barely predicted which stocks would do better. The ones called undervalued beat those called overvalued by 0.5 to 5 percentage points in three of the years and trailed them by 5.5 in the other, close to even overall. Calls on the very largest companies were no better: the best-known ones it called overvalued, such as Nvidia and Eli Lilly, more often went on to beat other large companies than to trail them. Read the verdict as how today's price compares with similar companies and with a simple forecast of the company's cash, not as a prediction. ${LISTED}`,
};

// What kind of company the fair value treats this as, which decides the methods it uses (report.fair_value).
function valuationKind(r) {
  const f = r.fundamentals || {};
  if (f.bank) return "bank";
  if (f.reit === "property") return "reit";
  if (r.sector === "Finance" || f.reit === "mortgage") return "financial";
  return "other";
}

// A plain-English account of how the estimate is made and how well it has done, under the methods. The discount rates
// and growth limits repeat report.py's CF_RATES, CF_SMALL_RATE, CF_RATE_BASIS, CF_GROWTH_CAP and CF_TERMINAL, and the
// track record comes from the point-in-time backtest described in the README.
function howItWorks(r) {
  const kind = valuationKind(r);
  const item = (title, text) => `<li><b>${title}</b><span>${text}</span></li>`;
  const peersLine = kind === "bank"
    ? "The 10 US-listed banks most like this one in return on tangible equity, return on assets, efficiency and size."
    : kind === "reit"
      ? "The 10 US-listed REITs that own property most like this one, preferring its own property type."
      : "The 10 US-listed companies most like this one in profit margins, cash flow, sales growth, size and sales per dollar of assets, preferring its own industry.";
  const items = [item("Similar companies", `${r.peer_basis ? esc(r.peer_basis) : peersLine} Each estimate that starts with Peer applies their median multiple to this company's own figures: what the stock would be worth if the market priced it like them.`)];
  if (kind === "other") {
    items.push(item("Value to sales", "What each similar company's whole business costs, its debt included and its cash taken off, per dollar of yearly sales. That multiple times this company's sales, less its debt and any other claims that come before its shares, plus its cash, gives the value of its shares."));
    items.push(item("Cash-flow model", "Starts from free cash flow: the cash the business brings in each year after paying for its operations, its equipment and the stock it pays staff with. The cash flow grows at the company's recent sales growth, kept between 0% and 12% a year, for 5 years, then slows evenly to 2.5% a year, about the pace of inflation, by year 10 and keeps that pace after."));
    items.push(item("The discount rate", "Money that arrives later is worth less than money today, so each future year's cash is shrunk by a discount rate: the yearly return investors want for the risk of owning the stock. For companies worth $10B or more it is 8%: about 4% from safe US government bonds plus about 4% for the extra risk of stocks, per Damodaran's January 2026 figures. Smaller companies are riskier, so the rate goes up a point for each step down in size: 9% from $2B, 10% from $300M and 11% below that. It is not moved with daily bond yields: in our tests, a rate that moved with bond yields and with how much each stock moves with the market did not make the verdicts more accurate."));
    // Only where the estimates show a price to earnings: for comparison when other estimates count, or counted when
    // it is the only one (report.fair_value, PE_COUNTS). Data written before PE_COUNTS has no "counted", and there a
    // price to earnings beside other estimates counted with them, so it gets neither item.
    const shown = r.valuation?.methods || [];
    const pe = shown.find((m) => m.name === "Peer price to earnings");
    if (pe && pe.counted === false) {
      items.push(item("Price to earnings, shown for comparison", "Similar companies' price to earnings appears under the estimates but is not counted. In our tests on 2022 to 2025 data, leaving it out made the verdicts for companies outside finance slightly better at telling which stocks would do better, since one year's profit swings more than sales and cash flow. The gain was small."));
    } else if (pe && shown.length === 1) {
      items.push(item("Price to earnings", "Similar companies' price to earnings, applied to this company's profit, is the only estimate that could be used here, so it counts. Where value to sales or the cash-flow model can be used, price to earnings is shown for comparison only."));
    }
  } else if (kind === "bank") {
    items.push(item("Price to tangible book and price to earnings", "Tangible book value is what the shareholders own, less goodwill and other intangible assets. Banks are valued this way and on their earnings, not on sales or a cash-flow model, since their borrowing is their raw material."));
  } else if (kind === "reit") {
    items.push(item("Price to FFO and value to sales", "Funds from operations (FFO) is the profit measure REITs report: net income with property depreciation added back and property sales left out."));
  } else {
    items.push(item("Price to sales and price to earnings", "Insurers, lenders and other financial companies borrow and invest as part of the business, so they are valued on similar companies' price to sales and price to earnings (a mortgage REIT on price to earnings, and a lender also on price to book), with no cash-flow model."));
  }
  items.push(item("The verdict", "The midpoint is the middle of the estimates counted (with two, their average). A midpoint more than 50% above or below the price needs two independent estimates to agree, and estimates from the same similar companies count as one. The range spans the estimates and is at least 10% either side of the midpoint. Undervalued means today's price is below the range, overvalued means above it."));
  items.push(item("How well has this worked?", TRACK_RECORD[kind]));
  return `<details class="disclose how" style="margin-top:18px">
    <summary><i class="ph ph-question" aria-hidden="true" style="margin:0;color:var(--text-2);transform:none"></i>How this fair value is worked out<i class="ph ph-caret-down" aria-hidden="true"></i></summary>
    <div class="body"><ul class="how-list">${items.join("")}</ul></div>
  </details>`;
}

function verdictPanel(r) {
  const v = r.valuation;
  if (!v || !VERDICT[v.verdict]) {
    // A fair value withheld for a stated reason (older data has none, so it gets the general explanation).
    const why = v?.withheld ? esc(v.withheld) : `There is not enough reliable data to estimate a fair value. This happens when we could not find a full year of financials in the company's SEC filings,
      which is common for new listings and foreign companies, foreign banks among them. It also happens when a company is losing money and has no positive cash flow to value,
      since comparing it with others on sales says little about what it is worth.`;
    return `<section class="panel panel-pad verdict"><h2>Verdict</h2>
      <p class="word" style="font-size:28px">Not enough data</p>
      <p class="line">${why}</p>${oneTimeNote(r)}</section>`;
  }
  const up = v.upside;
  const conf = v.confidence_note || CONFIDENCE[v.confidence];
  return `<section class="panel panel-pad verdict">
    <h2>Verdict</h2>
    <p class="word ${v.verdict}">${VERDICT[v.verdict].word}</p>
    <p class="line">Estimated fair value <b>${price(v.low)} to ${price(v.high)}</b>.${up == null ? "" : ` The midpoint of <b>${price(v.mid)}</b> is
      <b class="${signClass(up)}">${pct(Math.abs(up), 0)} ${up >= 0 ? "above" : "below"}</b> today's price.`}${v.limit_note ? ` ${esc(v.limit_note)}` : ""}</p>
    ${r.price ? rangeViz(v, r.price) : ""}
    <div class="methods">${v.methods.map((m) => `<div class="method-row${m.counted === false ? " ref" : ""}"><b>${esc(m.name)}${m.counted === false ? ` <span class="tag">For comparison</span>` : ""}</b><span class="v">${price(m.value)}</span><p>${esc(m.note)}</p></div>`).join("")}</div>
    ${r.peers?.length && v.methods.some((m) => m.name.startsWith("Peer")) ? `<p class="fine"><a href="#peers" data-jump="peers">See the ${r.peers.length} similar companies</a> behind the peer estimates.</p>` : ""}
    ${oneTimeNote(r)}
    <p class="fine">${esc(conf)} This is an estimate, not a price target, and not investment advice.</p>
    ${howItWorks(r)}
  </section>`;
}

function pricePanel(r) {
  const fromHigh = r.high52 ? r.price / r.high52 - 1 : null;
  return `<section class="panel panel-pad chart-panel">
    <h2>Price, last 12 months</h2>
    ${chart(r.weekly)}
    <div class="stat-row">
      <div>52-week low<b>${price(r.low52)}</b></div>
      <div>52-week high<b>${price(r.high52)}</b></div>
      <div>From high<b class="${signClass(fromHigh)}">${fromHigh == null ? "n/a" : pct(fromHigh, 0, true)}</b></div>
    </div>
  </section>`;
}

// Revenue by quarter (fundamentals.quarters: up to 12 quarters, oldest first, back to back; a quarter the filings don't
// give soundly has null figures) with gross profit and gross margin for the fiscal year and the last 12 months. Reports
// written before these fields existed have no "quarters" key and get no section. Gross figures show only where the
// health check has a gross margin bar: banks, insurers, other financial companies and REITs have none.
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthYear = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}` : "");
const shortMonth = (iso) => `${MON[+iso.slice(5, 7) - 1]} ’${iso.slice(2, 4)}`;
const fullDate = (iso) => date(iso, { month: "short", day: "numeric", year: "numeric" });
// One decimal for millions and billions ($109.4B, $375.0M), floored like money() so a figure never reads larger.
const amount = (n) => money(n, Math.abs(n) >= 1e6 ? { digits: 1 } : {});
// Growth on the year-ago quarter: one decimal below 100%, whole percents above.
const growth = (g) => (g == null ? "" : pct(g, Math.abs(g) >= 1 ? 0 : 1, true));
// Why a quarter with revenue has no growth figure (fundamentals._quarter_series' yoy_withheld), or null where its
// year-ago quarter isn't there.
const WITHHELD = {
  restated: "Not compared with the same quarter a year before: a later filing restated one of the two (for example after a business was sold), so they aren't on the same footing.",
  unchecked: "Not compared with the same quarter a year before: the two figures come from different filings that couldn't be checked against each other.",
  no_base: "Not compared with the same quarter a year before, which had no revenue.",
};

// Round axis steps (1, 2, 2.5 or 5 times a power of ten) giving three or four gridlines above zero, and always at
// least two gridlines, so the plot has a height.
function ticks(lo, hi) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / 3.5;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
  if (out.length < 2 || out[out.length - 1] < hi) out.push(+(out[out.length - 1] + step).toPrecision(12));
  return out;
}

function axisLabel(v, top) {
  if (v === 0) return "$0";
  const [d, u] = top >= 1e12 ? [1e12, "T"] : top >= 1e9 ? [1e9, "B"] : top >= 1e6 ? [1e6, "M"] : [1e3, "K"];
  return `${v < 0 ? "-" : ""}$${+(Math.abs(v) / d).toFixed(2)}${u}`;
}

// A quarter's name for its tooltip, its label for screen readers and the table: its fiscal quarter where the pipeline
// could number it, and its dates, or only the month it ended around where they are estimated (a blank quarter in a gap
// of two or more).
const quarterName = (q) => q.dates_estimated ? `Quarter ended around ${monthYear(q.end)}`
  : `${q.fiscal_quarter ? `Fiscal Q${q.fiscal_quarter}, ` : "Quarter "}${fullDate(q.start)} to ${fullDate(q.end)}`;

function quarterSection(r) {
  const f = r.fundamentals;
  if (!f || !Array.isArray(f.quarters)) return "";
  const qs = f.quarters;
  // Nothing to plot unless some quarter had revenue above zero, and nothing is shown where the sales figure looks
  // incomplete (the health check then measures no sales-based figure either).
  const plotted = !f.sales_doubt && qs.some((q) => q.revenue > 0);
  const gbar = (r.health || []).flatMap((g) => g.bars).find((b) => b.key === "gross_margin");
  const bank = !!f.bank;
  if (!plotted && !gbar) return "";
  const what = bank ? "revenue (interest earned less interest paid, plus fees)" : "revenue";
  const head = `<div class="h-top" id="quarters"><div><h2>Revenue by quarter</h2>
    <p>${plotted ? `Each bar is one quarter's ${what}, from the company's SEC filings, with its growth on the same quarter a year before.`
      : "Revenue and gross margin from the company's SEC filings."}</p></div></div>`;
  const none = f.sales_doubt ? "Quarter-by-quarter revenue isn't shown, since the sales figure in the company's SEC data looks incomplete: profit came out larger than sales."
    : qs.length ? "Quarter-by-quarter revenue isn't shown, since the company reported no revenue in these quarters."
    : "Quarter-by-quarter figures aren't available for this company. Companies based outside the US often file only yearly reports with the SEC, and a quarter is left out when its figures in the filings don't check out.";
  const chart = plotted ? quarterChart(qs, f.quarters_stale) : `<p class="q-none">${none}</p>`;
  return `${head}<section class="panel q-panel">${chart}${quarterStats(f, plotted, gbar)}${plotted ? quarterTable(qs, !!gbar && qs.some((q) => q.gross_profit != null)) : ""}</section>`;
}

function quarterChart(qs, stale) {
  const vals = qs.filter((q) => q.revenue != null).map((q) => q.revenue);
  const t = ticks(Math.min(0, ...vals), Math.max(...vals));
  const lo = t[0], hi = t[t.length - 1];
  const at = (v) => ((v - lo) / (hi - lo)) * 100; // % up from the plot's bottom
  const base = at(0);
  const n = qs.length;
  const derived = qs.some((q) => q.derived && q.revenue != null);
  const odd = qs.some((q) => q.extra_days && q.yoy != null);
  const restated = qs.some((q) => q.yoy_withheld === "restated");
  const unchecked = qs.some((q) => q.yoy_withheld === "unchecked");
  const first = qs.find((q) => q.revenue != null), last = qs[n - 1];
  const summary = `Revenue for the ${n} quarters to ${monthYear(last.end)}, from ${amount(first.revenue)} in the quarter ended ${monthYear(first.end)} to ${amount(last.revenue)} in the quarter ended ${monthYear(last.end)}${last.yoy != null ? `, ${last.yoy >= 0 ? "up" : "down"} ${pct(Math.abs(last.yoy), 1)} on a year before` : ""}. The table below lists every quarter.`;
  const cols = qs.map((q, i) => {
    const name = quarterName(q);
    const align = i < 2 ? " left" : i >= n - 2 ? " right" : "";
    const label = `<span class="qc-x" aria-hidden="true"><b>${q.fiscal_quarter ? `Q${q.fiscal_quarter}` : ""}</b>${shortMonth(q.end)}</span>`;
    if (q.revenue == null) {
      const why = "The company's SEC filings don't give this quarter's revenue, or it was left out because its figures didn't check out.";
      return `<div class="qc-col blank" role="listitem" tabindex="0" aria-label="${esc(`${name}: not available. ${why}`)}">
        <div class="qc-area" aria-hidden="true"><span class="qc-na">n/a</span></div>${label}
        <span class="qc-val" aria-hidden="true">n/a</span><span class="qc-yoy" aria-hidden="true"></span>
        <span class="qc-tip${align}" aria-hidden="true"><b>Not available</b><span>${esc(name)}</span><span>${why}</span></span></div>`;
    }
    const v = q.revenue;
    const bar = `--base:${base.toFixed(2)}%;--h:${Math.abs(at(v) - base).toFixed(2)}%`;
    const gm = q.gross_profit != null && v > 0 ? q.gross_profit / v : null;
    const yoyText = q.yoy != null ? `${q.yoy >= 0 ? "Up" : "Down"} ${pct(Math.abs(q.yoy), 1)} on the same quarter a year before.`
      : WITHHELD[q.yoy_withheld] || "No growth figure, since the same quarter a year before isn't available.";
    const extra = q.extra_days && q.yoy != null ? ` It ran ${Math.abs(q.extra_days)} days ${q.extra_days > 0 ? "longer" : "shorter"} than that quarter, since the company's fiscal year sometimes has an extra week, which moves growth by several points.` : "";
    const tip = `<span class="qc-tip${align}" aria-hidden="true"><b>${amount(v)}</b><span>${esc(name)}</span>
      <span>${yoyText}${extra}</span>${gm != null ? `<span>Gross margin ${pct(gm, 1)}.</span>` : ""}${q.derived ? `<span>Worked out as the fiscal year's total less its first three quarters.</span>` : ""}</span>`;
    const aria = `${name}: ${amount(v)}. ${yoyText}${extra}${q.derived ? " Worked out as the fiscal year's total less its first three quarters." : ""}`;
    return `<div class="qc-col${q.derived ? " derived" : ""}" role="listitem" tabindex="0" aria-label="${esc(aria)}">
      <div class="qc-area" aria-hidden="true"><div class="qc-bar${v < 0 ? " neg" : ""}" style="${bar}"></div></div>${label}
      <span class="qc-val" aria-hidden="true">${amount(v)}</span>
      <span class="qc-yoy ${signClass(q.yoy)}${extra ? " adj" : ""}" aria-hidden="true">${growth(q.yoy)}</span>${tip}</div>`;
  }).join("");
  const grid = t.map((v) => `<span class="qc-grid${v === 0 ? " zero" : ""}" style="bottom:${at(v)}%"></span>`).join("");
  const axis = t.map((v) => `<span style="bottom:${at(v)}%">${axisLabel(v, Math.max(Math.abs(lo), hi))}</span>`).join("");
  const keys = [derived && `<span><i class="qk derived" aria-hidden="true"></i>Fourth quarter worked out as the fiscal year's total less its first three quarters, since companies rarely report it on its own</span>`,
    odd && `<span><i class="qk adj" aria-hidden="true"></i>Growth over a quarter of a different length, where a fiscal year had an extra week</span>`,
    restated && `<span><i class="qk none" aria-hidden="true"></i>No growth shown where a later filing restated one of the two quarters (for example after a business was sold), so they aren't on the same footing</span>`,
    unchecked && `<span><i class="qk none" aria-hidden="true"></i>No growth shown where the two quarters' figures come from filings that couldn't be checked against each other</span>`].filter(Boolean);
  return `<figure class="qc" style="--n:${n}">
      <figcaption class="sr-only">${esc(summary)}</figcaption>
      <div class="qc-axis" aria-hidden="true">${axis}</div>
      <div class="qc-plot">
        <div class="qc-grids" aria-hidden="true">${grid}</div>
        <div class="qc-cols" role="list" aria-label="Revenue by quarter">${cols}</div>
      </div>
    </figure>
    ${stale ? `<p class="qc-stale">The latest quarter in the company's SEC filings ended ${monthYear(last.end)}, so these figures are not current.</p>` : ""}
    <p class="qc-note">Quarters are the company's own fiscal quarters, labeled with the month each ended.</p>
    ${keys.length ? `<div class="qc-keys">${keys.join("")}</div>` : ""}`;
}

// Revenue over the last 12 months, and gross profit and gross margin for the fiscal year and the last 12 months.
function quarterStats(f, plotted, gbar) {
  const tiles = [];
  const tile = (k, v, s, cls = "") => `<div><span class="k">${k}</span><b>${v}</b>${s ? `<span class="s ${cls}">${s}</span>` : ""}</div>`;
  const fyEnd = f.fiscal_year_end;
  const newer = f.ttm_end && (!fyEnd || f.ttm_end > fyEnd);
  if (plotted && f.ttm_revenue != null) {
    // Against the four quarters a year before, which the pipeline gives only where each quarter's growth is known, so
    // the two totals are on one footing.
    const g = f.ttm_prior_revenue > 0 ? f.ttm_revenue / f.ttm_prior_revenue - 1 : null;
    tiles.push(tile("Revenue, last 12 months", amount(f.ttm_revenue),
      `12 months to ${monthYear(f.ttm_end)}${g != null ? `, ${g >= 0 ? "up" : "down"} ${pct(Math.abs(g), 1)} on the 12 months before` : ""}`));
  } else if (plotted && f.revenue != null) {
    tiles.push(tile("Revenue", amount(f.revenue), fyEnd ? `Fiscal year to ${monthYear(fyEnd)}` : "Latest fiscal year"));
  }
  let fine = "";
  if (gbar) {
    const fy = fyEnd ? `Fiscal year to ${monthYear(fyEnd)}` : "Latest fiscal year";
    // From the dollar figures where both are there, since the margins are stored rounded to four places and would
    // otherwise round differently from the health check's (48.65% there, 48.6% here).
    const ratio = (gp, rev, m) => (gp != null && rev > 0 ? gp / rev : m);
    const gm = f.sales_doubt ? null : ratio(f.gross_profit, f.revenue, f.gross_margin);
    const tm = ratio(f.ttm_gross_profit, f.ttm_revenue, f.ttm_gross_margin);
    if (gm != null) {
      tiles.push(tile("Gross margin", pct(gm, 1), `${fy}, gross profit of ${amount(f.gross_profit)}`));
      if (newer && tm != null) {
        const d = tm - gm;
        tiles.push(tile("Gross margin, last 12 months", pct(tm, 1),
          `12 months to ${monthYear(f.ttm_end)}, gross profit of ${amount(f.ttm_gross_profit)}${Math.abs(d) >= 0.001 ? `, ${Math.abs(d * 100).toFixed(1)} points ${d > 0 ? "above" : "below"} the fiscal year` : ""}`));
      }
      fine = `Gross margin is the share of sales left after the direct cost of making or buying what was sold, before spending on research, marketing and running the company.${f.gross_basis && f.gross_basis !== "GrossProfit" ? " The company's filings show no gross profit line, so it is worked out as sales less cost of sales." : ""}`;
    } else {
      tiles.push(tile("Gross margin", "Not measured", ""));
      fine = esc((gbar.note || "").replace(/^Not measured\.\s*/, ""));
    }
  }
  if (!tiles.length) return "";
  return `<div class="q-stats">${tiles.join("")}</div>${fine ? `<p class="q-fine">${fine}</p>` : ""}`;
}

// The same figures as a table: the chart's text alternative, and the only place quarterly gross profit is listed.
function quarterTable(qs, gross) {
  const rows = [...qs].reverse().map((q) => {
    const gm = q.gross_profit != null && q.revenue > 0 ? q.gross_profit / q.revenue : null;
    const when = q.dates_estimated ? `ended around ${esc(monthYear(q.end))}` : `ended ${esc(fullDate(q.end))}`;
    return `<tr><td>${q.fiscal_quarter ? `Q${q.fiscal_quarter}, ` : ""}${when}${q.derived && q.revenue != null ? ` <span class="tag">Worked out</span>` : ""}</td>
      <td>${q.revenue == null ? "n/a" : amount(q.revenue)}</td>
      <td class="${signClass(q.yoy)}">${q.yoy != null ? growth(q.yoy) : q.revenue != null && q.yoy_withheld ? `<span class="faint">Not compared</span>` : ""}${q.extra_days && q.yoy != null ? ` <span class="faint">(${q.extra_days > 0 ? "+" : "-"}${Math.abs(q.extra_days)} days)</span>` : ""}</td>
      ${gross ? `<td>${q.gross_profit == null ? "n/a" : amount(q.gross_profit)}</td><td>${gm == null ? "n/a" : pct(gm, 1)}</td>` : ""}</tr>`;
  }).join("");
  return `<details class="disclose q-table">
    <summary>Every quarter as a table<i class="ph ph-caret-down" aria-hidden="true"></i></summary>
    <div class="body"><div class="table-wrap"><table class="data">
      <thead><tr><th scope="col">Fiscal quarter</th><th scope="col">Revenue</th><th scope="col">Growth on a year before</th>${gross ? `<th scope="col">Gross profit</th><th scope="col">Gross margin</th>` : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="q-fine">Newest first. A quarter is n/a where the company's SEC filings don't give its revenue, or it was left out because its figures didn't check out. Growth is not compared where the quarter a year before had no revenue, or a later filing restated one of the two, so they aren't on the same footing.</p></div>
  </details>`;
}

function healthSection(r) {
  if (!r.health) {
    return `<div class="h-top"><div><h2>Health check</h2><p>We could not find a full year of revenue and profit in this company's SEC filings, so it cannot be scored.
      This is common for companies with no sales yet, funds, SPACs and foreign filers, foreign banks among them.</p></div></div>`;
  }
  const s = r.health_score;
  const band = s == null ? "" : s >= 70 ? "strong" : s >= 40 ? "fair" : "weak";
  const word = { strong: ", Healthy", fair: ", Mixed", weak: ", Weak" }[band] || "";
  return `<div class="h-top">
      <div><h2>Health check</h2><p>Every number becomes a 1 to 100 score, where higher is better. Raw amounts like cash or share count only mean something next to price or history, so each is scored through the ratio that gives it meaning.</p></div>
      <div class="h-score" title="Average of all scored bars, excluding market mood"><b class="${band}">${s ?? "n/a"}</b><span>/ 100${word}</span></div>
    </div>
    <div class="h-groups">${r.health.map((g) => `
      <section class="panel h-group${g.bars.every((b) => b.context) ? " context" : ""}">
        <header><h3>${esc(g.name)}</h3><p>${esc(g.hint)}</p></header>
        ${g.bars.some((b) => b.key === "net_margin" || b.key === "ffo_margin" || b.key === "roa") ? oneTimeNote(r, "margin:12px 0 4px") : ""}
        ${g.bars.map((b) => `<div class="h-bar">
          <div class="l"><b>${esc(b.label)}</b><span>${esc(b.value)}</span></div>
          ${meter(b.score, b.band)}
          <p>${esc(b.note)}</p>
        </div>`).join("")}
      </section>`).join("")}
    </div>`;
}

function newsSection(r) {
  const n = r.news || { items: [], overall: { label: "none", text: "No recent headlines." } };
  const o = n.overall;
  return `<div class="h-top"><div><h2>Recent news</h2><p>Each headline is labeled by the words it uses, with the reason shown. Read the story before acting on a label.</p></div></div>
    <div class="panel news-top"><span class="tag ${NEWS_TAG[o.label]}">${o.label === "none" ? "No news" : "Overall: " + cap(o.label)}</span><p>${esc(o.text)}</p></div>
    ${n.items.length ? `<div class="news-list">${n.items.map((i) => `
      <a class="news-item" href="${esc(i.url)}" target="_blank" rel="noopener">
        <span class="tag ${NEWS_TAG[i.label]}" style="justify-self:start">${cap(i.label)}</span>
        <div><div class="t">${esc(i.title)}</div><div class="why">${esc(i.reason)}</div>
        <div class="src">${esc(i.source)}${i.date ? `, ${esc(date(i.date))}` : ""}</div></div>
      </a>`).join("")}</div>` : ""}`;
}

// A valuation multiple, or n/a where it doesn't apply (the note under the table says why).
const mult = (x) => (x == null ? "n/a" : `${x >= 100 ? Math.round(x).toLocaleString("en-US") : x.toFixed(1)}x`);

// Reports written before peers were chosen by their numbers carry only peer_group and peer_count, which the
// notes at the bottom still describe.
function peersSection(r) {
  if (!r.peer_basis) return "";
  const list = r.peers || [];
  const pm = r.peer_multiples;
  const head = `<div class="h-top" id="peers"><div><h2>Similar companies</h2><p>${esc(r.peer_basis)}</p></div></div>`;
  if (!list.length || !pm) return head;
  // Non-financial companies also get price to sales, the measure their health check ranks. Banks get price to tangible
  // book (ptbv) in place of a sales multiple and have no sales_label; every other report has sales_label.
  const cols = [pm.ps_label && ["ps", pm.ps_label], pm.ptbv_label && ["ptbv", pm.ptbv_label],
    pm.sales_label && ["sales", pm.sales_label], ["pe", pm.pe_label]].filter(Boolean);
  const row = (who, name, x) => `<div class="who"><b>${esc(who)}</b><span>${esc(name)}</span></div>
    ${cols.map(([k]) => `<div class="n">${mult(x?.[k])}</div>`).join("")}`;
  return `${head}
    <div class="panel peers${cols.length > 2 ? " wide" : ""}">
      <div class="peer-row peer-head"><span>Company</span>${cols.map(([, label]) => `<span>${esc(label)}</span>`).join("")}</div>
      <div class="peer-row self">${row(r.symbol, "This company", pm.company)}</div>
      ${list.map((p) => `<a class="peer-row" href="?t=${encodeURIComponent(p.symbol)}" data-sym="${esc(p.symbol)}">${row(p.symbol, p.name, p)}</a>`).join("")}
      <div class="peer-row total">${row("Median", `of the ${list.length} companies above`, pm.median)}</div>
    </div>
    ${pm.note ? `<p class="fine peers-note">${esc(pm.note)}</p>` : ""}`;
}

function stake(b) {
  if (b.new_position) return `<span class="up">New</span><small>position</small>`;
  if (b.stake_increase == null) return `<span class="faint">n/a</span>`;
  return `${pct(b.stake_increase, b.stake_increase < 0.1 ? 1 : 0, true)}<small>stake</small>`;
}

function insiderSection(r) {
  const c = r.insiders;
  if (!c) return "";
  return `<div class="h-top"><div><h2>Insider buying</h2><p>Open-market purchases filed with the SEC in the last ${r.insider_window || 30} days.</p></div>
      <span class="tag ${c.tier === 1 ? "tag-solid" : ""}">${c.tier <= 2 ? "Tier " + c.tier : "Smaller"}</span></div>
    <div class="panel" style="padding:12px 8px 8px">
      <div class="buyers-head"><span>Buyer</span><span>Amount</span><span>Price paid</span><span class="stake">Stake change</span><span></span></div>
      ${c.buyers.map((b) => `
      <div class="buyer">
        <div class="who"><b>${esc(b.name)}</b><span>${esc((b.roles || []).join(", ") || "Insider")}, bought ${esc(date(b.last))}</span></div>
        <div class="n">${money(b.value)}</div><div class="n">${price(b.avg_price)}</div>
        <div class="n stake">${stake(b)}</div>
        <a class="doc" href="${esc(b.filing)}" target="_blank" rel="noopener" title="Open the SEC filing"><i class="ph ph-arrow-up-right" aria-hidden="true"></i><span class="sr-only">SEC filing</span></a>
      </div>`).join("")}</div>`;
}

function render(r) {
  const f = r.fundamentals;
  document.title = `${r.symbol}: ${r.name} | Ticker Finder`;
  host.innerHTML = `
    <header class="r-head rise">
      <div class="r-id">
        <div class="sym">${esc(r.symbol)}<span class="faint">${esc(r.exchange || "")}</span>${r.screener ? `<a class="tag tag-up" href="screener.html" style="text-decoration:none">Passes the screener</a>` : ""}</div>
        <h1>${esc(r.name)}</h1>
        <p class="where">${[r.sector, r.industry].filter(Boolean).map(esc).join(", ")}</p>
      </div>
      <div class="r-price"><div class="p">${price(r.price)}</div>${r.mcap ? `<div class="sub">${money(r.mcap)} market value</div>` : ""}</div>
    </header>
    <div class="r-grid">${verdictPanel(r)}${pricePanel(r)}</div>
    ${quarterSection(r)}
    ${healthSection(r)}
    ${peersSection(r)}
    ${insiderSection(r)}
    ${newsSection(r)}
    <div class="notes">
      ${f ? `<p>Financials from fiscal year ${f.fiscal_year}${f.fiscal_year_end ? ` (ended ${esc(date(f.fiscal_year_end, { month: "short", day: "numeric", year: "numeric" }))})` : ""}.${f.balance_as_of ? ` Balance sheet as of ${esc(date(f.balance_as_of, { month: "short", day: "numeric", year: "numeric" }))}.` : ""} Source: SEC filings.</p>` : ""}
      ${r.peer_group && !r.peer_basis ? `<p>Compared with ${r.peer_count} other US-listed companies in ${esc(r.peer_group)}.</p>` : ""}
      <p>Educational estimates only. Not investment advice.</p>
    </div>`;
}

// Bumped on every navigation, so a slow response for an earlier pick never paints over a later one.
let seq = 0;

async function start() {
  const id = ++seq;
  document.title = "Ticker report | Ticker Finder";
  let popular = "";
  try {
    const u = await load("universe.json");
    popular = [...u].sort((a, b) => (b[3] || 0) - (a[3] || 0)).slice(0, 8)
      .map((r) => `<a href="?t=${encodeURIComponent(r[0])}"><span class="s">${esc(r[0])}</span><span class="n">${esc(r[1])}</span></a>`).join("");
  } catch {}
  if (id !== seq) return;
  host.innerHTML = `<section class="start">
    <h1>Look up any US stock</h1>
    <p class="lede">Get its news tone, 1 to 100 health scores and an estimated fair value, each with a one-line explanation.</p>
    ${popular ? `<div class="popular">${popular}</div>` : ""}
  </section>`;
  searchHost.querySelector("input").focus();
}

async function show(sym) {
  const id = ++seq;
  host.innerHTML = `<div style="padding-top:40px"><div class="skeleton" style="height:90px;margin-bottom:16px"></div>
    <div class="r-grid"><div class="skeleton" style="height:360px"></div><div class="skeleton" style="height:360px"></div></div></div>`;
  try {
    const r = await load(`t/${sym.toUpperCase()}.json`);
    if (id !== seq) return;
    render(r);
    window.scrollTo({ top: 0 });
  } catch (e) {
    if (id !== seq) return;
    if (e.status === 404) {
      host.innerHTML = `<div class="panel empty" style="margin-top:40px"><h3>No report for "${esc(sym)}"</h3>
        <p>Reports cover common stocks listed on the NYSE, Nasdaq and NYSE American that file with the SEC. Check the ticker, or search by company name above.</p></div>`;
    } else {
      host.innerHTML = `<div class="panel empty" style="margin-top:40px"><h3>Could not load the ${esc(sym)} report</h3>
        <p>The connection may have dropped. Try again in a moment.</p>
        <button class="btn btn-ghost btn-sm" type="button" data-retry style="margin-top:12px">Try again</button></div>`;
      host.querySelector("[data-retry]").addEventListener("click", () => show(sym));
    }
  }
}

function route() {
  const t = new URLSearchParams(location.search).get("t");
  t ? show(t) : start();
}
route();
