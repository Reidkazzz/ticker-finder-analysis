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

const VERDICT = {
  undervalued: { word: "Undervalued", tag: "tag-up" },
  fair: { word: "Fairly valued", tag: "tag-mid" },
  overvalued: { word: "Overvalued", tag: "tag-down" },
};
const NEWS_TAG = { bullish: "tag-up", bearish: "tag-down", neutral: "", none: "" };
const cap = (s) => s[0].toUpperCase() + s.slice(1);

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

function verdictPanel(r) {
  const v = r.valuation;
  if (!v) {
    return `<section class="panel panel-pad verdict"><h2>Verdict</h2>
      <p class="word" style="font-size:28px">Not enough data</p>
      <p class="line">We need at least a year of revenue or positive cash flow from SEC filings to estimate a fair value. Foreign companies that file under international rules, funds and very new listings often lack this.</p></section>`;
  }
  const up = v.upside;
  const conf = { higher: "All three methods agree well enough to form a range.", moderate: "Two methods were available.", low: "Only one method was available, so treat this range loosely." }[v.confidence];
  return `<section class="panel panel-pad verdict">
    <h2>Verdict</h2>
    <p class="word ${v.verdict}">${VERDICT[v.verdict].word}</p>
    <p class="line">Estimated fair value <b>${price(v.low)} to ${price(v.high)}</b>. The midpoint of <b>${price(v.mid)}</b> is
      <b class="${signClass(up)}">${pct(Math.abs(up), 0)} ${up >= 0 ? "above" : "below"}</b> today's price.</p>
    ${rangeViz(v, r.price)}
    <div class="methods">${v.methods.map((m) => `<div class="method-row"><b>${esc(m.name)}</b><span class="v">${price(m.value)}</span><p>${esc(m.note)}</p></div>`).join("")}</div>
    <p class="fine">${esc(conf)} This is an estimate, not a price target, and not investment advice.</p>
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

function healthSection(r) {
  if (!r.health) {
    return `<div class="h-top"><div><h2>Health check</h2><p>This company has no US-format financial statements on file with the SEC, so it cannot be scored.</p></div></div>`;
  }
  const s = r.health_score;
  const band = s >= 70 ? "strong" : s >= 40 ? "fair" : "weak";
  const word = { strong: "Healthy", fair: "Mixed", weak: "Weak" }[band];
  return `<div class="h-top">
      <div><h2>Health check</h2><p>Every number becomes a 1 to 100 score, where higher is better. Raw amounts like cash or share count only mean something next to price or history, so each is scored through the ratio that gives it meaning.</p></div>
      <div class="h-score" title="Average of all scored bars, excluding market mood"><b class="${band}">${s ?? "n/a"}</b><span>/ 100, ${word}</span></div>
    </div>
    <div class="h-groups">${r.health.map((g) => `
      <section class="panel h-group${g.bars.every((b) => b.context) ? " context" : ""}">
        <header><h3>${esc(g.name)}</h3><p>${esc(g.hint)}</p></header>
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

function insiderSection(r) {
  const c = r.insiders;
  if (!c) return "";
  return `<div class="h-top"><div><h2>Insider buying</h2><p>Open-market purchases filed with the SEC in the last 30 days.</p></div>
      <span class="tag ${c.tier === 1 ? "tag-solid" : ""}">${c.tier <= 2 ? "Tier " + c.tier : "Smaller"}</span></div>
    <div class="panel" style="padding:8px">${c.buyers.map((b) => `
      <div class="buyer">
        <div class="who"><b>${esc(b.name)}</b><span>${esc((b.roles || []).join(", ") || "Insider")}, bought ${esc(date(b.last))}</span></div>
        <div class="n">${money(b.value)}</div><div class="n">${price(b.avg_price)}</div>
        <div class="n stake">${b.new_position ? `<span class="up">New</span>` : b.stake_increase == null ? "" : pct(b.stake_increase, 0, true)}</div>
        <a class="doc" href="${esc(b.filing)}" target="_blank" rel="noopener"><i class="ph ph-arrow-up-right" aria-hidden="true"></i><span class="sr-only">SEC filing</span></a>
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
        <p class="where">${esc(r.sector)}, ${esc(r.industry)}</p>
      </div>
      <div class="r-price"><div class="p">${price(r.price)}</div><div class="sub">${money(r.mcap)} market value</div></div>
    </header>
    <div class="r-grid">${verdictPanel(r)}${pricePanel(r)}</div>
    ${healthSection(r)}
    ${insiderSection(r)}
    ${newsSection(r)}
    <div class="notes">
      ${f ? `<p>Financials from fiscal year ${f.fiscal_year}${f.fiscal_year_end ? ` (ended ${esc(date(f.fiscal_year_end, { month: "short", day: "numeric", year: "numeric" }))})` : ""}. Balance sheet as of ${esc(date(f.balance_as_of, { month: "short", day: "numeric", year: "numeric" }))}. Source: SEC filings.</p>` : ""}
      ${r.peer_group ? `<p>Compared with ${r.peer_count} other US-listed companies in ${esc(r.peer_group)}.</p>` : ""}
      <p>Educational estimates only. Not investment advice.</p>
    </div>`;
}

async function start() {
  document.title = "Ticker report | Ticker Finder";
  let popular = "";
  try {
    const u = await load("universe.json");
    popular = [...u].sort((a, b) => (b[3] || 0) - (a[3] || 0)).slice(0, 8)
      .map((r) => `<a href="?t=${encodeURIComponent(r[0])}"><span class="s">${esc(r[0])}</span><span class="n">${esc(r[1])}</span></a>`).join("");
  } catch {}
  host.innerHTML = `<section class="start">
    <h1>Look up any US stock</h1>
    <p class="lede">Get its news tone, 1 to 100 health scores and an estimated fair value, each with a one-line explanation.</p>
    ${popular ? `<div class="popular">${popular}</div>` : ""}
  </section>`;
  searchHost.querySelector("input").focus();
}

async function show(sym) {
  host.innerHTML = `<div style="padding-top:40px"><div class="skeleton" style="height:90px;margin-bottom:16px"></div>
    <div class="r-grid"><div class="skeleton" style="height:360px"></div><div class="skeleton" style="height:360px"></div></div></div>`;
  try {
    render(await load(`t/${sym.toUpperCase()}.json`));
    window.scrollTo({ top: 0 });
  } catch {
    host.innerHTML = `<div class="panel empty" style="margin-top:40px"><h3>No report for "${esc(sym)}"</h3>
      <p>Reports cover common stocks listed on the NYSE, Nasdaq and NYSE American that file with the SEC. Check the ticker, or search by company name above.</p></div>`;
  }
}

function route() {
  const t = new URLSearchParams(location.search).get("t");
  t ? show(t) : start();
}
route();
