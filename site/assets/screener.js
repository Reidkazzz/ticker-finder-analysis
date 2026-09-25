import { chrome, esc, load, meter, money, pct, price, times } from "./common.js";

chrome();

const state = { sector: new URLSearchParams(location.search).get("sector") || "All", sort: "score", open: null };
const host = document.querySelector("[data-results]");
const countEl = document.querySelector("[data-count]");
let data;

const SORTS = {
  score: (a, b) => b.score - a.score,
  ps: (a, b) => a.ps - b.ps,
  net_margin: (a, b) => b.net_margin - a.net_margin,
  fcf_yield: (a, b) => (b.fcf_yield ?? -1) - (a.fcf_yield ?? -1),
  drawdown: (a, b) => (b.drawdown ?? -1) - (a.drawdown ?? -1),
  mcap: (a, b) => a.mcap - b.mcap,
};

const band = (s) => (s >= 70 ? "strong" : s >= 40 ? "fair" : "weak");
const debtTag = (r) =>
  r.debt_status === "none" ? `<span class="tag tag-up">No debt</span>`
  : r.debt_status === "net_cash" ? `<span class="tag tag-up">Net cash</span>`
  : `<span class="tag">Low, ${times(r.lt_de)}</span>`;

function flags(r) {
  const f = [];
  if (r.flags.includes("out_of_favor")) f.push(`<span class="tag tag-mid" title="25% or more below its 52-week high">Out of favor</span>`);
  if (r.flags.includes("undiscovered")) f.push(`<span class="tag" title="Two or fewer analysts cover it">Undiscovered</span>`);
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
  const asReported = r.profitable_years_reported;
  out.push(["ph-calendar-check", `Profitable in ${r.profitable_years} of the last ${r.years_checked} years${
    asReported != null && asReported !== r.profitable_years ? `, not counting one-time items (${asReported} as reported)` : ""}.`]);
  if (r.drawdown != null) out.push(["ph-arrow-down-right", `Trades ${pct(r.drawdown, 0)} below its 52-week high of ${price(r.high52)}.`]);
  out.push(["ph-users", r.analysts == null ? "Analyst coverage could not be checked today." : r.analysts === 0 ? "No Wall Street analysts publish estimates on it." : `${r.analysts} analyst${r.analysts === 1 ? "" : "s"} publish estimates on it.`]);
  return out;
}

function detailRow(r, cols) {
  // Whole points when the pipeline sends them. Older data has one-decimal parts, and rounding those
  // one by one here would stop them adding up to the score, so they keep their decimal.
  const whole = r.score_parts.every((p) => Number.isInteger(p.points));
  const pts = (n) => (whole ? String(n) : (Math.round(n * 10) / 10).toFixed(1));
  const total = r.score_parts.reduce((a, p) => a + p.points, 0);
  return `<tr class="detail"><td colspan="${cols}"><div class="detail-inner">
    <div><h4>Why it passed</h4><ul class="facts">${facts(r).map(([i, t]) => `<li><i class="ph ${i}" aria-hidden="true"></i><span>${esc(t)}</span></li>`).join("")}</ul></div>
    <div><h4>How the ${r.score} score adds up</h4><div class="parts">${r.score_parts.map((p) => `
      <div class="part"><span>${esc(p.label)}</span><div class="part-track"><span style="width:${(p.points / p.max) * 100}%"></span></div><span class="pts">${pts(p.points)} / ${p.max}</span></div>`).join("")}
      <div class="part part-total"><span>Total</span><span>${Math.abs(total - r.score) < 0.05 ? "" : `Rounds to ${r.score}`}</span><span class="pts">${pts(total)} / ${r.score_parts.reduce((a, p) => a + p.max, 0)}</span></div>
    </div></div>
    <div class="detail-actions"><a class="btn btn-primary btn-sm" href="report.html?t=${encodeURIComponent(r.symbol)}">Full report on ${esc(r.symbol)}</a></div>
  </div></td></tr>`;
}

