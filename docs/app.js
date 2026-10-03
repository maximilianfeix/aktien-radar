"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const nf = d => new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const NF = [nf(0), nf(1), nf(2), nf(3), nf(4)];
const num = (x, d = 2) => x == null ? "–" : NF[d].format(x);
const price = x => x == null ? "–" : NF[x >= 1000 ? 0 : x >= 1 ? 2 : 4].format(x);
const pct = (x, d = 1) => {
  if (x == null) return "–";
  const r = Math.round(x * 100 * 10 ** d) / 10 ** d;  // round first so that -0.0001 does not print as "-0,0 %"
  return (r > 0 ? "+" : "") + NF[d].format(r || 0) + " %";
};
const CURRENCY = { L: "GBP", SW: "CHF", CO: "DKK", DE: "EUR", AS: "EUR", PA: "EUR", MC: "EUR", MI: "EUR" };
const currency = a => CURRENCY[a.ticker.split(".")[1]] || "USD";
const unit = a => a.ticker.endsWith(".L") ? 0.01 : 1;  // London quotes are in pence; money amounts are shown in pounds
const big = x => x == null ? "–" : x >= 1e12 ? num(x / 1e12, 2) + " Bio." : x >= 1e9 ? num(x / 1e9, 1) + " Mrd." : num(x / 1e6, 0) + " Mio.";
const when = iso => new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
const SIGNAL_CLASS = { Kaufen: "buy", Halten: "hold", Beobachten: "watch", Meiden: "avoid" };
const badge = (s, label = s) => `<span class="badge ${SIGNAL_CLASS[s]}"><i></i>${esc(label)}</span>`;
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5"];
const HORIZON = { short: "kurzfristig", long: "langfristig" };
const REGIME = { risk_on: "Risk-on", risk_off: "Risk-off", neutral: "Neutral" };

const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};
const state = {
  fMarket: "all", fSignal: "all", fWatch: false, preset: null, compare: store.get("compare", null), query: "", sort: "long_score", dir: -1, heat: "chg_1d", group: 0,
  watch: new Set(store.get("watch", [])), positions: store.get("positions", []), calc: store.get("calc", { depot: 10000, risk: 1 }),
};
let DATA, BY, ROWS = [], CHARTS, BACKTESTS, NEWS = null;
const getJSON = (path, opts) => fetch(path, opts).then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });

function seg(el, options, current, onPick) {
  el.innerHTML = options.map(([v, l]) => `<button type="button" data-v="${esc(v)}" aria-pressed="${v == current}">${esc(l)}</button>`).join("");
  el.onclick = e => {
    const b = e.target.closest("button");
    if (!b) return;
    el.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", x === b));
    onPick(b.dataset.v);
  };
}

function spark(values, w, h, area = false) {
  if (!values || values.length < 2) return "";
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 2 - ((v - lo) / span) * (h - 4)]);
  const line = "M" + pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`
    + (area ? `<path d="${line}L${w} ${h}L0 ${h}Z" fill="var(--signal-ink)" opacity=".12"/>` : "")
    + `<path d="${line}" fill="none" stroke="var(--signal-ink)" stroke-width="${area ? 2 : 1.5}" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}

function reasons(a, max = 9) {
  return `<ul class="why">${a.reasons.slice(0, max).map(r => `<li class="pro">${esc(r)}</li>`).join("")}${a.risks.slice(0, max).map(r => `<li class="con">${esc(r)}</li>`).join("")}</ul>`;
}

function ring(score) {
  const c = 2 * Math.PI * 30;
  return `<div class="ring" title="Score ${num(score, 0)} von 100"><svg width="76" height="76" viewBox="0 0 76 76"><circle cx="38" cy="38" r="30" fill="none" stroke="var(--grid)" stroke-width="7"/>
    <circle cx="38" cy="38" r="30" fill="none" stroke="var(--signal-ink)" stroke-width="7" stroke-linecap="round" stroke-dasharray="${(score / 100 * c).toFixed(1)} ${c.toFixed(1)}"/></svg><b>${num(score, 0)}</b></div>`;
}

function pickCard(a, horizon, title) {
  if (!a) return `<div class="card pick" style="cursor:default"><h3>${title}</h3><div class="pick-name">Kein Kaufsignal</div><p class="sub" style="margin-top:8px">Kein Wert erfüllt aktuell alle Bedingungen. Cash ist auch eine Position.</p></div>`;
  return `<button type="button" class="card pick" data-open="${esc(a.ticker)}"><h3>${title}</h3>
    <div class="pick-top"><div><div class="pick-name">${esc(a.name)}</div><div class="small">${esc(a.ticker)} · ${esc(DATA.markets[a.market])}${a.sector ? " · " + esc(a.sector) : ""}</div></div>${ring(a[horizon + "_score"])}</div>
    ${spark(a.spark, 600, 64, true)}
    <dl class="levels"><div><dt>Einstieg</dt><dd>${price(a.price)}</dd></div><div><dt>Stop</dt><dd>${price(a.stop)}</dd></div><div><dt>Ziel</dt><dd>${price(a.target)}</dd></div></dl>
    ${reasons(a, 4)}</button>`;
}

function ago(iso) {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 6e4));
  return min < 1 ? "gerade eben" : min < 60 ? `vor ${min} Min.` : min < 1440 ? `vor ${Math.round(min / 60)} Std.` : `vor ${Math.round(min / 1440)} Tagen`;
}
function countUp(el, to, digits = 0, suffix = "") {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = num(to, digits) + suffix; return; }
  const t0 = performance.now(), run = t => {
    const k = Math.min(1, (t - t0) / 1100), eased = 1 - (1 - k) ** 4;
    el.textContent = num(to * eased, digits) + suffix;
    if (k < 1) requestAnimationFrame(run);
  };
  requestAnimationFrame(run);
}
function gauge(score) {
  const arc = Math.PI * 80, color = score >= 66 ? "var(--good)" : score >= 40 ? "var(--warning)" : "var(--critical)";
  return `<div class="gauge"><svg viewBox="0 0 200 112" role="img" aria-label="Markt-Ampel ${score} von 100">
    <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="var(--grid)" stroke-width="14" stroke-linecap="round"/>
    <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="${color}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${(score / 100 * arc).toFixed(1)} ${arc.toFixed(1)}"/></svg><b>${score}</b></div>`;
}

