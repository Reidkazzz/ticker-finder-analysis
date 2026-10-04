import { chrome, date, esc, load, meter, money, pct, price, times } from "./common.js";

chrome();

const params = new URLSearchParams(location.search);
const INSIDER_FILTERS = ["any", "buying", "below"];
const state = {
  sector: params.get("sector") || "All",
  insider: INSIDER_FILTERS.includes(params.get("insider")) ? params.get("insider") : "any",
  sort: "score",
  open: null,
};
const host = document.querySelector("[data-results]");
const countEl = document.querySelector("[data-count]");
let data;
let insiderDays = null; // the insider scan's window (meta.json), for "in the last 30 days"

// Missing values sort last whichever way a column sorts.
const low = (v) => v ?? Number.MAX_VALUE;
const high = (v) => v ?? -Number.MAX_VALUE;
const SORTS = {
  score: (a, b) => b.score - a.score,
  ps: (a, b) => a.ps - b.ps,
  pb: (a, b) => low(a.pb) - low(b.pb),
  net_margin: (a, b) => b.net_margin - a.net_margin,
  fcf_yield: (a, b) => high(b.fcf_yield) - high(a.fcf_yield),
  drawdown: (a, b) => high(b.drawdown) - high(a.drawdown),
  mcap: (a, b) => a.mcap - b.mcap,
};

// Older data (a screener.json from before a feature) lacks its keys, and the page hides what it can't show.
const hasPb = () => data.results.some((r) => "pb" in r);
const hasInsiders = () => data.insiders_checked === true;
const ofDrawdown = () => pct(data.rules?.out_of_favor_drawdown ?? 0.25, 0);
const window_ = () => (insiderDays ? `in the last ${insiderDays} days` : "recently");

const band = (s) => (s >= 70 ? "strong" : s >= 40 ? "fair" : "weak");
const debtTag = (r) =>
  r.debt_status === "none" ? `<span class="tag tag-up">No debt</span>`
  : r.debt_status === "net_cash" ? `<span class="tag tag-up">Net cash</span>`
  : `<span class="tag">Low, ${times(r.lt_de)}</span>`;

// Each flag as [key, tag class, label, tooltip], in the order the tags show.
function flagDefs() {
  const dd = ofDrawdown();
  return [
    ["out_of_favor", "tag-mid", "Out of favor", `${dd} or more below its 52-week high`],
    ["industry_out_of_favor", "tag-mid", "Industry out of favor", `The median company in its industry trades ${dd} or more below its 52-week high`],
    ["undiscovered", "", "Undiscovered", "Two or fewer analysts cover it"],
    ["under_book", "tag-up", "Under book value", "Its market value is less than its book value (shareholders' equity less preferred stock)"],
    ["insiders_buying", "tag-up", "Insiders buying", `Tier 1 or 2 open-market insider buying ${window_()}`],
    ["below_insider_price", "tag-up", "Below insiders' price", "The price is under the average price insiders paid"],
    ["above_upper_band", "tag-down", "Above upper band, wait for a pullback", "The price is above the average of its last 20 weekly closes plus two standard deviations"],
  ];
}

function flags(r) {
  const f = flagDefs().filter(([k]) => r.flags.includes(k))
    .map(([, cls, label, tip]) => `<span class="tag ${cls}" title="${esc(tip)}">${esc(label)}</span>`);
  return f.length ? `<div class="co-flags">${f.join("")}</div>` : "";
}