function renderSectors() {
  const el = document.querySelector("[data-sectors]");
  const total = data.results.length;
  const list = [{ name: "All", passed: total }, ...data.sectors.filter((s) => s.checked > 0 || s.excluded)];
  el.innerHTML = list.map((s) => `<button class="pill" type="button" data-sector="${esc(s.name)}" aria-pressed="${s.name === state.sector}">
    ${esc(s.name === "All" ? "All sectors" : s.name)}<span class="count">${s.passed}</span></button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("[data-sector]");
    if (!b) return;
    state.sector = b.dataset.sector;
    state.open = null;
    const u = new URL(location);
    state.sector === "All" ? u.searchParams.delete("sector") : u.searchParams.set("sector", state.sector);
    history.replaceState(null, "", u);
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

function unverifiedNote() {
  const list = (data.unverified || []).map((u) => (typeof u === "string" ? { symbol: u } : u))
    .filter((u) => u && u.symbol && (state.sector === "All" || !u.sector || u.sector === state.sector));
  if (!list.length) return "";
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
    const links = andList(g.map((u) => {
      const title = [u.name && u.name.replace(/\.$/, ""), u.reason].filter(Boolean).join(". ");
      return `<a href="report.html?t=${encodeURIComponent(u.symbol)}"${title ? ` title="${esc(title)}"` : ""}>${esc(u.symbol)}</a>`;
    }));
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
  const rows = data.results.filter((r) => state.sector === "All" || r.sector === state.sector).sort(SORTS[state.sort]);
  const sec = data.sectors.find((s) => s.name === state.sector);
  countEl.innerHTML = state.sector === "All"
    ? `<b>${rows.length}</b> ${rows.length === 1 ? "company" : "companies"} passed every check`
    : sec?.excluded ? `${esc(state.sector)} is not screened`
    : `<b>${rows.length}</b> of ${sec?.checked ?? 0} ${esc(state.sector)} companies passed every check`;

  if (sec?.excluded) {
    host.innerHTML = `<div class="panel empty"><h3>${esc(state.sector)} companies are not screened</h3><p>${esc(sec.excluded)}</p></div>`;
    return;
  }

  if (!rows.length) {
    host.innerHTML = `<div class="panel empty"><h3>No ${esc(state.sector)} company passes today</h3>
      <p>That is normal. The checks are strict on purpose, and some sectors rarely have cheap, debt-light, profitable small caps.
      Try another sector or come back after the next update.</p></div>${unverifiedNote()}`;
    return;
  }
  const cols = 10;
  host.innerHTML = `<div class="table-wrap"><table class="data">
    <thead><tr>
      <th>#</th><th>Company</th><th style="text-align:left">Score</th><th>Market value</th><th>Price / sales</th>
      <th>Net margin</th><th>Debt</th><th>Cash yield</th><th>Below high</th><th>Analysts</th>
    </tr></thead>
    <tbody>${rows.map((r, i) => `
      <tr class="row" tabindex="0" data-sym="${esc(r.symbol)}" aria-expanded="${state.open === r.symbol}">
        <td class="rank-cell">${i + 1}</td>
        <td class="co-cell"><div class="co"><span class="sym">${esc(r.symbol)}</span><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span></div>${flags(r)}</td>
        <td class="score-cell">${meter(r.score, band(r.score))}</td>
        <td>${money(r.mcap)}</td>
        <td>${times(r.ps)}</td>
        <td${r.one_time_note && r.net_margin_reported != null ? ` title="Leaves out one-time items. Reported margin: ${pct(r.net_margin_reported)}"` : ""}>${pct(r.net_margin)}</td>
        <td>${debtTag(r)}</td>
        <td>${pct(r.fcf_yield)}</td>
        <td>${r.drawdown == null ? "n/a" : pct(r.drawdown, 0)}</td>
        <td>${r.analysts ?? "n/a"}</td>
      </tr>${state.open === r.symbol ? detailRow(r, cols) : ""}`).join("")}
    </tbody></table></div>
    <p class="faint" style="font-size:13px;margin-top:12px">Select a row to see why it passed and how its score adds up. Cash yield is free cash flow as a share of market value.</p>
    ${unverifiedNote()}`;

  host.querySelectorAll("tr.row").forEach((tr) => {
    const toggle = () => { state.open = state.open === tr.dataset.sym ? null : tr.dataset.sym; renderTable(); };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });
}

document.querySelector("[data-sort]").addEventListener("change", (e) => { state.sort = e.target.value; renderTable(); });

load("screener.json").then((d) => {
  data = d;
  if (state.sector !== "All" && !d.sectors.some((s) => s.name === state.sector)) state.sector = "All";
  renderSectors();
  renderTable();
}).catch(() => {
  host.innerHTML = `<div class="panel empty"><h3>Screener data is not available yet</h3><p>Results appear after the first nightly update runs.</p></div>`;
});