function renderOverview() {
  const r = DATA.regime, o = DATA.picks.overall, buys = DATA.assets.filter(a => a.long_signal === "Kaufen").length;
  const above = Object.values(DATA.breadth || {}).reduce((s, b) => s + b.above, 0), total = Object.values(DATA.breadth || {}).reduce((s, b) => s + b.total, 0);
  $("#regime").innerHTML = badge({ risk_on: "Kaufen", neutral: "Beobachten", risk_off: "Meiden" }[r.state], "Marktlage: " + r.label);
  $("#status").textContent = `Letzter Lauf ${ago(DATA.generated_at)} · ${DATA.assets.length} Werte geprüft`;
  $("#status").title = new Date(DATA.generated_at).toLocaleString("de-DE");
  $("#lede-count").textContent = DATA.assets.length;
  $("#regime-text").textContent = r.text;
  $("#disclaimer").textContent = DATA.disclaimer;
  $("#picks").innerHTML = pickCard(BY[o.short[0]], "short", "Kurzfristig · Tage bis Wochen") + pickCard(BY[o.long[0]], "long", "Langfristig · Monate bis Jahre");

  const movers = [...DATA.assets].filter(a => a.chg_1d != null).sort((a, b) => Math.abs(b.chg_1d) - Math.abs(a.chg_1d)).slice(0, 18);
  const tape = movers.map(a => `<button type="button" data-open="${esc(a.ticker)}" tabindex="-1"><b>${esc(a.name)}</b><span>${price(a.price)}</span><span class="${a.chg_1d >= 0 ? "up" : "down"}">${a.chg_1d >= 0 ? "▲" : "▼"} ${pct(a.chg_1d)}</span></button>`).join("");
  $("#tape-in").innerHTML = tape + tape; $("#tape").hidden = !movers.length;

  $("#numbers").innerHTML = `<div><div class="n" data-n="${DATA.assets.length}"></div><p><b>Werte geprüft.</b> Aktien, ETFs und Coins aus vier Märkten, jede Stunde neu bewertet.</p></div>
    <div><div class="n" data-n="${buys}"></div><p><b>langfristige Kaufsignale.</b> Aufwärtstrend, positives Momentum und ein Score von mindestens 70.</p></div>
    <div><div class="n" data-n="${total ? above / total * 100 : 0}" data-s=" %"></div><p><b>über der 200-Tage-Linie.</b> Je breiter der Aufwärtstrend, desto tragfähiger ist er.</p></div>
    <div><div class="n" data-n="${r.ampel?.score ?? 0}"></div><p><b>von 100 auf der Markt-Ampel.</b> Trend der Indizes, Marktbreite und Nervosität in einer Zahl.</p></div>`;
  new IntersectionObserver((entries, io) => { if (entries.some(e => e.isIntersecting)) { io.disconnect(); document.querySelectorAll("#numbers .n").forEach(el => countUp(el, +el.dataset.n, 0, el.dataset.s || "")); } }).observe($("#numbers"));

  if (r.ampel) {
    const part = (l, v) => `<span>${l}<b>${num(v * 100, 0)} %</b></span>`;
    $("#ampel").innerHTML = `<h3>Markt-Ampel</h3>${gauge(r.ampel.score)}<div class="gauge-label">${esc(r.ampel.label)}</div>
      <div class="rows">${part("Indizes im Aufwärtstrend", r.ampel.parts.trend)}${part("Aktien über 200-Tage-Linie", r.ampel.parts.breadth)}${part("Ruhe am Markt (VIX)", r.ampel.parts.calm)}</div>`;
  }
  if (DATA.ai) {
    $("#ai").hidden = false;
    $("#ai").innerHTML = `<h3>Einordnung von Claude</h3>` + DATA.ai.text.split(/\n\s*\n/).map(p => `<p>${esc(p)}</p>`).join("");
  }
  const core = DATA.core;
  if (core) {
    const alloc = Object.entries(core.allocation || {}), cash = Math.max(0, 1 - alloc.reduce((s, [, w]) => s + w, 0));
    const parts = alloc.map(([t, w], i) => ({ name: BY[t]?.name || t, w, color: `var(${SERIES[i % SERIES.length]})` }));
    if (cash > 0.005) parts.push({ name: "Cash", w: cash, color: "var(--muted)" });
    $("#core").innerHTML = `<h3>Kernstrategie hält gerade</h3><div style="font-weight:500;font-size:19px;letter-spacing:-.02em">${esc(core.name)}</div>
      <div class="alloc">${parts.map(p => `<i style="width:${p.w * 100}%;background:${p.color}"></i>`).join("")}</div>
      <div class="rows">${parts.map(p => `<span><i style="background:${p.color}"></i>${esc(p.name)}<b>${num(p.w * 100, 0)} %</b></span>`).join("")}</div>
      <p class="small" style="margin:12px 0 0">Seit 2021: ${pct(core.out_of_sample.cagr)} p. a., größter Rückgang ${pct(core.out_of_sample.max_drawdown, 0)}. Umschichtung am Monatsende.</p>`;
  } else $("#core").hidden = true;
  $("#breadth").innerHTML = `<h3>Marktbreite</h3>` + Object.entries(DATA.breadth || {}).map(([m, b]) =>
    `<div class="meter"><span>${esc(DATA.markets[m])}</span><div class="track"><i style="width:${b.above / b.total * 100}%"></i></div><b>${b.above} / ${b.total}</b></div>`).join("")
    + `<p class="small" style="margin:12px 0 0">Anteil der Werte über ihrer 200-Tage-Linie.</p>`;
  const t = DATA.track;
  if (t) $("#track").innerHTML = `<h3>Live-Bilanz der Kaufsignale</h3>` + (t.closed
    ? `<div class="stat">${num(t.hit_rate * 100, 0)} % Treffer</div><p class="small">${t.closed} abgeschlossene Signale, im Schnitt ${pct(t.avg_return)}.</p>`
    : `<div class="stat">${t.open} offen</div><p class="small">Noch kein Signal abgeschlossen.</p>`)
    + `<p class="small" style="margin:0">Laufende Signale im Schnitt ${pct(t.open_avg_return)}. Mitgeschrieben seit ${new Date(t.since).toLocaleDateString("de-DE")}. <a href="#signale">Zur Bilanz</a></p>`;
  const tiles = Object.values(r.indices).map(i => `<div class="card"><div class="small">${esc(i.name)}</div><div class="stat" style="font-size:22px">${price(i.price)}</div>
    <div class="small" style="margin-bottom:10px">${pct(i.vs_sma200)} zur 200-Tage-Linie</div>${badge(i.uptrend ? "Kaufen" : "Meiden", i.uptrend ? "Aufwärtstrend" : "Abwärtstrend")}</div>`);
  if (r.vix != null) tiles.push(`<div class="card"><div class="small">VIX (Nervosität)</div><div class="stat" style="font-size:22px">${num(r.vix, 1)}</div><div class="small">unter 20 ruhig, über 28 Stress</div></div>`);
  $("#indices").innerHTML = tiles.join("");

  const day = [...DATA.assets].filter(a => a.chg_1d != null).sort((a, b) => b.chg_1d - a.chg_1d);
  const mover = a => `<button type="button" data-open="${esc(a.ticker)}"><span>${esc(a.name)}</span><span class="tk">${esc(a.ticker)}</span><b class="${a.chg_1d >= 0 ? "up" : "down"}">${pct(a.chg_1d)}</b></button>`;
  $("#gainers").innerHTML = day.slice(0, 6).map(mover).join(""); $("#losers").innerHTML = day.slice(-6).reverse().map(mover).join("");

  $("#sectors thead").innerHTML = `<tr><th class="l">Sektor</th><th>Werte</th><th>1 M</th><th>12-1 M</th><th>im Aufwärtstrend</th><th>Kaufsignale</th></tr>`;
  $("#sectors tbody").innerHTML = (DATA.sectors || []).map(x => `<tr><td class="name"><b>${esc(x.sector)}</b></td><td>${x.count}</td><td>${pct(x.avg_1m)}</td><td>${pct(x.avg_12_1, 0)}</td><td>${x.uptrend} / ${x.count}</td><td>${x.buys}</td></tr>`).join("")
    || `<tr><td class="l">Noch keine Sektordaten.</td></tr>`;
  const soon = DATA.assets.filter(a => a.earnings_in_days != null && a.earnings_in_days <= 14).sort((a, b) => a.earnings_in_days - b.earnings_in_days).slice(0, 14);
  $("#earnings").innerHTML = soon.map(a => `<button type="button" data-open="${esc(a.ticker)}"><span>${esc(a.name)}</span>${badge(a.long_signal)}<b>${a.earnings_in_days === 0 ? "heute" : a.earnings_in_days === 1 ? "morgen" : "in " + a.earnings_in_days + " Tagen"}</b></button>`).join("")
    || `<p class="small" style="margin:0">In den nächsten 14 Tagen stehen bei den beobachteten Aktien keine Termine an.</p>`;
}