function facts(r) {
  const out = [];
  out.push(["ph-receipt", `Sold ${money(r.revenue)} in fiscal ${r.fiscal_year} and kept ${pct(r.net_margin, 0)} of it as profit${r.one_time_note ? ", not counting one-time items" : ""}.`]);
  if (r.one_time_note) out.push(["ph-info", r.one_time_note]);
  if (r.revenue_growth != null) out.push(["ph-trend-up", `Sales ${r.revenue_growth >= 0 ? "grew" : "slipped"} ${pct(Math.abs(r.revenue_growth))} from the year before.`]);
  out.push(["ph-coins", r.debt_status === "none" ? `Has no debt. Cash on hand: ${money(r.net_cash)}.`
    : r.net_cash > 0 ? `Holds ${money(r.net_cash)} more cash than all of its debt.`
    : `Long-term debt is ${times(r.lt_de)} its equity, under the 0.5 limit.`]);
  if (r.fcf_yield != null) out.push(["ph-hand-coins", `Generated ${money(r.fcf)} of free cash, ${pct(r.fcf_yield)} of its market value.`]);
  if ("pb" in r) {
    out.push(["ph-scales", r.pb != null
      ? `Its market value is ${under(r.pb)} its book value of ${money(r.book_value)} (shareholders' equity less preferred stock).`
      : "Its book value (shareholders' equity less preferred stock) is zero or less, so it has no price to book."]);
  }
  const asReported = r.profitable_years_reported;
  out.push(["ph-calendar-check", `Profitable in ${r.profitable_years} of the last ${r.years_checked} years${
    asReported != null && asReported !== r.profitable_years ? `, not counting one-time items (${asReported} as reported)` : ""}.`]);
  if (r.drawdown != null) out.push(["ph-arrow-down-right", `Trades ${pct(r.drawdown, 0)} below its 52-week high of ${price(r.high52)}.`]);
  out.push(["ph-users", r.analysts == null ? "Analyst coverage could not be checked today." : r.analysts === 0 ? "No Wall Street analysts publish estimates on it." : `${r.analysts} analyst${r.analysts === 1 ? " publishes" : "s publish"} estimates on it.`]);
  return out;
}

