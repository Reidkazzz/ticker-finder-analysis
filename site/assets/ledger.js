// A screener result's ledger: its 100 points part by part, the eight rules with its own figures, and its flags. Shared
// by the screener (an opened row) and the ticker report (a company on the list), so both say the same thing.
// `ctx` is { rules, industries, insiderDays } from screener.json and meta.json.
import { esc, icon, money, pct, price, tape, times } from "./common.js";

// A ratio a list is "under" (price to book, market value to net cash), cut rather than rounded under 1, so 0.997 reads
// 0.99x and not 1.00x on a list of companies under 1.
export const under = (x) => (x != null && x < 1 ? `${(Math.floor(x * 100) / 100).toFixed(2)}x` : times(x));
// Long-term debt to equity under 0.01 reads "<0.01x", not "0.00x", which would look like no debt at all.
export const deRatio = (x) => (x != null && x < 0.005 ? "<0.01x" : times(x));
// The debt column in words: no debt, more cash than debt, or long-term debt as a multiple of equity.
export const debtTag = (r) =>
  r.debt_status === "none" ? `<span class="debt-word">No debt</span>`
  : r.debt_status === "net_cash" ? `<span class="debt-word">Net cash</span>`
  : `<span class="debt-word low">Low, ${esc(deRatio(r.lt_de))}</span>`;

export const windowWords = (ctx) => (ctx.insiderDays ? `in the last ${ctx.insiderDays} days` : "recently");

// Each flag as [key, tone, icon, label, meaning], in the order the flags show. Flags are context, not verdicts, so their
// tiles are neutral and told apart by their icons; only the warning's tone ("down") colors its icon red.
export function flagDefs(ctx) {
  const dd = pct(ctx.rules?.out_of_favor_drawdown ?? 0.25, 0);
  return [
    ["out_of_favor", "", "trend-down", "Out of favor", `${dd} or more below its 52-week high`],
    ["industry_out_of_favor", "", "factory", "Industry out of favor", `The median company in its industry trades ${dd} or more below its 52-week high`],
    ["undiscovered", "", "binoculars", "Undiscovered", "Two or fewer analysts cover it"],
    ["under_book", "", "scales", "Under book value", "Its market value is less than its book value (shareholders' equity less preferred stock)"],
    ["insiders_buying", "", "user-plus", "Insiders buying", `Tier 1 or 2 open-market insider buying ${windowWords(ctx)}`],
    ["below_insider_price", "", "tag", "Below insiders' price", "The price is under the average price insiders paid"],
    ["above_upper_band", "down", "warning", "Above upper band, wait for a pullback", "The price is above the average of its last 20 weekly closes plus two standard deviations"],
  ];
}
export const flagTile = ([, tone, ic]) => `<span class="flag${tone ? ` flag-${tone}` : ""}" aria-hidden="true">${icon(ic)}</span>`;

// A row's flags as small icons, each named for screen readers. Past FLAGS_SHOWN, the rest are counted in a "+N" tile.
const FLAGS_SHOWN = 4;
export function flagIcons(r, ctx) {
  const f = flagDefs(ctx).filter(([k]) => r.flags.includes(k));
  if (!f.length) return `<span class="sr-only">No flags</span>`;
  const cut = f.length > FLAGS_SHOWN ? FLAGS_SHOWN - 1 : f.length;
  const rest = f.slice(cut);
  return `<ul class="flag-set">${f.slice(0, cut).map((d) => `<li title="${esc(`${d[3]}: ${d[4]}`)}">${flagTile(d)}<span class="sr-only">${esc(d[3])}</span></li>`).join("")}${
    rest.length ? `<li title="${esc(rest.map((d) => d[3]).join(", "))}"><span class="flag flag-more" aria-hidden="true">+${rest.length}</span><span class="sr-only">${esc(rest.map((d) => d[3]).join(", "))}</span></li>` : ""}</ul>`;
}