const safeLink = p => p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">Quelle</a>` : "";
const newsList = n => `<ul class="why">${n.pro.map(p => `<li class="pro">${esc(p.text)}${safeLink(p)}</li>`).join("")}${n.con.map(p => `<li class="con">${esc(p.text)}${safeLink(p)}</li>`).join("")}${n.dates.map(p => `<li>${esc(p.text)}${safeLink(p)}</li>`).join("")}</ul>`;
async function renderNews() {
  try { NEWS = await getJSON("data/news.json", { cache: "no-cache" }); } catch { return; }
  if (!NEWS?.at || Date.now() - Date.parse(NEWS.at) > 36 * 36e5) { NEWS = null; return; }
  const items = NEWS.items.filter(n => BY[n.ticker]);
  $("#news").hidden = false;
  $("#news").innerHTML = `<h3>Nachrichtenlage · ${ago(NEWS.at)}</h3><div class="grid g2">${items.map(n => `<div><button type="button" class="chip" data-open="${esc(n.ticker)}" style="margin-bottom:10px">${esc(BY[n.ticker].name)}</button>${newsList(n)}</div>`).join("")}</div>`
    + (NEWS.market.length ? `<p class="small" style="margin:14px 0 0"><b>Gesamtmarkt:</b> ${NEWS.market.map(p => esc(p.text) + safeLink(p)).join(" ")}</p>` : "")
    + `<p class="small" style="margin:8px 0 0">Automatisch von Claude recherchiert. Jede Aussage ist mit ihrer Quelle verlinkt.</p>`;
}

async function renderChanges() {
  let log = [];
  try { log = await getJSON("data/changes.json", { cache: "no-cache" }); } catch { /* optional */ }
  $("#changes").innerHTML = log.slice(0, 14).map(c => `<button type="button" data-open="${esc(c.ticker)}"><b>${esc(c.name)}</b><span class="tk">${esc(c.ticker)} · ${HORIZON[c.horizon]}</span>
    ${badge(c.from)}<span aria-hidden="true">→</span>${badge(c.to)}<span class="small when">${when(c.at)}</span></button>`).join("")
    || `<p class="small" style="margin:10px">Seit dem Start gab es noch keinen Signalwechsel.</p>`;
}

const HEAT = { chg_1d: ["1 Tag", 0.03], mom_1m: ["1 Monat", 0.15], mom_12_1: ["12-1 Monate", 0.5] };
function renderHeat() {
  const scale = HEAT[state.heat][1];
  $("#heat-min").textContent = pct(-scale, 0); $("#heat-max").textContent = pct(scale, 0);
  $("#heat").innerHTML = Object.entries(DATA.markets).map(([m, label]) => {
    const tiles = DATA.assets.filter(a => a.market === m).sort((a, b) => (b[state.heat] ?? -9) - (a[state.heat] ?? -9)).map(a => {
      const v = a[state.heat], i = v == null ? 0 : Math.min(1, Math.abs(v) / scale);
      const bg = `color-mix(in oklab, var(${v >= 0 ? "--pos" : "--neg"}) ${(i * 100).toFixed(0)}%, var(--mid))`;
      return `<button type="button" data-open="${esc(a.ticker)}" title="${esc(a.name)}: ${pct(v)}" style="background:${bg};${i > 0.55 ? "color:#fff" : ""}"><b>${esc(m === "etf" ? a.ticker.replace(/\.DE$/, "") : a.name)}</b><span>${pct(v)}</span></button>`;
    }).join("");
    return `<div class="heat-group"><h3>${esc(label)}</h3><div class="heat">${tiles}</div></div>`;
  }).join("");
}

const COLS = [["watch", "", ""], ["name", "Wert", "l"], ["spark", "1 Jahr", ""], ["price", "Kurs", ""], ["chg_1d", "1 T", ""], ["mom_1m", "1 M", ""],
  ["mom_12_1", "12-1 M", ""], ["rsi14", "RSI", ""], ["short_score", "Kurzfrist", ""], ["long_score", "Langfrist", ""]];