// The flags that aren't already told by the facts above, in words.
function flagFacts(r) {
  const out = [];
  const ins = r.insiders;
  if (ins) {
    const paid = ins.avg_price;
    const vs = paid ? r.price / paid - 1 : null;
    out.push(["ph-user-check", `Insiders bought ${money(ins.total_value)} of its stock on the open market ${window_()} (Tier ${ins.tier})${
      paid ? `, paying ${price(paid)} a share on average. Today's price of ${price(r.price)} is ${pct(Math.abs(vs), Math.abs(vs) < 0.1 ? 1 : 0)} ${vs < 0 ? "below" : "above"} that` : ""}.`]);
  }
  if (r.flags.includes("industry_out_of_favor")) {
    const ind = (data.industries || []).find((x) => x.industry === r.industry);
    out.push(["ph-factory", ind ? `Its industry, ${ind.industry}, is out of favor: its median company trades ${pct(ind.median_drawdown, 0)} below its 52-week high.`
      : `Its industry, ${r.industry}, is out of favor.`]);
  }
  if (r.flags.includes("above_upper_band") && r.upper_band != null) {
    out.push(["ph-warning", `Its price of ${price(r.price)} is above its upper band of ${price(r.upper_band)} (the average of its last 20 weekly closes plus two standard deviations), so it has risen unusually fast. Many traders would wait for the price to pull back before buying.`]);
  }
  return out;
}

const factList = (list) => `<ul class="facts">${list.map(([i, t]) => `<li><i class="ph ${i}" aria-hidden="true"></i><span>${esc(t)}</span></li>`).join("")}</ul>`;

function detailRow(r, cols) {
  // Whole points when the pipeline sends them. Older data has one-decimal parts, and rounding those
  // one by one here would stop them adding up to the score, so they keep their decimal.
  const whole = r.score_parts.every((p) => Number.isInteger(p.points));
  const pts = (n) => (whole ? String(n) : (Math.round(n * 10) / 10).toFixed(1));
  const total = r.score_parts.reduce((a, p) => a + p.points, 0);
  const more = flagFacts(r);
  return `<tr class="detail"><td colspan="${cols}"><div class="detail-inner">
    <div><h4>Why it passed</h4>${factList(facts(r))}${more.length ? `<h4 class="facts-head">What its flags mean</h4>${factList(more)}` : ""}</div>
    <div><h4>How the ${r.score} score adds up</h4><div class="parts">${r.score_parts.map((p) => `
      <div class="part"><span>${esc(p.label)}</span><div class="part-track"><span style="width:${p.max ? (p.points / p.max) * 100 : 0}%"></span></div><span class="pts">${pts(p.points)} / ${p.max}</span></div>`).join("")}
      <div class="part part-total"><span>Total</span><span>${Math.abs(total - r.score) < 0.05 ? "" : `Rounds to ${r.score}`}</span><span class="pts">${pts(total)} / ${r.score_parts.reduce((a, p) => a + p.max, 0)}</span></div>
    </div></div>
    <div class="detail-actions"><a class="btn btn-primary btn-sm" href="report.html?t=${encodeURIComponent(r.symbol)}">Full report on ${esc(r.symbol)}</a></div>
  </div></td></tr>`;
}

// The insider filter above the list (Tier 1 or 2 buying, and a price under what insiders paid).
const insiderOk = (r) => state.insider === "any"
  || (state.insider === "buying" ? r.flags.includes("insiders_buying") : r.flags.includes("below_insider_price"));
// What the filter asks, after "and" in the count (for one company, then for several). Only Tier 1 or 2 buying counts:
// a company with smaller purchases (Tier 3) is on neither side of the filter's claim.
const INSIDER_WORDS = {
  buying: ["has Tier 1 or 2 insider buying", "have Tier 1 or 2 insider buying"],
  below: ["trades below what Tier 1 or 2 insiders paid", "trade below what Tier 1 or 2 insiders paid"],
};
// The insider scan's days not yet read (meta.json), which the filter's empty state names.
let insiderPending = 0;

function renderSectors() {
  const el = document.querySelector("[data-sectors]");
  // With the insider filter on, each sector counts the companies the list would show.
  const count = (name) => (state.insider === "any"
    ? (name === "All" ? data.results.length : data.sectors.find((s) => s.name === name)?.passed ?? 0)
    : data.results.filter((r) => insiderOk(r) && (name === "All" || r.sector === name)).length);
  const list = [{ name: "All" }, ...data.sectors.filter((s) => s.checked > 0 || s.excluded)];
  el.innerHTML = list.map((s) => `<button class="pill" type="button" data-sector="${esc(s.name)}" aria-pressed="${s.name === state.sector}">
    ${esc(s.name === "All" ? "All sectors" : s.name)}<span class="count">${s.excluded ? 0 : count(s.name)}</span></button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("[data-sector]");
    if (!b) return;
    state.sector = b.dataset.sector;
    state.open = null;
    setParam("sector", state.sector === "All" ? null : state.sector);
    renderSectors();
    renderTable();
  };
}

function setParam(key, value) {
  const u = new URL(location);
  value == null ? u.searchParams.delete(key) : u.searchParams.set(key, value);
  history.replaceState(null, "", u);
}

function renderInsiderFilter() {
  const row = document.querySelector("[data-insider-only]");
  if (!hasInsiders()) {
    state.insider = "any";
    return;
  }
  row.hidden = false;
  const el = row.querySelector("[data-insider-filter]");
  const paint = () => el.querySelectorAll("[data-insider]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.insider === state.insider));
  paint();
  el.onclick = (e) => {
    const b = e.target.closest("[data-insider]");
    if (!b) return;
    state.insider = b.dataset.insider;
    state.open = null;
    setParam("insider", state.insider === "any" ? null : state.insider);
    paint();
    renderSectors();
    renderTable();
  };
}

// Companies that passed every other check but were left out because a check could not be confirmed on this run:
// their US registration ("us") or their debt ("debt"), each listed in "unconfirmed" with its reason in "reason".
// Older data lists only registration problems, as symbols or as objects without "unconfirmed".
const its = (n) => (n === 1 ? "its" : "their");
const isLeftOut = (n) => (n === 1 ? "it is left out" : "they are left out");
const UNCONFIRMED = {
  us: (n) => `passed every financial check, but ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
  debt: (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings, so the low-debt rule could not be confirmed and ${isLeftOut(n)}`,
  "debt us": (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings and ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
};
const andList = (a) => (a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
const symLink = (s, title) => `<a href="report.html?t=${encodeURIComponent(s)}"${title ? ` title="${esc(title)}"` : ""}>${esc(s)}</a>`;

function unverifiedNote() {
  const list = (data.unverified || []).map((u) => (typeof u === "string" ? { symbol: u } : u))
    .filter((u) => u && u.symbol && (state.sector === "All" || !u.sector || u.sector === state.sector));
  if (!list.length || state.insider !== "any") return "";
  const groups = new Map();
  for (const u of list) {
    const key = Array.isArray(u.unconfirmed) && u.unconfirmed.length ? [...u.unconfirmed].sort().join(" ") : "us";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(u);
  }
  const order = ["us", "debt", "debt us"];
  const keys = [...groups.keys()].sort((a, b) => (order.indexOf(a) + 1 || 9) - (order.indexOf(b) + 1 || 9));
  const sentences = keys.map((key) => {
    const g = groups.get(key);
    const n = g.length;
    const links = andList(g.map((u) => symLink(u.symbol, [u.name && u.name.replace(/\.$/, ""), u.reason].filter(Boolean).join(". "))));
    const why = UNCONFIRMED[key] ? UNCONFIRMED[key](n)
      : `passed every other check, but one check could not be confirmed today, so ${isLeftOut(n)}`;
    // A registration lookup that failed today may work tomorrow. A debt reading changes only with new filings.
    const again = key.split(" ").includes("us") ? " The next update checks again." : "";
    return `${n} ${n === 1 ? "company" : "companies"} ${why}: ${links}.${again}`;
  });
  return `<div class="notice unverified"><i class="ph ph-info" aria-hidden="true"></i>
    <div>${sentences.map((s, i) => `<p${i ? ` style="margin-top:6px"` : ""}>${s}</p>`).join("")}</div></div>`;
}

function renderTable() {
  const rows = data.results.filter((r) => (state.sector === "All" || r.sector === state.sector) && insiderOk(r)).sort(SORTS[state.sort]);
  const sec = data.sectors.find((s) => s.name === state.sector);
  const which = state.insider === "any" ? "" : ` and ${INSIDER_WORDS[state.insider][rows.length === 1 ? 0 : 1]}`;
  countEl.innerHTML = state.sector === "All"
    ? `<b>${rows.length}</b> ${rows.length === 1 ? "company" : "companies"} passed every check${which}`
    : sec?.excluded ? `${esc(state.sector)} is not screened`
    : `<b>${rows.length}</b> of ${sec?.checked ?? 0} ${esc(state.sector)} companies passed every check${which}`;

  if (sec?.excluded) {
    const see = Array.isArray(data.financials_list) ? ` <a href="#financials">See the banks and insurers under book value below.</a>` : "";
    host.innerHTML = `<div class="panel empty"><h3>${esc(state.sector)} companies are not screened</h3><p>${esc(sec.excluded)}${see}</p></div>`;
    return;
  }

  if (!rows.length) {
    const passing = state.sector === "All" ? "the companies that pass" : `the ${esc(state.sector)} companies that pass`;
    const notYet = insiderPending ? ` (${insiderPending} filing day${insiderPending === 1 ? " is" : "s are"} not scanned yet)` : "";
    host.innerHTML = state.insider !== "any"
      ? `<div class="panel empty"><h3>No ${state.sector === "All" ? "" : `${esc(state.sector)} `}company on the list ${INSIDER_WORDS[state.insider][0]}</h3>
        <p>None of ${passing} today had Tier 1 or 2 insider buying ${esc(window_())}${state.insider === "below" ? " at an average price above today's price" : ""}${notYet}.
        Try "Any company"${state.sector === "All" ? "" : " or another sector"}.</p></div>`
      : `<div class="panel empty"><h3>No ${esc(state.sector)} company passes today</h3>
      <p>That is normal. The checks are strict on purpose, and some sectors rarely have cheap, debt-light, profitable small caps.
      Try another sector or come back after the next update.</p></div>${unverifiedNote()}`;
    return;
  }
  const pb = hasPb();
  const cols = pb ? 11 : 10;
  host.innerHTML = `<div class="table-wrap"><table class="data">
    <thead><tr>
      <th>#</th><th>Company</th><th style="text-align:left">Score</th><th>Market value</th><th>Price / sales</th>${pb ? "<th>Price / book</th>" : ""}
      <th>Net margin</th><th>Debt</th><th>Cash yield</th><th>Below high</th><th>Analysts</th>
    </tr></thead>
    <tbody>${rows.map((r, i) => `
      <tr class="row" tabindex="0" data-sym="${esc(r.symbol)}" aria-expanded="${state.open === r.symbol}">
        <td class="rank-cell">${i + 1}</td>
        <td class="co-cell"><div class="co"><span class="sym">${esc(r.symbol)}</span><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span></div>${flags(r)}</td>
        <td class="score-cell">${meter(r.score, band(r.score))}</td>
        <td>${money(r.mcap)}</td>
        <td>${times(r.ps)}</td>
        ${pb ? `<td>${under(r.pb)}</td>` : ""}
        <td${r.one_time_note && r.net_margin_reported != null ? ` title="Leaves out one-time items. Reported margin: ${pct(r.net_margin_reported)}"` : ""}>${pct(r.net_margin)}</td>
        <td>${debtTag(r)}</td>
        <td>${pct(r.fcf_yield)}</td>
        <td>${r.drawdown == null ? "n/a" : pct(r.drawdown, 0)}</td>
        <td>${r.analysts ?? "n/a"}</td>
      </tr>${state.open === r.symbol ? detailRow(r, cols) : ""}`).join("")}
    </tbody></table></div>
    <p class="faint" style="font-size:13px;margin-top:12px">Select a row to see why it passed and how its score adds up. Cash yield is free cash flow as a share of market value.${pb ? " Price to book is market value over book value: shareholders' equity less preferred stock." : ""}</p>
    ${unverifiedNote()}`;

  host.querySelectorAll("tr.row").forEach((tr) => {
    const toggle = () => { state.open = state.open === tr.dataset.sym ? null : tr.dataset.sym; renderTable(); };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });
}

// The sections under the list. Each is hidden in the page and shown only when the data has it, so a screener.json
// from before these lists leaves them out. A long list shows its first SHOWN rows and a button for the rest.
const SHOWN = 15;
// A ratio a list is "under" (price to book, market value to net cash), cut rather than rounded under 1, so 0.997 reads
// 0.99x and not 1.00x on a list of companies under 1.
const under = (x) => (x != null && x < 1 ? `${(Math.floor(x * 100) / 100).toFixed(2)}x` : times(x));
const coCell = (r) => `<td class="txt co-cell"><div class="co"><a class="sym" href="report.html?t=${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span></div></td>`;
const asOf = (iso) => (iso ? date(iso, { month: "short", day: "numeric", year: "numeric" }) : "n/a");

function section(key, body) {
  const el = document.querySelector(`[data-section="${key}"]`);
  el.hidden = false;
  el.querySelector("[data-body]").innerHTML = body;
  const more = el.querySelector("[data-more]");
  if (more) {
    more.onclick = () => {
      const extra = el.querySelectorAll("tr[data-extra]");
      extra.forEach((tr) => { tr.hidden = false; });
      more.remove();
      extra[0]?.querySelector("a")?.focus(); // keep keyboard focus in the list, at the first row it revealed
    };
  }
}

// head: the column names. The first column (the company or industry) and those numbered in `text` hold words, aligned
// left (class "txt"); the rest hold figures, aligned right.
function listTable(head, rows, cell, note = "", text = []) {
  const extra = rows.length - SHOWN;
  return `<div class="table-wrap"><table class="data list">
    <thead><tr>${head.map((h, i) => `<th${i === 0 || text.includes(i) ? ` class="txt"` : ""}>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr${i >= SHOWN ? " data-extra hidden" : ""}>${cell(r)}</tr>`).join("")}</tbody></table></div>
    ${extra > 0 ? `<button class="btn btn-ghost btn-sm list-more" type="button" data-more>Show all ${rows.length}</button>` : ""}${note}`;
}

function unconfirmedNote(syms, what) {
  if (!syms?.length) return "";
  return `<div class="notice unverified"><i class="ph ph-info" aria-hidden="true"></i><p>${syms.length} more ${syms.length === 1 ? "company meets" : "companies meet"} ${what}, but where ${syms.length === 1 ? "it is" : "they are"} incorporated or based could not be confirmed with the SEC today: ${andList(syms.map((s) => symLink(s)))}. The next update checks again.</p></div>`;
}

function renderIndustries() {
  if (!Array.isArray(data.industries)) return;
  const out = data.industries.filter((x) => x.out_of_favor);
  const market = data.market_median_ps;
  const lead = `<p class="faint list-note">${data.industries.length} industries measured${market != null ? `. The whole market's median price to sales is ${times(market)}; an industry's median shown in green is under it` : ""}.</p>`;
  if (!data.industries.length) {
    section("industries", `<div class="panel empty"><h3>Industries could not be measured today</h3><p>Too few companies had a year of prices loaded to measure any industry.</p></div>`);
    return;
  }
  if (!out.length) {
    section("industries", `<div class="panel empty"><h3>No industry is out of favor today</h3><p>In every industry measured, the median company trades less than ${ofDrawdown()} below its 52-week high.</p></div>${lead}`);
    return;
  }
  section("industries", `${listTable(["Industry", "Companies", "Median below high", "Median price / sales"], out, (x) => `
    <td class="txt ind-cell"><b>${esc(x.industry)}</b><span>${esc(x.sector)}</span></td><td>${x.count}</td><td>${pct(x.median_drawdown, 0)}</td>
    <td${x.median_ps != null && market != null ? ` class="${x.median_ps < market ? "up" : ""}"` : ""}>${times(x.median_ps)}</td>`)}${lead}`);
}

function runway(r) {
  if (r.runway_years != null) {
    const n = Math.max(1, Math.round(r.runway_years * 12));
    return r.runway_years < 1 ? `${n} month${n === 1 ? "" : "s"}` : `${r.runway_years.toFixed(1)} years`;
  }
  return r.runway_note === "not_burning" ? `<span class="faint" title="Free cash flow was zero or more in its latest fiscal year">Not burning cash</span>`
    : r.runway_note === "stale" ? `<span class="faint" title="Its latest fiscal year of figures (${esc(String(r.fiscal_year ?? ""))}) is over a year older than its balance sheet, so its cash burn is out of date">Out of date</span>`
    : `<span class="faint" title="Its free cash flow could not be read from its filings">Unknown</span>`;
}

function renderNetCash() {
  if (!Array.isArray(data.net_cash_list)) return;
  const rows = data.net_cash_list;
  const note = unconfirmedNote(data.net_cash_unconfirmed, "the other tests");
  if (!rows.length) {
    section("net_cash", `<div class="panel empty"><h3>No company is worth less than its net cash today</h3><p>None of the US companies checked has a market value under its cash and short-term investments minus all debt, preferred stock and outside stakes in its subsidiaries.</p></div>${note}`);
    return;
  }
  section("net_cash", listTable(["Company", "Market value", "Net cash", "Value / net cash", "Free cash flow", "Cash runway", "Balance sheet"], rows, (r) => `
    ${coCell(r)}<td>${money(r.mcap)}</td><td>${money(r.net_cash)}</td><td>${under(r.mcap_to_net_cash)}</td>
    <td class="${r.fcf != null && r.fcf < 0 ? "down" : ""}">${money(r.fcf)}</td><td>${runway(r)}</td><td>${esc(asOf(r.balance_as_of))}</td>`,
  `<p class="faint list-note">${rows.length} ${rows.length === 1 ? "company" : "companies"}, cheapest against net cash first. Free cash flow is for the latest fiscal year; cash and debt are from the balance sheet dated on the right.</p>${note}`));
}

function renderFinancials() {
  if (!Array.isArray(data.financials_list)) return;
  const rows = data.financials_list;
  const note = unconfirmedNote(data.financials_unconfirmed, "the other tests");
  if (!rows.length) {
    section("financials", `<div class="panel empty"><h3>No profitable bank or insurer trades under book value today</h3></div>${note}`);
    return;
  }
  section("financials", listTable(["Company", "Kind", "Market value", "Book used", "Price / book", "Profit last year", "Return on equity"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.kind === "bank" ? "Bank" : r.industry || "Financial")}</td><td>${money(r.mcap)}</td>
    <td title="${esc(r.book_basis === "tangible" ? "Tangible book value: equity less preferred stock, goodwill and other intangible assets" : "Book value: equity less preferred stock")}">${r.book_basis === "tangible" ? "Tangible" : "Book"}, ${money(r.book)}</td>
    <td>${under(r.price_to_book)}</td><td>${money(r.net_income)}</td><td>${pct(r.roe)}</td>`,
  `<p class="faint list-note">${rows.length} ${rows.length === 1 ? "company" : "companies"}, lowest price to book first. Profit and return on equity leave out one-time items and are what is left to common shareholders, after preferred dividends.</p>${note}`, [1]));
}

function renderGiants() {
  if (!Array.isArray(data.giants)) return;
  const rows = data.giants;
  if (!rows.length) {
    section("giants", `<div class="panel empty"><h3>No company worth over $90 billion trades above 10 times its sales today</h3></div>`);
    return;
  }
  // Older data has no sales_basis: its sales were the latest fiscal year's.
  const period = (r) => (r.sales_basis === "ttm" ? `the 12 months to ${asOf(r.sales_end)}` : `fiscal ${r.fiscal_year ?? "year"}`);
  section("giants", listTable(["Company", "Sector", "Market value", "Price / sales"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.sector)}</td><td>${money(r.mcap)}</td><td class="down" title="${esc(`On ${money(r.revenue)} of sales in ${period(r)}`)}">${times(r.ps)}</td>`,
  `<p class="faint list-note">Highest price to sales first, on each company's sales over the last 12 months where its quarterly reports give them, otherwise its latest fiscal year's.</p>`, [1]));
}

// The explanations above the lists say what the data was built with: the profit margin rule from its rules (8% in
// data from before September 2026), and the newer flags and the score's weights only where the data has them.
function explainRules() {
  const margin = data.rules?.min_net_margin;
  if (margin != null) {
    const n = Math.round(margin * 10000) / 100;
    document.querySelectorAll("[data-margin-pct]").forEach((el) => { el.textContent = `${n}%`; });
    document.querySelectorAll("[data-margin-cents]").forEach((el) => { el.textContent = `${n} cent${n === 1 ? "" : "s"}`; });
    document.querySelectorAll("[data-thin-margin]").forEach((el) => { el.hidden = margin > 0.05; });
  }
  const fresh = Array.isArray(data.industries);
  document.querySelectorAll("[data-new-flag]").forEach((el) => { el.hidden = !fresh; });
  const w = data.score_weights || {};
  document.querySelector("[data-score-help]").hidden = !Object.keys(STATED_WEIGHTS).every((k) => w[k] === STATED_WEIGHTS[k])
    || Object.keys(w).length !== Object.keys(STATED_WEIGHTS).length;
}
// The weights "How the score adds up" states (screener.SCORE_WEIGHTS). Data scored with other weights hides it, so the
// page never explains a score by weights it wasn't built with; each row's own parts still show.
const STATED_WEIGHTS = { ps: 15, debt: 20, margin: 15, fcf: 10, growth: 10, small: 5, out_of_favor: 5, undiscovered: 5, under_book: 10, insiders_buying: 5 };

document.querySelector("[data-sort]").addEventListener("change", (e) => { state.sort = e.target.value; renderTable(); });

Promise.all([load("screener.json"), load("meta.json").catch(() => null)]).then(([d, meta]) => {
  data = d;
  insiderDays = meta?.insider_days ?? null;
  insiderPending = meta?.insider_pending?.length ?? 0;
  if (insiderDays) document.querySelectorAll("[data-window]").forEach((el) => { el.textContent = insiderDays; });
  if (!hasPb()) document.querySelector("[data-needs-pb]")?.remove();
  if (state.sector !== "All" && !d.sectors.some((s) => s.name === state.sector)) state.sector = "All";
  explainRules();
  renderInsiderFilter();
  renderSectors();
  renderTable();
  renderIndustries();
  renderNetCash();
  renderFinancials();
  renderGiants();
}).catch(() => {
  host.innerHTML = `<div class="panel empty"><h3>Screener data is not available yet</h3><p>Results appear after the first nightly update runs.</p></div>`;
});