// What each score part was measured on, in the company's own figures.
function partInput(key, r, ctx) {
  switch (key) {
    case "ps": return `${times(r.ps)} price to sales`;
    case "debt": return r.debt_status === "none" ? "No debt" : r.debt_status === "net_cash" ? `${money(r.net_cash)} more cash than debt` : `Long-term debt ${deRatio(r.lt_de)} of equity`;
    case "margin": return `${pct(r.net_margin)} net margin${r.one_time_note ? ", without one-time items" : ""}`;
    case "fcf": return r.fcf_yield == null ? "No cash yield" : `${pct(r.fcf_yield)} of market value in free cash`;
    case "growth": return r.revenue_growth == null ? "No prior year" : `Sales ${pct(r.revenue_growth, 1, true)} on the year`;
    case "small": return `${money(r.mcap)} market value`;
    case "out_of_favor": return r.drawdown == null ? "No 52-week high yet" : `${pct(r.drawdown, 0)} below its 52-week high`;
    case "undiscovered": return r.analysts == null ? "Coverage not checked" : `${r.analysts} analyst${r.analysts === 1 ? "" : "s"}`;
    case "under_book": return !("pb" in r) ? "" : r.pb == null ? "Book value zero or less" : `${under(r.pb)} its book value`;
    case "insiders_buying": return r.insiders ? `Tier ${r.insiders.tier} buying ${windowWords(ctx)}` : `No Tier 1 or 2 buying ${windowWords(ctx)}`;
    default: return "";
  }
}

export function scoreLedger(r, ctx, { level = 4 } = {}) {
  // Whole points when the pipeline sends them. Older data has one-decimal parts, and rounding those one by one here
  // would stop them adding up to the score, so they keep their decimal.
  const whole = r.score_parts.every((p) => Number.isInteger(p.points));
  const pts = (n) => (whole ? String(n) : (Math.round(n * 10) / 10).toFixed(1));
  const total = r.score_parts.reduce((a, p) => a + p.points, 0);
  const max = r.score_parts.reduce((a, p) => a + p.max, 0);
  return `<div class="ledger-col">
    <div class="ledger-head"><h${level}>Score</h${level}><span class="ledger-total"><b>${esc(String(r.score))}</b> of ${max}</span></div>
    ${tape(r.score_parts, "tape-lg")}
    <table class="ledger-table"><caption class="sr-only">How the ${esc(String(r.score))} points add up</caption>
      <thead class="sr-only"><tr><th scope="col">Part</th><th scope="col">Measured on</th><th scope="col">Points</th></tr></thead>
      <tbody>${r.score_parts.map((p) => `<tr class="${p.points ? "" : "zero"}">
        <th scope="row">${esc(p.label)}</th><td class="in">${esc(partInput(p.key, r, ctx))}</td>
        <td class="pt"><span class="pt-bar" style="--f:${p.max ? Math.max(0, Math.min(1, p.points / p.max)) : 0}" aria-hidden="true"></span><b>${pts(p.points)}</b><span class="of">/${p.max}</span></td></tr>`).join("")}
      </tbody>
      <tfoot><tr><th scope="row">Total</th><td class="in">${Math.abs(total - r.score) < 0.05 ? "" : `Rounds to ${esc(String(r.score))}`}</td><td class="pt"><b>${pts(total)}</b><span class="of">/${max}</span></td></tr></tfoot>
    </table></div>`;
}

// The eight rules, each with the company's own figure beside the limit it passed.
export function rulesLedger(r, ctx, { level = 4, note = true } = {}) {
  const R = ctx.rules || {};
  const asReported = r.profitable_years_reported;
  const rows = [
    ["Price to sales", times(r.ps), `under ${R.max_ps ?? 2}`],
    ["Market value", money(r.mcap), `under ${money(R.max_mcap ?? 3e9).replace(".00", "")}`],
    ["Long-term debt to equity", r.debt_status === "none" ? "No debt" : `${deRatio(r.lt_de)}${r.net_cash > 0 ? ", more cash than debt" : ""}`, `under ${times(R.max_lt_de ?? 0.5)}`],
    ["Net profit margin", `${pct(r.net_margin)}${r.one_time_note && r.net_margin_reported != null ? ` (${pct(r.net_margin_reported)} reported)` : ""}`, `${pct(R.min_net_margin ?? 0.03, 0)} or more`],
    ["Based and incorporated in the US", r.hq ? `Based in ${r.hq}` : "Confirmed", "US"],
    ["Positive equity and free cash flow", `Free cash flow ${money(r.fcf)}`, "above $0"],
    ["Profitable years", `${r.profitable_years} of ${r.years_checked}${asReported != null && asReported !== r.profitable_years ? ` (${asReported} as reported)` : ""}`, `${R.min_profitable_years ?? 2} of 3`],
    ["Sales growth", r.revenue_growth == null ? "n/a" : pct(r.revenue_growth, 1, true), `${pct(R.min_revenue_growth ?? -0.05, 0)} or more`],
  ];
  return `<div class="ledger-col">
    <div class="ledger-head"><h${level}>Passes all eight rules</h${level}><span class="ledger-total">Fiscal ${esc(String(r.fiscal_year ?? ""))}</span></div>
    <table class="ledger-table rules"><caption class="sr-only">The company's figure for each rule and the limit it passed</caption>
      <thead class="sr-only"><tr><th scope="col">Rule</th><th scope="col">Its figure</th><th scope="col">Limit</th></tr></thead>
      <tbody>${rows.map(([k, v, lim]) => `<tr><th scope="row">${icon("check", "ok")}${esc(k)}</th><td class="in">${esc(v)}</td><td class="lim">${esc(lim)}</td></tr>`).join("")}</tbody>
    </table>
    <p class="ledger-note">Sold ${esc(money(r.revenue))} in fiscal ${esc(String(r.fiscal_year ?? ""))}.${note && r.one_time_note ? ` ${esc(r.one_time_note)}` : ""}</p>
  </div>`;
}