function buildTable() {
  const score = (a, h) => `${badge(a[h + "_signal"])} ${num(a[h + "_score"], 0)}<span class="bar"><b style="width:${a[h + "_score"]}%"></b></span>`;
  const body = $("#assets tbody");
  body.innerHTML = DATA.assets.map(a => `<tr class="row" data-open="${esc(a.ticker)}" tabindex="0">
    <td><button type="button" class="star" data-star="${esc(a.ticker)}" aria-pressed="${state.watch.has(a.ticker)}" aria-label="${esc(a.name)} auf die Watchlist">★</button></td>
    <td class="name"><b>${esc(a.name)}</b><br><span class="tk">${esc(a.ticker)} · ${esc(DATA.markets[a.market])}</span></td>
    <td>${spark(a.spark, 84, 26)}</td><td>${price(a.price)}</td><td>${pct(a.chg_1d)}</td><td>${pct(a.mom_1m)}</td><td>${pct(a.mom_12_1, 0)}</td>
    <td>${num(a.rsi14, 0)}</td><td>${score(a, "short")}</td><td>${score(a, "long")}</td></tr>`).join("");
  ROWS = [...body.children].map((tr, i) => ({ tr, a: DATA.assets[i], text: (DATA.assets[i].name + " " + DATA.assets[i].ticker).toLowerCase() }));
  applyTable();
}
const PRESETS = {
  pullback: ["Rücksetzer im Aufwärtstrend", a => a.vs_sma200 > 0 && a.rsi2 != null && a.rsi2 < 15],
  highs: ["Nahe am 52-Wochen-Hoch", a => a.vs_sma200 > 0 && a.from_high != null && a.from_high > -0.02],
  quality: ["Qualität mit Kaufsignal", a => a.quality >= 0.7 && a.long_signal === "Kaufen"],
  dividend: ["Dividende im Aufwärtstrend", a => a.dividend_yield >= 0.025 && a.vs_sma200 > 0],
  oversold: ["Überverkauft", a => a.rsi14 != null && a.rsi14 < 30],
  calm: ["Ruhige Werte mit Kaufsignal", a => a.vol != null && a.vol < 0.2 && a.long_signal === "Kaufen"],
  earnings: ["Zahlen in 14 Tagen", a => a.earnings_in_days != null && a.earnings_in_days <= 14],
};
function visibleRows() {
  const q = state.query.toLowerCase();
  return ROWS.filter(r => (state.fMarket === "all" || r.a.market === state.fMarket)
    && (state.fSignal === "all" || r.a.long_signal === state.fSignal || r.a.short_signal === state.fSignal)
    && (!state.fWatch || state.watch.has(r.a.ticker)) && (!q || r.text.includes(q)) && (!state.preset || PRESETS[state.preset][1](r.a)));
}
function applyTable() {
  $("#assets thead").innerHTML = "<tr>" + COLS.map(([k, l, c]) => `<th class="${c}">${k === "watch" || k === "spark" ? l : `<button type="button" data-k="${k}">${l}${state.sort === k ? (state.dir < 0 ? " ↓" : " ↑") : ""}</button>`}</th>`).join("") + "</tr>";
  const shown = new Set(visibleRows());
  const sorted = [...ROWS].sort((x, y) => {
    const a = x.a[state.sort], b = y.a[state.sort];
    if (a == null) return 1; if (b == null) return -1;
    return (typeof a === "string" ? a.localeCompare(b) : a - b) * state.dir;
  });
  const body = $("#assets tbody"), frag = document.createDocumentFragment();
  sorted.forEach(r => { r.tr.hidden = !shown.has(r); frag.appendChild(r.tr); });
  body.appendChild(frag);
  $("#count").textContent = `${shown.size} von ${ROWS.length} Werten` + (DATA.dropped.length ? ` · ohne aktuelle Kursdaten: ${DATA.dropped.join(", ")}` : "");
}
function exportCSV() {
  const fields = ["ticker", "name", "market", "price", "chg_1d", "mom_1m", "mom_12_1", "rsi14", "short_score", "short_signal", "long_score", "long_signal", "stop", "target"];
  const rows = visibleRows().map(r => fields.map(f => `"${String(r.a[f] ?? "").replace(/"/g, '""')}"`).join(";"));
  const blob = new Blob(["﻿" + fields.join(";") + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "aktien-radar.csv" });
  link.click(); URL.revokeObjectURL(link.href);
}

/* Line chart shared by the backtests and the price chart in the drawer. */
function lineChart(box, series, { log = false, fmt, height = 320, hlines = [], direct = false } = {}) {
  const tip = $(".tip", box);
  box.querySelector("svg, p")?.remove();
  series = series.filter(s => s.pts.length > 1);
  if (!series.length) {
    box.insertAdjacentHTML("afterbegin", `<p class="small" style="padding:24px 0;margin:0">Dafür liegen gerade keine Kursdaten vor.</p>`);
    box.onmousemove = box.ontouchmove = box.ontouchstart = null;
    return;
  }
  const W = box.clientWidth || 600, H = height, labels = direct && series.length <= 4 && W > 640;
  const m = { t: 12, r: labels ? 190 : hlines.length ? 56 : 12, b: 26, l: 52 };
  const f = v => log ? Math.log(v) : v;
  const xs = series.flatMap(s => s.pts.map(p => p[0])), ys = series.flatMap(s => s.pts.map(p => p[1])).concat(hlines.map(h => h.v));
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const y0 = log ? f(lo * .95) : lo - (hi - lo) * .06, y1 = log ? f(hi * 1.05) : hi + (hi - lo) * .06;
  const X = t => m.l + (t - x0) / (x1 - x0 || 1) * (W - m.l - m.r), Y = v => m.t + (y1 - f(v)) / (y1 - y0 || 1) * (H - m.t - m.b);
  let ticks = [];
  if (log) { for (let v = 0.125; v < 4096; v *= 2) if (f(v) > y0 && f(v) < y1) ticks.push(v); }
  else {
    const raw = (y1 - y0) / 4, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 2.5, 5, 10].map(k => k * mag).find(s => s >= raw);
    for (let v = Math.ceil(y0 / step) * step; v < y1; v += step) ticks.push(v);
  }
  let svg = ticks.map(v => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="12" fill="var(--muted)">${fmt(v)}</text>`).join("");
  const months = [], start = new Date(x0), span = (x1 - x0) / 864e5;
  for (let d = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1)); d <= x1; d.setUTCMonth(d.getUTCMonth() + 1)) months.push(new Date(d));
  const marks = span > 800 ? months.filter(d => d.getUTCMonth() === 0) : months;
  const every = Math.ceil(marks.length / Math.max(2, Math.floor((W - m.l - m.r) / 70)));
  svg += marks.filter((_, i) => i % every === 0).map(d => `<text x="${X(+d)}" y="${H - 6}" text-anchor="middle" font-size="12" fill="var(--muted)">${span > 800 ? d.getUTCFullYear() : d.toLocaleDateString("de-DE", { month: "short", year: "2-digit", timeZone: "UTC" })}</text>`).join("");
  svg += hlines.map(h => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(h.v)}" y2="${Y(h.v)}" stroke="${h.color}" stroke-width="1.5" stroke-dasharray="5 4"/><text x="${W - m.r + 6}" y="${Y(h.v) + 4}" font-size="12" fill="var(--ink-2)">${esc(h.label)}</text>`).join("");
  svg += series.map(s => `<path d="M${s.pts.map(p => X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join("L")}" fill="none" stroke="${s.color}" stroke-width="${s.thin ? 1.5 : 2}" stroke-linejoin="round"/>`).join("");
  if (labels) {
    const ends = series.map(s => ({ s, y: Y(s.pts.at(-1)[1]) })).sort((a, b) => a.y - b.y);
    ends.forEach((e, i) => { if (i && e.y - ends[i - 1].y < 30) e.y = ends[i - 1].y + 30; });
    svg += ends.map(e => `<text x="${W - m.r + 8}" y="${e.y}" font-size="12" fill="var(--ink-2)"><tspan font-weight="600" fill="var(--ink)">${fmt(e.s.pts.at(-1)[1])}</tspan><tspan x="${W - m.r + 8}" dy="14">${esc(e.s.name.length > 22 ? e.s.name.slice(0, 21) + "…" : e.s.name)}</tspan></text>`).join("");
  }
  svg += `<g class="hover" style="display:none"><line y1="${m.t}" y2="${H - m.b}" stroke="var(--axis)"/>${series.map(s => `<circle r="4.5" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`).join("")}</g>`;
  box.insertAdjacentHTML("afterbegin", `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Kursverlauf, Werte stehen in der Tabelle bzw. im Text daneben">${svg}</svg>`);
  const hover = $(".hover", box), dots = hover.querySelectorAll("circle"), line = $("line", hover), ref = series[0].pts;
  const move = e => {
    const px = (e.touches ? e.touches[0].clientX : e.clientX) - box.getBoundingClientRect().left;
    const t = x0 + (Math.min(Math.max(px, m.l), W - m.r) - m.l) / (W - m.l - m.r) * (x1 - x0);
    let i = ref.findIndex(p => p[0] >= t); if (i < 0) i = ref.length - 1;
    const stamp = ref[i][0], tx = X(stamp);
    hover.style.display = ""; line.setAttribute("x1", tx); line.setAttribute("x2", tx);
    const rows = series.map((s, k) => {
      const p = s.pts.findLast(q => q[0] <= stamp);
      dots[k].style.display = p ? "" : "none";
      if (p) { dots[k].setAttribute("cx", tx); dots[k].setAttribute("cy", Y(p[1])); }
      return { s, v: p?.[1] };
    }).filter(r => r.v != null).sort((a, b) => b.v - a.v);
    tip.innerHTML = `<div class="d">${new Date(stamp).toLocaleDateString("de-DE", { dateStyle: "medium" })}</div>` + rows.map(r => `<div class="r"><i style="background:${r.s.color}"></i>${esc(r.s.name)}<b>${fmt(r.v)}</b></div>`).join("");
    tip.style.display = "block"; tip.style.top = "8px";
    tip.style.left = Math.max(0, tx + 14 + tip.offsetWidth > W ? tx - 14 - tip.offsetWidth : tx + 14) + "px";
  };
  const leave = () => { hover.style.display = "none"; tip.style.display = "none"; };
  box.onmousemove = move; box.ontouchmove = move; box.ontouchstart = move; box.onmouseleave = leave; box.ontouchend = leave;
}

/* Detail drawer */
function rangeBar(a) {
  if (a.from_high == null || !a.spark?.length) return "";
  const high = a.price / (1 + a.from_high), low = Math.min(...a.spark, a.price), pos = high > low ? (a.price - low) / (high - low) * 100 : 100;
  return `<div class="range" title="Lage in der 52-Wochen-Spanne"><i style="left:${Math.min(100, Math.max(0, pos)).toFixed(0)}%"></i></div><div class="range-ends"><span>52-W-Tief ${price(low)}</span><span>52-W-Hoch ${price(high)}</span></div>`;
}
let lastFocus = null;
function calcOutput(a) {
  const { depot, risk } = state.calc, k = unit(a), cost = a.price * k, perUnit = (a.price - a.stop) * k;
  if (!(depot > 0) || !(risk > 0) || !(perUnit > 0)) return "Depotgröße und Risiko eingeben.";
  const whole = a.market !== "crypto", raw = Math.min(depot * risk / 100 / perUnit, depot / cost), qty = whole ? Math.floor(raw) : Math.floor(raw * 1e4) / 1e4;
  if (qty <= 0) return "Bei diesem Risiko reicht es für kein ganzes Stück. Risiko oder Depotgröße erhöhen – oder den Wert auslassen.";
  return `<b>${num(qty, whole ? 0 : 4)} Stück</b> kaufen, das sind ${price(qty * cost)} (${num(qty * cost / depot * 100, 0)} % des Depots). Fällt der Kurs auf den Stop bei ${price(a.stop)}, verlierst du ${price(qty * perUnit)}. Am Ziel bei ${price(a.target)} gewinnst du ${price(qty * (a.target - a.price) * k)}.`;
}
function openDrawer(ticker, push = true) {
  const a = BY[ticker];
  if (!a) return;
  lastFocus = document.activeElement;
  state.open = ticker;
  const metric = (l, v) => `<div><dt>${l}</dt><dd>${v}</dd></div>`;
  const pence = unit(a) < 1 ? " Kurse in Pence, Beträge in Pfund." : "";
  $("#drawer").innerHTML = `<div class="drawer-head"><div><div class="pick-name">${esc(a.name)}</div><div class="small">${esc(a.ticker)} · ${esc(DATA.markets[a.market])}${a.sector ? " · " + esc(a.sector) : ""}</div></div>
      <button type="button" class="star" style="margin-left:auto;font-size:22px" data-star="${esc(a.ticker)}" aria-pressed="${state.watch.has(a.ticker)}" aria-label="Auf die Watchlist">★</button>
      <button type="button" class="icon-btn" id="close" aria-label="Schließen">✕</button></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span class="stat">${price(a.price)}</span><span class="small">${pct(a.chg_1d)} heute</span>
      <span style="margin-left:auto">${badge(a.short_signal, "Kurzfristig: " + a.short_signal)} ${badge(a.long_signal, "Langfristig: " + a.long_signal)}</span></div>
    <div class="card"><div class="legend" style="margin-top:0"><span><i style="background:var(--s1)"></i>Kurs</span><span><i style="background:var(--s2)"></i>50-Tage-Linie</span><span><i style="background:var(--s3)"></i>200-Tage-Linie</span></div>
      <div class="chart" id="price-chart"><div class="tip"></div><div class="skeleton" style="height:240px"></div></div></div>
    <div class="card"><h3>Warum</h3>${reasons(a)}${rangeBar(a)}</div>
    ${NEWS?.items.find(n => n.ticker === a.ticker) ? `<div class="card"><h3>Nachrichtenlage · ${ago(NEWS.at)}</h3>${newsList(NEWS.items.find(n => n.ticker === a.ticker))}</div>` : ""}
    <div class="card"><h3>Handelsplan</h3><dl class="levels" style="margin-bottom:12px"><div><dt>Einstieg</dt><dd>${price(a.price)}</dd></div><div><dt>Stop (${pct(a.stop / a.price - 1)})</dt><dd>${price(a.stop)}</dd></div><div><dt>Ziel (${pct(a.target / a.price - 1)})</dt><dd>${price(a.target)}</dd></div></dl>
      <div class="calc"><label>Depotgröße<input id="c-depot" type="number" min="0" step="100" value="${state.calc.depot}"></label><label>Risiko je Position in %<input id="c-risk" type="number" min="0" max="100" step="0.1" value="${state.calc.risk}"></label></div>
      <div class="calc-out" id="c-out">${calcOutput(a)}</div><p class="small" style="margin:8px 0 0">Beträge in Handelswährung.${pence} Der Stop gilt als Schlusskurs-Stop.</p></div>
    <div class="card"><h3>Kennzahlen</h3><dl class="kv">${metric("Score kurz", num(a.short_score, 0))}${metric("Score lang", num(a.long_score, 0))}${metric("1 Woche", pct(a.mom_1w))}${metric("1 Monat", pct(a.mom_1m))}${metric("6 Monate", pct(a.mom_6m, 0))}${metric("12-1 Monate", pct(a.mom_12_1, 0))}
      ${metric("zur 200-T-Linie", pct(a.vs_sma200, 0))}${metric("zum 52-W-Hoch", pct(a.from_high, 0))}${metric("Schwankung p. a.", a.vol == null ? "–" : num(a.vol * 100, 0) + " %")}${metric("RSI 14", num(a.rsi14, 0))}
      ${a.market_cap ? metric("Börsenwert", big(a.market_cap)) : ""}${a.pe ? metric("KGV", num(a.pe, 1)) : ""}${a.dividend_yield ? metric("Dividende", num(a.dividend_yield * 100, 1) + " %") : ""}${a.earnings_in_days != null ? metric("Quartalszahlen", a.earnings_in_days === 0 ? "heute" : "in " + a.earnings_in_days + " T") : ""}</dl></div>`;
  document.body.classList.add("open");
  setModal(true);
  $("#drawer").focus();
  if (push) history.pushState(null, "", "#w/" + encodeURIComponent(ticker));
  const recalc = () => { state.calc = { depot: +$("#c-depot").value, risk: +$("#c-risk").value }; store.set("calc", state.calc); $("#c-out").innerHTML = calcOutput(a); };
  $("#c-depot").oninput = recalc; $("#c-risk").oninput = recalc;
  loadCharts().then(() => drawPrice(a)).catch(() => drawPrice(a));
}
function drawPrice(a) {
  const box = $("#price-chart");
  if (!box || state.open !== a.ticker) return;  // the user has moved on to another asset or closed the drawer
  box.querySelector(".skeleton")?.remove();
  const m = CHARTS?.[a.market], s = m?.series[a.ticker];
  const line = (key, name, color, thin) => ({ name, color, thin, pts: s ? m.dates.map((d, i) => [Date.parse(d), s[key][i]]).filter(p => p[1] != null) : [] });
  const series = [line("c", "Kurs", "var(--s1)"), line("s50", "50-Tage-Linie", "var(--s2)", true), line("s200", "200-Tage-Linie", "var(--s3)", true)];
  if (series[0].pts.length) series[0].pts.push([Date.parse(DATA.generated_at), a.price]);
  lineChart(box, series, { fmt: price, height: 260, hlines: [{ v: a.stop, label: "Stop", color: "var(--critical)" }, { v: a.target, label: "Ziel", color: "var(--good)" }] });
}
function setModal(open) {  // keep keyboard focus inside the open drawer and out of the closed one
  $("#drawer").inert = !open;
  $("main").inert = open; $(".top").inert = open;
}
function closeDrawer(push = true) {
  if (!document.body.classList.contains("open")) return;
  document.body.classList.remove("open");
  setModal(false);
  state.open = null;
  if (push) history.pushState(null, "", location.pathname + location.search);
  lastFocus?.focus?.();
}
let chartsRequest;
function loadCharts() {  // one request, shared by every caller
  chartsRequest ??= getJSON("data/charts.json").then(c => { CHARTS = c; }).catch(e => { chartsRequest = null; throw e; });
  return chartsRequest;
}
function fromHash() {
  const match = location.hash.match(/^#w\/(.+)$/);
  if (match && BY[decodeURIComponent(match[1])]) openDrawer(decodeURIComponent(match[1]), false); else closeDrawer(false);
}
function toggleStar(ticker) {
  state.watch.has(ticker) ? state.watch.delete(ticker) : state.watch.add(ticker);
  store.set("watch", [...state.watch]);
  document.querySelectorAll(`[data-star="${CSS.escape(ticker)}"]`).forEach(b => b.setAttribute("aria-pressed", state.watch.has(ticker)));
  if (state.fWatch) applyTable();
}

/* Portfolio */
function renderPositions() {
  $("#positions thead").innerHTML = state.positions.length ? `<tr><th class="l">Wert</th><th>Stück</th><th>Kaufkurs</th><th>Kurs</th><th>Gewinn / Verlust</th><th class="l">Signal</th><th class="l">Hinweis</th><th></th></tr>` : "";
  const totals = {};
  $("#positions tbody").innerHTML = state.positions.map((p, i) => {
    const a = BY[p.t];
    if (!a) return `<tr><td class="l">${esc(p.t)}</td><td colspan="6" class="l">Aktuell keine Kursdaten.</td><td><button class="btn" type="button" data-del="${i}">Entfernen</button></td></tr>`;
    const gain = a.price / p.p - 1, k = unit(a), sum = totals[currency(a)] ??= { cost: 0, value: 0 };
    sum.cost += p.q * p.p * k; sum.value += p.q * a.price * k;
    const hint = a.long_signal === "Meiden" ? "Radar rät ab – Ausstieg prüfen" : a.vs_sma200 < 0 ? "Unter der 200-Tage-Linie" : a.rsi14 > 75 ? "Überkauft – nicht nachkaufen" : "Trend intakt, Stop bei " + price(a.stop);
    return `<tr class="row" data-open="${esc(p.t)}"><td class="name"><b>${esc(a.name)}</b><br><span class="tk">${esc(p.t)}</span></td><td>${num(p.q, p.q % 1 ? 4 : 0)}</td><td>${price(p.p)}</td><td>${price(a.price)}</td>
      <td>${pct(gain)} · ${price(p.q * (a.price - p.p) * k)}</td><td class="l">${badge(a.long_signal)}</td><td class="l">${esc(hint)}</td><td><button class="btn" type="button" data-del="${i}">Entfernen</button></td></tr>`;
  }).join("");
  $("#pos-sum").textContent = state.positions.length
    ? Object.entries(totals).map(([cur, t]) => `${cur}: ${price(t.value)} Wert, ${pct(t.value / t.cost - 1)} auf den Einsatz`).join(" · ")
    : "Noch keine Position eingetragen.";
  renderXray();
}

function renderXray() {
  const held = state.positions.map(p => BY[p.t]).filter(Boolean);
  if (!held.length) { $("#xray").innerHTML = ""; return; }
  const checks = [], add = (ok, text) => checks.push(`<li class="${ok ? "pro" : "con"}">${text}</li>`), names = list => list.map(a => esc(a.name)).join(", ");
  const avoid = held.filter(a => a.long_signal === "Meiden"), below = held.filter(a => a.vs_sma200 < 0), soon = held.filter(a => a.earnings_in_days != null && a.earnings_in_days <= 7);
  const hot = held.filter(a => a.rsi14 > 75), markets = new Set(held.map(a => a.market)), sectors = held.filter(a => a.sector).reduce((m, a) => m.set(a.sector, (m.get(a.sector) || 0) + 1), new Map());
  const top = [...sectors].sort((x, y) => y[1] - x[1])[0];
  add(!avoid.length, avoid.length ? `Das Radar rät ab bei: ${names(avoid)}. Ausstieg prüfen.` : "Keine Position mit dem Signal „Meiden“.");
  add(!below.length, below.length ? `Unter der 200-Tage-Linie: ${names(below)}.` : "Alle Positionen liegen über ihrer 200-Tage-Linie.");
  add(held.length >= 5, held.length >= 5 ? `${held.length} Positionen – ordentlich gestreut.` : `Nur ${held.length} ${held.length === 1 ? "Position" : "Positionen"}. Ein einzelner Fehlgriff trifft das Depot hart.`);
  add(markets.size > 1, markets.size > 1 ? `Verteilt auf ${markets.size} Märkte.` : `Alles in einem Markt (${esc(DATA.markets[[...markets][0]])}).`);
  if (top && held.length >= 3) add(top[1] / held.length <= 0.5, top[1] / held.length <= 0.5 ? "Kein Sektor stellt mehr als die Hälfte der Positionen." : `Klumpen: ${top[1]} von ${held.length} Positionen im Sektor ${esc(top[0])}.`);
  if (hot.length) add(false, `Überkauft, nicht nachkaufen: ${names(hot)}.`);
  if (soon.length) add(false, `Quartalszahlen in den nächsten 7 Tagen: ${names(soon)}.`);
  $("#xray").innerHTML = `<h3>Depot-Check</h3><ul class="why">${checks.join("")}</ul>`;
}

/* Compare chart */
const cmpList = () => state.compare || [DATA.picks.overall.long[0], DATA.picks.overall.short[0], "EUNL.DE"].filter((t, i, all) => t && BY[t] && all.indexOf(t) === i);
function renderCompare() {
  const list = cmpList().filter(t => BY[t]).slice(0, 4);
  $("#cmp-chips").innerHTML = list.map((t, i) => `<button type="button" class="chip" data-cmp-del="${esc(t)}" aria-label="${esc(BY[t].name)} entfernen"><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:var(${SERIES[i]});margin-right:8px"></i>${esc(BY[t].name)}<span class="x">✕</span></button>`).join("");
  const series = list.map((t, i) => {
    const m = CHARTS?.[BY[t].market], c = m?.series[t]?.c || [], first = c.find(v => v != null);
    return { name: BY[t].name, color: `var(${SERIES[i]})`, pts: m && first ? m.dates.map((d, k) => [Date.parse(d), c[k] == null ? null : c[k] / first * 100]).filter(q => q[1] != null) : [] };
  });
  $("#cmp-legend").innerHTML = series.map(x => `<span><i style="background:${x.color}"></i>${esc(x.name)}</span>`).join("");
  lineChart($("#cmp-chart"), series, { fmt: v => num(v, 0), height: 340, direct: true });
}
async function loadCompare() {
  try { await loadCharts(); } catch { /* the chart shows its empty state */ }
  renderCompare();
  new ResizeObserver(() => renderCompare()).observe($("#cmp-chart"));
}

/* Open signals of the live record */
async function loadSignals() {
  let track;
  try { track = await getJSON("data/track.json", { cache: "no-cache" }); } catch { return; }
  const open = ["short", "long"].flatMap(h => Object.entries(track.open[h]).map(([t, e]) => ({ t, h, ...e }))).sort((a, b) => b.return - a.return);
  const rows = [...open.slice(0, 8), ...(open.length > 16 ? open.slice(-8) : open.slice(8))];
  const line = (e, done) => `<tr class="row" data-open="${esc(e.t || e.ticker)}"><td class="name"><b>${esc(e.name)}</b><br><span class="tk">${esc(e.t || e.ticker)} · ${HORIZON[e.h || e.horizon]}</span></td><td class="l">${done ? "beendet " + when(e.until) : "läuft"}</td><td>${when(e.since)}</td><td>${price(e.entry)}</td><td>${price(done ? e.exit : e.price)}</td><td class="${e.return >= 0 ? "up" : "down"}">${pct(e.return)}</td></tr>`;
  $("#open-signals thead").innerHTML = `<tr><th class="l">Wert</th><th class="l">Status</th><th>Signal seit</th><th>Einstieg</th><th>Kurs</th><th>Ergebnis</th></tr>`;
  $("#open-signals tbody").innerHTML = track.closed.slice(0, 8).map(e => line(e, true)).join("") + rows.map(e => line(e, false)).join("")
    || `<tr><td class="l">Noch keine Signale mitgeschrieben.</td></tr>`;
}

/* Backtests (loaded when scrolled into view) */
const GROUPS = [["ETF-Strategien", ["bh_spy", "etf_rotation", "dual_momentum", "trend_spy", "rsi2_spy"]], ["Einzelaktien", ["bh_spy", "stock_momentum_us", "stock_momentum_eu"]], ["Krypto", ["bh_btc", "trend_btc", "crypto_momentum"]]];
function renderBacktestChart() {
  const list = GROUPS[state.group][1].map(k => BACKTESTS.find(b => b.key === k)).filter(Boolean);
  const start = list.map(b => b.curve.dates[0]).sort().pop();
  const series = list.map((b, i) => {
    const from = b.curve.dates.findIndex(d => d >= start), base = b.curve.equity[from];
    return { name: b.name, color: `var(${SERIES[i]})`, pts: b.curve.dates.slice(from).map((d, j) => [Date.parse(d), b.curve.equity[from + j] / base]) };
  });
  $("#bt-legend").innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  lineChart($("#bt-chart"), series, { log: true, fmt: v => num(v, v < 1 ? 2 : v < 10 ? 2 : 0) + " €", height: 340, direct: true });
}
async function loadBacktests() {
  try { BACKTESTS = await getJSON("data/backtests.json"); } catch { $("#bt-note").textContent = "Die Backtests konnten nicht geladen werden."; return; }
  seg($("#bt-group"), GROUPS.map((g, i) => [i, g[0]]), 0, v => { state.group = +v; renderBacktestChart(); });
  renderBacktestChart();
  new ResizeObserver(() => renderBacktestChart()).observe($("#bt-chart"));
  $("#bt-table thead").innerHTML = `<tr><th class="l">Strategie</th><th>Rendite p. a.<br>gesamt</th><th>Rendite p. a.<br>bis 2020</th><th>Rendite p. a.<br>seit 2021</th><th>Sharpe<br>seit 2021</th><th>Größter Rückgang<br>gesamt</th><th>Rendite je Rückgang<br>seit 2021</th></tr>`;
  $("#bt-table tbody").innerHTML = BACKTESTS.map(b => {
    const f = b.full || {}, i = b.in_sample || {}, o = b.out_of_sample || {};
    const tags = (b.key === DATA.core_strategy ? `<span class="tag">Kernstrategie</span>` : "") + (b.survivorship_bias ? `<span class="tag">geschönt*</span>` : "");
    return `<tr><td class="name"><b>${esc(b.name)}</b>${tags}<br><span class="tk">${esc(b.description)}</span></td><td>${pct(f.cagr)}</td><td>${pct(i.cagr)}</td><td>${pct(o.cagr)}</td><td>${num(o.sharpe)}</td><td>${pct(f.max_drawdown, 0)}</td><td>${num(o.calmar)}</td></tr>`;
  }).join("");
  $("#bt-note").innerHTML = (DATA.core ? `<b>Kernstrategie:</b> ${esc(DATA.core.name)} hatte seit 2021 das beste Verhältnis aus Rendite und größtem Rückgang unter den unverzerrten Tests. ` : "")
    + `„Seit 2021“ ist der Zeitraum, den die Regeln nie gesehen haben, und damit der ehrlichste Maßstab. *Geschönt: Diese Tests nutzen die heutige Auswahl an Aktien und Coins, also die Gewinner von heute (Survivorship Bias). Die echte Rendite wäre niedriger gewesen.`;
}
async function loadHistory() {
  let hist = [];
  try { hist = await getJSON("data/history.json", { cache: "no-cache" }); } catch { /* optional */ }
  const cell = h => h ? `<b>${esc(h.name)}</b> <span class="tk">${esc(h.ticker)} · ${price(h.price)} · Score ${num(h.score, 0)}</span>` : "kein Signal";
  $("#history thead").innerHTML = `<tr><th class="l">Zeit</th><th class="l">Kurzfristig</th><th class="l">Langfristig</th><th class="l">Marktlage</th></tr>`;
  $("#history tbody").innerHTML = hist.slice(-24).reverse().map(h => `<tr><td class="l">${when(h.at)}</td><td class="l">${cell(h.short)}</td><td class="l">${cell(h.long)}</td><td class="l">${esc(REGIME[h.regime] || h.regime)}</td></tr>`).join("");
}
function lazy(el, load) {
  const io = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) { io.disconnect(); load(); } }, { rootMargin: "600px" });
  io.observe(el);
}