// The flags a company carries, in words, and what the flags that aren't told elsewhere mean for it. `link` adds the
// button to its report (the screener's opened row).
export function sideLedger(r, ctx, { link = true, level = 4 } = {}) {
  const f = flagDefs(ctx).filter(([k]) => r.flags.includes(k));
  const notes = [];
  const ins = r.insiders;
  if (ins) {
    const paid = ins.avg_price;
    const vs = paid ? r.price / paid - 1 : null;
    notes.push(`Insiders bought ${money(ins.total_value)} of its stock on the open market ${windowWords(ctx)} (Tier ${ins.tier})${
      paid ? `, paying ${price(paid)} a share on average. Today's price of ${price(r.price)} is ${pct(Math.abs(vs), Math.abs(vs) < 0.1 ? 1 : 0)} ${vs < 0 ? "below" : "above"} that` : ""}.`);
  }
  if (r.flags.includes("industry_out_of_favor")) {
    const ind = (ctx.industries || []).find((x) => x.industry === r.industry);
    notes.push(ind ? `Its industry, ${ind.industry}, is out of favor: its median company trades ${pct(ind.median_drawdown, 0)} below its 52-week high.`
      : `Its industry, ${r.industry}, is out of favor.`);
  }
  if (r.drawdown != null && r.high52 != null) notes.push(`Trades ${pct(r.drawdown, 0)} below its 52-week high of ${price(r.high52)}.`);
  if (r.flags.includes("above_upper_band") && r.upper_band != null) {
    notes.push(`Its price of ${price(r.price)} is above its upper band of ${price(r.upper_band)} (the average of its last 20 weekly closes plus two standard deviations), so it has risen unusually fast. Many traders would wait for the price to pull back before buying.`);
  }
  return `<div class="ledger-col ledger-side">
    <div class="ledger-head"><h${level}>Flags</h${level}><span class="ledger-total">${f.length || "None"}</span></div>
    ${f.length ? `<ul class="flag-names">${f.map((d) => `<li>${flagTile(d)}<span><b>${esc(d[3])}</b>${esc(d[4])}.</span></li>`).join("")}</ul>` : `<p class="ledger-note">No flags. Flags never keep a company off the list.</p>`}
    ${notes.length ? `<ul class="ledger-notes">${notes.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    ${link ? `<a class="btn btn-primary btn-sm ledger-go" href="report.html?t=${encodeURIComponent(r.symbol)}">Open the ${esc(r.symbol)} report${icon("arrow-right")}</a>` : ""}
  </div>`;
}

// The whole ledger: score, rules and flags side by side. Options: link (the report button), level (its headings'
// level, 4 under the screener's table, 3 under a page's h2) and note (the one-time items note in the rules column).
export function ledger(r, ctx, opts = {}) {
  return `<div class="ledger" role="region" aria-label="${esc(r.symbol)} score and rules">${scoreLedger(r, ctx, opts)}${rulesLedger(r, ctx, opts)}${sideLedger(r, ctx, opts)}</div>`;
}