/* Search box in the top bar */
function setupSearch() {
  const input = $("#q"), box = $("#suggest");
  let hits = [], active = 0;
  const show = () => {
    const q = input.value.trim().toLowerCase();
    hits = q ? DATA.assets.filter(a => (a.name + " " + a.ticker).toLowerCase().includes(q)).slice(0, 8) : [];
    active = 0; box.hidden = !hits.length;
    box.innerHTML = hits.map((a, i) => `<button type="button" data-open="${esc(a.ticker)}" class="${i ? "" : "on"}"><b>${esc(a.name)}</b><span class="tk">${esc(a.ticker)}</span><span style="margin-left:auto">${badge(a.long_signal)}</span></button>`).join("");
  };
  input.oninput = show;
  input.onkeydown = e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); if (!hits.length) return;
      active = (active + (e.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
      [...box.children].forEach((b, i) => b.classList.toggle("on", i === active));
    } else if (e.key === "Enter" && hits[active]) { openDrawer(hits[active].ticker); input.value = ""; show(); }
    else if (e.key === "Escape") { input.value = ""; show(); input.blur(); }
  };
  input.onblur = () => setTimeout(() => { box.hidden = true; }, 150);
  input.onfocus = show;
}

function wire() {
  document.addEventListener("click", e => {
    const star = e.target.closest("[data-star]");
    if (star) { e.stopPropagation(); toggleStar(star.dataset.star); return; }
    const cmpDel = e.target.closest("[data-cmp-del]");
    if (cmpDel) { state.compare = cmpList().filter(t => t !== cmpDel.dataset.cmpDel); store.set("compare", state.compare); renderCompare(); return; }
    const preset = e.target.closest("[data-preset]");
    if (preset) { state.preset = state.preset === preset.dataset.preset ? null : preset.dataset.preset; document.querySelectorAll("[data-preset]").forEach(b => b.setAttribute("aria-pressed", b.dataset.preset === state.preset)); applyTable(); return; }
    const del = e.target.closest("[data-del]");
    if (del) { state.positions.splice(+del.dataset.del, 1); store.set("positions", state.positions); renderPositions(); return; }
    if (e.target.closest("#close") || e.target.id === "scrim") { closeDrawer(); return; }
    const open = e.target.closest("[data-open]");
    if (open) { openDrawer(open.dataset.open); if (open.closest("#suggest")) { $("#q").value = ""; $("#suggest").hidden = true; } }
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeDrawer();
    else if (e.key === "/" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); $("#q").focus(); }
    else if (e.key === "Enter" && e.target.matches?.("tr.row")) openDrawer(e.target.dataset.open);
  });
  window.addEventListener("popstate", fromHash);
  $("#theme").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch { /* private mode */ }
  };
  $("#assets thead").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    state.dir = state.sort === b.dataset.k ? -state.dir : (b.dataset.k === "name" ? 1 : -1); state.sort = b.dataset.k; applyTable();
  };
  $("#f-watch").onclick = e => { state.fWatch = !state.fWatch; e.currentTarget.setAttribute("aria-pressed", state.fWatch); e.currentTarget.textContent = (state.fWatch ? "★" : "☆") + " Watchlist"; applyTable(); };
  $("#csv").onclick = exportCSV;
  const lookup = raw => BY[raw] ? raw : DATA.assets.find(a => a.ticker.toLowerCase() === raw.toLowerCase() || a.name.toLowerCase() === raw.toLowerCase())?.ticker;
  $("#cmp-form").onsubmit = e => {
    e.preventDefault();
    const t = lookup($("#cmp-ticker").value.trim()), list = cmpList();
    if (!t || list.includes(t)) return;
    state.compare = [...list, t].slice(-4); store.set("compare", state.compare); e.target.reset(); renderCompare();
  };
  addEventListener("scroll", () => $("#nav").classList.toggle("stuck", scrollY > 8), { passive: true });
  $("#pos-form").onsubmit = e => {
    e.preventDefault();
    const raw = $("#pos-ticker").value.trim(), t = BY[raw] ? raw : DATA.assets.find(a => a.ticker.toLowerCase() === raw.toLowerCase() || a.name.toLowerCase() === raw.toLowerCase())?.ticker;
    const q = +$("#pos-qty").value, p = +$("#pos-price").value;
    if (!t) { $("#pos-sum").textContent = "Diesen Wert kennt das Radar nicht. Bitte einen Ticker aus der Liste wählen."; return; }
    if (!(q > 0) || !(p > 0)) return;
    state.positions.push({ t, q, p }); store.set("positions", state.positions); e.target.reset(); renderPositions();
  };
}

async function init() {
  wire();
  try { DATA = await getJSON("data/latest.json", { cache: "no-cache" }); }
  catch (e) {
    $("#picks").hidden = true; $("#error").hidden = false; $("#status").textContent = "Keine Daten";
    $("#error").textContent = "Die Daten konnten nicht geladen werden (" + e.message + "). Bitte später erneut versuchen.";
    return;
  }
  BY = Object.fromEntries(DATA.assets.map(a => [a.ticker, a]));
  $("#app").hidden = false;
  const markets = Object.entries(DATA.markets);
  renderOverview();
  seg($("#heat-period"), Object.entries(HEAT).map(([k, v]) => [k, v[0]]), state.heat, v => { state.heat = v; renderHeat(); });
  renderHeat();
  $("#presets").innerHTML = Object.entries(PRESETS).map(([k, v]) => `<button type="button" class="chip" data-preset="${k}" aria-pressed="false">${v[0]}</button>`).join("");
  seg($("#f-market"), [["all", "Alle Märkte"], ...markets], "all", v => { state.fMarket = v; applyTable(); });
  seg($("#f-signal"), [["all", "Alle Signale"], ["Kaufen", "Kaufen"], ["Halten", "Halten"], ["Beobachten", "Beobachten"], ["Meiden", "Meiden"]], "all", v => { state.fSignal = v; applyTable(); });
  buildTable();
  $("#tickers").innerHTML = DATA.assets.map(a => `<option value="${esc(a.ticker)}">${esc(a.name)}</option>`).join("");
  renderPositions(); setupSearch(); renderChanges();
  renderNews().finally(fromHash);
  if (/^#[a-z]+$/.test(location.hash)) $(location.hash)?.scrollIntoView();  // the section did not exist yet when the browser tried
  lazy($("#backtests"), loadBacktests); lazy($("#verlauf"), loadHistory); lazy($("#vergleich"), loadCompare); lazy($("#signale"), loadSignals);
  const reveal = new IntersectionObserver(entries => entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); } }), { rootMargin: "0px 0px -8% 0px" });
  document.querySelectorAll(".reveal").forEach(el => reveal.observe(el));
  (window.requestIdleCallback || setTimeout)(() => loadCharts().catch(() => {}));
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
}
init();
