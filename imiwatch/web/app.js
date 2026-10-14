/* imiwatch の画面。Python 側の API（imiwatch/api.py）を呼んで動く。
   pywebview では window.pywebview.api、ブラウザでは POST /api/<名前> を使う。 */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
};
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const pad = (n) => String(n).padStart(2, "0");
const toMs = (t) => new Date(t).getTime();
const fmtTime = (t, withDate = true) => {
  if (!t) return "—";
  const d = new Date(t);
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return withDate ? `${d.getMonth() + 1}/${d.getDate()} ${time}` : time;
};
const fmtInterval = (s) => (s % 3600 === 0 ? `${s / 3600}時間` : s % 60 === 0 ? `${s / 60}分` : `${s}秒`);
const hitOf = (v, op, th) => v !== null && v !== undefined && (op === "<=" ? v <= th : v >= th);
const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

/* ================================================================== API */
const TOKEN = new URLSearchParams(location.search).get("token");
const api = {
  ready: new Promise((resolve) => {
    if (TOKEN) return resolve("http");
    if (window.pywebview && window.pywebview.api) return resolve("webview");
    window.addEventListener("pywebviewready", () => resolve("webview"), { once: true });
  }),
  async call(name, ...args) {
    await this.ready;
    let res;
    if (TOKEN) {
      const r = await fetch(`/api/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Imiwatch-Token": TOKEN },
        body: JSON.stringify({ args }),
      });
      res = await r.json();
    } else {
      res = await window.pywebview.api[name](...args);
    }
    if (!res || res.ok === false) throw new Error((res && res.error) || "応答がありません");
    return res;
  },
};

/* ================================================================== 状態 */
const S = {
  boot: null,
  monitors: [],          // 一覧の要約
  selected: null,        // 選んでいる見張りの ID
  mon: null,             // 選んでいる見張りの全体（plan を含む）
  rows: [],              // 選んでいる見張りの記録
  answers: {},           // 最新の判定の答え（判断保留の表示に使う）
  chartRows: [],         // グラフの指標の点に対応する記録
  pinned: null,          // グラフで選んだ点
  range: "all",
  seq: 0,
  shotCache: new Map(),
  ed: null,              // 作成・編集中の内容
};
const store = {
  get(k, d) { try { const v = localStorage.getItem("imiwatch." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("imiwatch." + k, JSON.stringify(v)); } catch { /* 保存できなくても動く */ } },
};

/* ================================================================== 知らせ */
function toast(title, text = "", kind = "") {
  const t = h("div", { class: `toast ${kind}` }, h("b", { text: title }), text ? h("span", { text }) : null);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "notify" ? 10000 : 5000);
  t.addEventListener("click", () => t.remove());
}
const fail = (e) => toast("うまくいきませんでした", e.message || String(e), "error");

async function busy(btn, fn) {
  btn.classList.add("busy");
  btn.disabled = true;
  try { return await fn(); } catch (e) { fail(e); return null; } finally { btn.classList.remove("busy"); btn.disabled = false; }
}

function ask({ title, text = "", input = null, ok = "OK", danger = false }) {
  const dlg = $("#dlg-prompt");
  $("#p-title").textContent = title;
  $("#p-text").textContent = text;
  const inp = $("#p-input");
  inp.hidden = input === null;
  inp.value = input || "";
  const okBtn = $("#p-ok");
  okBtn.textContent = ok;
  okBtn.className = `btn ${danger ? "danger" : "primary"}`;
  dlg.returnValue = "";
  dlg.showModal();
  if (input !== null) setTimeout(() => { inp.focus(); inp.select(); }, 30);
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok" ? (input !== null ? inp.value : true) : null), { once: true });
  });
}

/* ================================================================== 画面の切り替え */
function show(view) {
  for (const v of ["empty", "detail", "editor"]) $(`#view-${v}`).hidden = v !== view;
}

/* ================================================================== 一覧 */
function sparkSvg(values, th) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "watch-spark");
  svg.setAttribute("viewBox", "0 0 100 20");
  svg.setAttribute("preserveAspectRatio", "none");
  const vals = (values || []).filter((v) => v !== null);
  if (th !== undefined && th !== null) {
    const ln = document.createElementNS(svg.namespaceURI, "line");
    const y = 19 - th * 18;
    Object.entries({ x1: 0, x2: 100, y1: y, y2: y }).forEach(([k, v]) => ln.setAttribute(k, v));
    svg.append(ln);
  }
  if (vals.length >= 2) {
    const step = 100 / (vals.length - 1);
    const d = vals.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(19 - v * 18).toFixed(1)}`).join("");
    const p = document.createElementNS(svg.namespaceURI, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

function renderList() {
  const ul = $("#watch-list");
  ul.replaceChildren();
  for (const m of S.monitors) {
    const hit = m.notify_enabled && hitOf(m.metric, m.op, m.threshold);
    const li = h("li", {
      class: `watch${m.id === S.selected ? " on" : ""}${m.running ? " running" : ""}${m.error && m.running ? " fault" : ""}`,
      title: m.request || m.name,
      onclick: () => select(m.id),
    },
      h("div", { class: "watch-thumb", style: m.thumb ? `background-image:url(${m.thumb})` : "" }),
      h("div", { class: "watch-name", text: m.name }),
      h("div", { class: `watch-value${hit ? " hit" : ""}`, text: fmt(m.metric) }),
      sparkSvg(m.spark, m.notify_enabled ? m.threshold : null),
    );
    ul.append(li);
  }
  $("#side-empty").hidden = S.monitors.length > 0;
}

function upsertSummary(sum) {
  const i = S.monitors.findIndex((m) => m.id === sum.id);
  if (i >= 0) {
    if (!sum.thumb && S.monitors[i].thumb) sum.thumb = S.monitors[i].thumb;
    S.monitors[i] = sum;
  } else S.monitors.push(sum);
}

/* ================================================================== 詳細 */
async function select(id) {
  if (!id) { S.selected = null; show("empty"); renderList(); return; }
  S.selected = id;
  store.set("selected", id);
  S.pinned = null;
  renderList();
  show("detail");
  try {
    const [m, se] = await Promise.all([api.call("get_monitor", id), api.call("get_series", id)]);
    if (S.selected !== id) return;
    S.mon = m.monitor;
    S.rows = se.rows;
    S.answers = se.answers || {};
    upsertSummary({ ...m.summary, thumb: (S.monitors.find((x) => x.id === id) || {}).thumb });
    $("#plan-text").textContent = `あなたの依頼\n${S.mon.request || "（なし）"}\n\n${m.text}`;
    renderHeader();
    renderChart(true);
    renderObserve();
    renderLog();
  } catch (e) { fail(e); }
}

function current() { return S.monitors.find((m) => m.id === S.selected); }

function renderHeader() {
  const m = current();
  if (!m || !S.mon) return;
  $("#d-name").textContent = m.name;
  const st = $("#d-state");
  if (m.running && m.error) { st.className = "state fault"; st.textContent = "エラー"; st.title = m.error; }
  else if (m.running) { st.className = "state live"; st.textContent = `${fmtInterval(m.interval_seconds)}ごとに確認中`; st.title = ""; }
  else { st.className = "state"; st.textContent = "停止中"; st.title = ""; }
  $("#d-request").textContent = m.request ? `「${m.request}」` : "";
  const tg = $("#d-toggle");
  tg.textContent = m.running ? "停止" : "開始";
  tg.className = m.running ? "btn" : "btn primary";
  renderReadout();
}

function renderReadout() {
  const m = current();
  if (!m) return;
  const row = S.pinned;
  const v = row ? row.m : m.metric;
  const hit = m.notify_enabled && hitOf(v, m.op, m.threshold);
  const el = $("#r-value");
  el.textContent = fmt(v);
  el.classList.toggle("hit", !!hit);
  $("#r-name").textContent = m.metric_name;
  const sub = $("#r-sub");
  sub.replaceChildren();
  if (row) sub.append(`${fmtTime(row.t)} の値`);
  else if (m.notify_enabled) sub.append("通知 ", h("b", { text: `${m.op === "<=" ? "≤" : "≥"} ${fmt(m.threshold)}` }), ` が ${m.consecutive} 回続いたら`);
  else sub.append("通知はオフ（記録だけ）");
}

/* ----------------------------------------------------------------- グラフ */
let chart = null;
const PART_COLORS = ["#8E97B5", "#B39A6B", "#6FA39A", "#AE86A3", "#93A27F", "#9C8C80", "#7D9BB8"];

function ensureChart() {
  if (chart) return chart;
  chart = echarts.init($("#chart"), null, { renderer: "canvas" });
  new ResizeObserver(() => chart && chart.resize()).observe($("#chart"));
  // ドラッグ拡大のモードでは ECharts のクリックが届かないので、画面の要素で直接受け取る。
  // 押した位置からほとんど動かずに離したらクリックとみなす。
  const el = $("#chart");
  let down = null;
  el.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY, Date.now()]), true);
  el.addEventListener("pointerup", (e) => {
    if (!down) return;
    const [dx, dy, t] = down;
    down = null;
    if (Math.hypot(e.clientX - dx, e.clientY - dy) > 4 || Date.now() - t > 600) return;
    const rect = el.getBoundingClientRect();
    const pt = [e.clientX - rect.left, e.clientY - rect.top];
    if (!chart.containPixel("grid", pt) || !S.chartRows.length) return;
    const x = chart.convertFromPixel({ gridIndex: 0 }, pt)[0];
    let best = null, bd = Infinity;
    for (const r of S.chartRows) { const d = Math.abs(toMs(r.t) - x); if (d < bd) { bd = d; best = r; } }
    if (best) pin(best);
  }, true);
  el.addEventListener("dblclick", () => applyRange(S.range), true);
  return chart;
}

function chartSeries() {
  const m = current();
  const plan = S.mon.plan;
  const valid = S.rows.filter((r) => (r.s === "ok" || r.s === "reused") && r.m !== null && r.m !== undefined);
  S.chartRows = valid;
  const weights = plan.metric.weights || {};
  const qids = Object.keys(plan.questions);
  const series = qids.map((qid, i) => ({
    id: `q:${qid}`, name: clip(plan.questions[qid].instructions || qid, 18), type: "line",
    data: valid.filter((r) => r.v && r.v[qid] !== undefined).map((r) => [toMs(r.t), r.v[qid]]),
    showSymbol: false, symbol: "circle", symbolSize: 4, z: 2,
    lineStyle: { width: 1.2, color: PART_COLORS[i % PART_COLORS.length], type: weights[qid] ? "solid" : "dotted" },
    itemStyle: { color: PART_COLORS[i % PART_COLORS.length] },
    emphasis: { disabled: true }, silent: true,
  }));
  const th = m.threshold;
  series.push({
    id: "metric", name: m.metric_name, type: "line", z: 5,
    data: valid.map((r) => [toMs(r.t), r.m]),
    showSymbol: valid.length < 60, symbol: "circle", symbolSize: 5,
    lineStyle: { width: 2.6, color: "#2747D8", cap: "round", join: "round" },
    itemStyle: { color: "#2747D8", borderColor: "#fff", borderWidth: 1 },
    emphasis: { scale: 1.6, itemStyle: { borderWidth: 2 } },
    markLine: m.notify_enabled ? {
      silent: true, symbol: "none",
      lineStyle: { color: "#E89B12", type: [5, 4], width: 1.4 },
      label: { formatter: `通知 ${fmt(th)}`, position: "insideEndTop", color: "#B37400", fontSize: 11, fontFamily: "Bahnschrift, sans-serif" },
      data: [{ yAxis: th }],
    } : undefined,
  });
  series.push({
    id: "notified", name: "通知した点", type: "scatter", z: 6,
    data: valid.filter((r) => r.n).map((r) => [toMs(r.t), r.m]),
    symbolSize: 11, itemStyle: { color: "#E89B12", borderColor: "#fff", borderWidth: 2 }, silent: true,
  });
  series.push({
    id: "errors", name: "エラー", type: "scatter", z: 4,
    data: S.rows.filter((r) => r.s === "error").map((r) => [toMs(r.t), 0.015]),
    symbol: "rect", symbolSize: [3, 9], itemStyle: { color: "#CF3F3F" }, silent: true,
  });
  return series;
}

function renderChart(full = false) {
  const c = ensureChart();
  if (!S.mon) return;
  const series = chartSeries();
  const legendData = series.filter((s) => s.id.startsWith("q:") || s.id === "metric").map((s) => s.name);
  if (!full) {
    c.setOption({ series, legend: { data: legendData } }, { replaceMerge: ["series"] });
    return;
  }
  const hiddenParts = store.get("hideParts", false);
  const selected = {};
  if (hiddenParts) series.filter((s) => s.id.startsWith("q:")).forEach((s) => (selected[s.name] = false));
  c.setOption({
    animation: false,
    textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { left: 40, right: 22, top: 40, bottom: 58 },
    legend: { type: "scroll", data: legendData, selected, top: 0, left: 4, right: 4, itemWidth: 16, itemHeight: 3, icon: "rect", itemGap: 14,
      pageIconColor: "#5E6878", pageIconInactiveColor: "#CBD1DB", pageTextStyle: { color: "#8A93A3", fontSize: 10 },
      textStyle: { color: "#5E6878", fontSize: 11 }, inactiveColor: "#C3C9D3" },
    xAxis: { type: "time", axisLine: { lineStyle: { color: "#CBD1DB" } }, axisTick: { show: false },
      axisLabel: { color: "#8A93A3", fontSize: 11, hideOverlap: true, fontFamily: "Bahnschrift, sans-serif" },
      splitLine: { show: false } },
    yAxis: { type: "value", min: 0, max: 1, interval: 0.25, axisLabel: { color: "#8A93A3", fontSize: 11, formatter: (v) => v.toFixed(2), fontFamily: "Bahnschrift, sans-serif" },
      splitLine: { lineStyle: { color: "#EEF0F4" } } },
    tooltip: {
      trigger: "axis", confine: true, transitionDuration: 0, backgroundColor: "#fff", borderColor: "#DFE3EA",
      padding: [10, 12], extraCssText: "box-shadow:0 12px 32px rgba(20,27,38,.16);border-radius:10px;",
      axisPointer: { type: "line", lineStyle: { color: "#2747D8", width: 1, opacity: .35 } },
      formatter: tooltipFormatter,
    },
    toolbox: { left: -200, itemSize: 1, feature: { dataZoom: { yAxisIndex: "none", filterMode: "none" } } },
    dataZoom: [
      { type: "inside", xAxisIndex: 0, filterMode: "none", zoomOnMouseWheel: true, moveOnMouseWheel: "shift", moveOnMouseMove: false },
      { type: "slider", xAxisIndex: 0, filterMode: "none", height: 20, bottom: 8, borderColor: "#DFE3EA", backgroundColor: "#F6F7F9",
        fillerColor: "rgba(39,71,216,.08)", dataBackground: { lineStyle: { color: "#9AA6D8" }, areaStyle: { color: "rgba(39,71,216,.06)" } },
        selectedDataBackground: { lineStyle: { color: "#2747D8" }, areaStyle: { color: "rgba(39,71,216,.12)" } },
        handleStyle: { color: "#fff", borderColor: "#9AA6D8" }, moveHandleSize: 4, labelFormatter: (v) => fmtTime(v),
        textStyle: { color: "#8A93A3", fontSize: 10 } },
    ],
    series,
  }, { notMerge: true });
  c.off("legendselectchanged");
  c.on("legendselectchanged", (e) => {
    const parts = series.filter((s) => s.id.startsWith("q:")).map((s) => s.name);
    store.set("hideParts", parts.every((n) => e.selected[n] === false));
  });
  // ドラッグで範囲を選んで拡大できるようにする（ツールボックスのボタンは見せない）
  c.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: true });
  applyRange(S.range);
}

function applyRange(range) {
  S.range = range;
  store.set("range", range);
  $$("#range-seg button").forEach((b) => b.classList.toggle("on", b.dataset.range === range));
  if (!chart) return;
  if (range === "all") { chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 }); return; }
  const now = Date.now();
  const start = range === "1h" ? now - 3600e3 : range === "7d" ? now - 7 * 86400e3 : new Date(new Date().setHours(0, 0, 0, 0)).getTime();
  chart.dispatchAction({ type: "dataZoom", startValue: start, endValue: now });
}

function tooltipFormatter(params, ticket, callback) {
  const p = (Array.isArray(params) ? params : [params]).find((x) => x.seriesId === "metric");
  if (!p) return "";
  const row = S.chartRows[p.dataIndex];
  if (!row) return "";
  const m = current();
  const hit = m.notify_enabled && hitOf(row.m, m.op, m.threshold);
  const parts = Object.entries(S.mon.plan.questions).map(([qid, q]) =>
    `<div class="tt-row"><span>${esc(clip(q.instructions || qid, 22))}</span><span>${row.v && row.v[qid] !== undefined ? fmt(row.v[qid]) : "—"}</span></div>`).join("");
  const build = (img) => `<div class="tt"><div class="tt-time">${fmtTime(row.t)}${row.s === "reused" ? "（変化なし）" : ""}</div>
    <div class="tt-value${hit ? " hit" : ""}">${fmt(row.m)}</div>${parts}
    ${row.n ? '<div class="tt-row"><span style="color:#B37400;font-weight:700">通知しました</span><span></span></div>' : ""}
    <div class="tt-img">${img ? `<img src="${img}">` : row.shot ? "読み込み中…" : "画像なし"}</div></div>`;
  const key = `${S.selected}/${row.shot}`;
  if (!row.shot) return build(null);
  if (S.shotCache.has(key)) return build(S.shotCache.get(key));
  api.call("shot", S.selected, row.shot).then((r) => {
    S.shotCache.set(key, r.image);
    callback(ticket, build(r.image));
  }).catch(() => callback(ticket, build(null)));
  return build(null);
}
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* ----------------------------------------------------------------- 観測窓 */
async function setObserveImage(src) {
  const box = $("#o-img");
  box.replaceChildren(src ? h("img", { src, alt: "撮影した画像" }) : h("span", { text: "画像がありません" }));
}

async function renderObserve() {
  const pinned = S.pinned;
  const obs = $(".observe");
  obs.classList.toggle("pinned", !!pinned);
  $("#o-label").textContent = pinned ? "選んだ時点の画像" : "最新の画像";
  $("#o-back").hidden = !pinned;
  const row = pinned || [...S.rows].reverse().find((r) => r.s !== "error") || null;
  $("#o-time").textContent = row ? `${fmtTime(row.t)}${row.s === "reused" ? "（変化なし）" : ""}` : "";
  renderFacets(row, pinned ? {} : S.answers);
  if (!row) { $("#o-img").replaceChildren(h("span", { text: "まだ撮影していません" })); return; }
  const key = `${S.selected}/${row.shot}`;
  try {
    if (row.shot) {
      if (!S.shotCache.has(key)) S.shotCache.set(key, (await api.call("shot", S.selected, row.shot)).image);
      setObserveImage(S.shotCache.get(key));
    } else setObserveImage((await api.call("latest_image", S.selected)).image);
  } catch { setObserveImage(null); }
}

function renderFacets(row, answers) {
  const ul = $("#o-facets");
  ul.replaceChildren();
  if (!S.mon) return;
  const plan = S.mon.plan;
  const weights = plan.metric.weights || {};
  for (const [qid, q] of Object.entries(plan.questions)) {
    const a = (answers || {})[qid] || {};
    const v = row && row.v ? row.v[qid] : undefined;
    let txt = fmt(v);
    if (a.abstained) txt = "判断保留";
    else if (q.type === "score" && v !== undefined) txt = `${fmt(v * (q.criteria.length - 1), 1)} / ${q.criteria.length - 1}`;
    else if (q.type === "choice" && !q.target && a.choice) txt = a.choice;
    const li = h("li", { class: `facet${weights[qid] ? "" : " rec"}${a.abstained ? " held" : ""}`, title: `${qid}: ${q.instructions}` },
      h("span", { class: "facet-name", text: q.instructions || qid }),
      h("span", { class: "facet-val", text: txt }),
      h("span", { class: "facet-bar" }, h("i", { style: `width:${v !== undefined && !a.abstained ? Math.round(v * 100) : 0}%` })),
    );
    ul.append(li);
  }
}

function pin(row) {
  S.pinned = row;
  renderReadout();
  renderObserve();
}

/* ----------------------------------------------------------------- 記録 */
function logRow(r) {
  const st = r.s === "error" ? h("span", { class: "tag error", text: "エラー" })
    : r.s === "reused" ? h("span", { class: "tag", text: "変化なし" }) : h("span", { class: "tag ok", text: "判定" });
  return h("tr", { class: `${r.n ? "notified" : ""} ${r.s === "error" ? "error" : ""}` },
    h("td", { class: "time", text: fmtTime(r.t) }),
    h("td", {}, st, r.n ? h("span", { class: "tag notify", style: "margin-left:4px", text: "通知" }) : null),
    h("td", { class: "num", text: fmt(r.m) }),
    h("td", { text: r.p || "" }),
    h("td", { class: "num", text: r.l ? `${r.l} ms` : "" }),
    h("td", { class: "note", text: r.e || "" }),
  );
}
function renderLog() {
  const tb = $("#log-body");
  tb.replaceChildren(...S.rows.slice(-300).reverse().map(logRow));
  if (!S.rows.length) tb.append(h("tr", {}, h("td", { colspan: 6, class: "note", text: "まだ記録がありません。「今すぐ判定」で最初の1点を記録できます。" })));
}

/* ================================================================== 受け取り（ポーリング） */
async function pollLoop() {
  try {
    const r = await api.call("poll", S.seq);
    if (r.reset) { await reloadAll(); }
    for (const ev of r.events) handleEvent(ev);
    S.seq = r.seq;
  } catch { /* 一時的な失敗は次回に任せる */ }
  setTimeout(pollLoop, document.hidden ? 3000 : 900);
}

function handleEvent(ev) {
  if (ev.type === "result") {
    upsertSummary(ev.summary);
    renderList();
    if (ev.monitor_id === S.selected && S.mon) {
      S.rows.push(ev.row);
      if (ev.row.s !== "error") S.answers = ev.answers || {};
      renderHeader();
      renderChart(false);
      if (!S.pinned) renderObserve();
      const tb = $("#log-body");
      if (tb.firstChild && tb.firstChild.querySelector("td[colspan]")) tb.replaceChildren();
      tb.prepend(logRow(ev.row));
      if (ev.row.s === "error") toast(`${ev.summary.name}: 判定できませんでした`, ev.row.e, "error");
    }
  } else if (ev.type === "notify") {
    if (!ev.native) toast(ev.title, ev.message, "notify");
  } else if (ev.type === "deleted") {
    S.monitors = S.monitors.filter((m) => m.id !== ev.monitor_id);
    renderList();
  }
}

async function reloadAll() {
  const r = await api.call("list_monitors");
  S.monitors = r.monitors;
  renderList();
  if (S.selected && S.monitors.some((m) => m.id === S.selected)) select(S.selected);
  else select(S.monitors[0] && S.monitors[0].id);
}

/* ================================================================== 詳細の操作 */
function bindDetail() {
  $("#d-toggle").onclick = (e) => busy(e.currentTarget, async () => {
    const m = current();
    const r = await api.call(m.running ? "stop" : "start", m.id);
    upsertSummary(r.summary);
    renderList(); renderHeader();
    toast(m.running ? "止めました" : `開始しました（${fmtInterval(m.interval_seconds)}ごと）`, m.name, "ok");
  });
  $("#d-run").onclick = (e) => busy(e.currentTarget, async () => {
    await api.call("run_now", S.selected);
    toast("判定しています", "結果はグラフと記録に入ります");
  });
  $("#d-edit").onclick = () => openEditor("edit");
  $("#d-more").onclick = (e) => { e.stopPropagation(); $("#d-menu").hidden = !$("#d-menu").hidden; };
  document.addEventListener("click", () => ($("#d-menu").hidden = true));
  $("#d-menu").onclick = async (e) => {
    const act = e.target.dataset.action;
    const m = current();
    if (act === "rename") {
      const name = await ask({ title: "名前を変える", input: m.name, ok: "変える" });
      if (name && name.trim()) {
        try { const r = await api.call("update", m.id, { name }); upsertSummary(r.summary); renderList(); renderHeader(); } catch (er) { fail(er); }
      }
    } else if (act === "delete") {
      const yes = await ask({ title: "見張りを削除しますか？", text: `「${m.name}」と、その記録・画像をすべて削除します。元に戻せません。`, ok: "削除する", danger: true });
      if (yes) {
        try {
          await api.call("delete", m.id);
          S.monitors = S.monitors.filter((x) => x.id !== m.id);
          toast("削除しました", m.name);
          select(S.monitors[0] && S.monitors[0].id);
        } catch (er) { fail(er); }
      }
    }
  };
  $$("#range-seg button").forEach((b) => (b.onclick = () => applyRange(b.dataset.range)));
  $("#btn-zoom-reset").onclick = () => applyRange(S.range);
  $("#btn-focus").onclick = toggleFocus;
  $("#o-back").onclick = () => pin(null);
  $$(".tabs button").forEach((b) => (b.onclick = () => {
    $$(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    $("#tab-log").hidden = b.dataset.tab !== "log";
    $("#tab-plan").hidden = b.dataset.tab !== "plan";
  }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#instrument").classList.contains("focus")) toggleFocus(); });
}

function toggleFocus() {
  const inst = $("#instrument");
  const on = !inst.classList.contains("focus");
  inst.classList.toggle("focus", on);
  $("#btn-focus").textContent = on ? "✕" : "⤢";
  $("#btn-focus").title = on ? "元に戻す（Esc）" : "グラフを大きく表示（Esc で戻る）";
  let bd = $(".focus-backdrop");
  if (on && !bd) { bd = h("div", { class: "focus-backdrop", onclick: toggleFocus }); document.body.append(bd); }
  if (!on && bd) bd.remove();
  setTimeout(() => chart && chart.resize(), 30);
}

/* ================================================================== 作成・編集 */
const EXAMPLES = [
  "在庫表示が「在庫あり」に変わったら教えて",
  "チャット欄に自分宛ての急ぎの依頼が来たら教えて",
  "このグラフが急に下がったら教えて",
  "未読や赤いバッジの多さを測りたい",
];
const TYPE_LABEL = { noul: "はい／いいえ", choice: "選択肢", score: "段階評価" };
const TYPE_HELP = {
  noul: "当てはまるかどうかを、0〜1 の確率で返します。「はい」が知りたい状態になる向きで書きます。",
  choice: "決めた選択肢から1つを選びます。丸を付けた選択肢の確率が、この観点の値になります。どれにも当てはまらない場合の other を入れておくと安定します。",
  score: "段階のどこに当たるかを返します。段階は低い順に、程度ではなく目に見える状況で書きます（「少し」「かなり」より「半分ほど埋まっている」）。",
};

function defaultsFor(type, from = {}) {
  const q = { id: from.id, type, instructions: from.instructions || "", weight: from.weight ?? 1, note: from.note || "" };
  if (type === "noul") q.criteria = { true: "", false: "" };
  if (type === "choice") { q.criteria = { yes: "当てはまる", no: "当てはまらない", other: "どちらとも言えない" }; q.target = "yes"; }
  if (type === "score") q.criteria = ["当てはまらない", "一部が当てはまる", "はっきり当てはまる"];
  return q;
}

function newId(type) {
  const base = { noul: "check", choice: "kind", score: "level" }[type] || "q";
  const used = new Set(S.ed.questions.map((q) => q.id));
  let i = 1;
  while (used.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}

function planToQuestions(plan) {
  const weights = (plan.metric && plan.metric.weights) || {};
  return Object.entries(plan.questions || {}).map(([id, q]) => {
    const o = { id, type: q.type, instructions: q.instructions || "", weight: weights[id] ?? 0, note: "" };
    if (q.type === "noul") o.criteria = { true: (q.criteria && q.criteria.true) || "", false: (q.criteria && q.criteria.false) || "" };
    else if (q.type === "choice") { o.criteria = { ...(q.criteria || {}) }; o.target = q.target || null; }
    else o.criteria = [...(q.criteria || [])];
    return o;
  });
}

function buildPlan() {
  const e = S.ed;
  const questions = {}, weights = {};
  for (const q of e.questions) {
    const item = { type: q.type, instructions: q.instructions.trim() };
    if (q.type === "noul") {
      const c = {};
      if (q.criteria.true.trim()) c.true = q.criteria.true.trim();
      if (q.criteria.false.trim()) c.false = q.criteria.false.trim();
      if (Object.keys(c).length) item.criteria = c;
    } else if (q.type === "choice") {
      item.criteria = Object.fromEntries(Object.entries(q.criteria).filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v || null]));
      if (q.target && item.criteria[q.target] !== undefined) item.target = q.target;
    } else item.criteria = q.criteria.map((c) => c.trim()).filter(Boolean);
    questions[q.id] = item;
    const usable = q.type !== "choice" || item.target;
    if (usable && Number(q.weight) > 0) weights[q.id] = Number(q.weight);
  }
  return {
    title: (e.name || e.metricName || "見張り").trim(),
    summary: e.summary || "",
    questions,
    metric: { name: (e.metricName || "指標").trim(), weights },
    notify: { enabled: e.notify.enabled, op: e.notify.op, threshold: Number(e.notify.threshold),
      consecutive: Number(e.notify.consecutive) || 1, cooldown_minutes: Number(e.notify.cooldown_minutes) || 0,
      message: e.notify.message || `${e.metricName || "指標"}が条件を満たしました` },
    suggested_interval_seconds: Number(e.interval) || 300,
    caveats: e.caveats || [],
  };
}

function loadPlanIntoEditor(plan, { keepName = true } = {}) {
  const e = S.ed;
  e.questions = planToQuestions(plan);
  e.metricName = plan.metric.name || e.metricName;
  if (!keepName || !e.name) e.name = plan.title || e.name;
  e.summary = plan.summary || "";
  e.caveats = plan.caveats || [];
  const n = plan.notify || {};
  e.notify = { enabled: n.enabled !== false, op: n.op || ">=", threshold: n.threshold ?? 0.7, consecutive: n.consecutive ?? 2,
    cooldown_minutes: n.cooldown_minutes ?? 60, message: n.message || "" };
  if (plan.suggested_interval_seconds) e.interval = plan.suggested_interval_seconds;
}

function openEditor(mode) {
  const base = { questions: [], notify: { enabled: true, op: ">=", threshold: 0.7, consecutive: 2, cooldown_minutes: 60, message: "" },
    caveats: [], summary: "", interval: 300, provider: "", name: "", metricName: "", request: "", region: null, regionImage: null };
  S.ed = { ...base, mode, id: null, addType: store.get("addType", "noul") };
  if (mode === "edit" && S.mon) {
    const m = S.mon;
    Object.assign(S.ed, { id: m.id, name: m.name, request: m.request, region: m.region, provider: m.provider || "", interval: m.interval_seconds });
    loadPlanIntoEditor(m.plan);
    S.ed.interval = m.interval_seconds;
    api.call("region_image", m.region).then((r) => { if (S.ed && S.ed.id === m.id) { S.ed.regionImage = r.image; renderRegion(); } }).catch(() => {});
  }
  $("#e-title").textContent = mode === "edit" ? `「${S.ed.name}」を編集` : "新しい見張り";
  $("#e-save").textContent = mode === "edit" ? "保存" : "保存のみ";
  $("#e-save-start").textContent = mode === "edit" ? (current() && current().running ? "保存して続ける" : "保存して開始") : "保存して開始";
  fillEditorFields();
  renderRegion();
  renderQuestions();
  renderCaveats();
  $("#e-design-note").textContent = "";
  $("#e-trial").replaceChildren(h("span", { class: "muted", text: "「今の画面で試す」で、保存前に値を確かめられます。" }));
  show("editor");
  $(".editor-scroll").scrollTop = 0;
}

function fillEditorFields() {
  const e = S.ed;
  $("#e-request").value = e.request;
  $("#e-name").value = e.name;
  $("#e-metric-name").value = e.metricName;
  const sel = $("#e-provider");
  sel.replaceChildren(h("option", { value: "", text: `既定の設定（${providerLabel(S.boot.default_provider)}）` }),
    ...S.boot.providers.map((p) => h("option", { value: p.key, text: p.label })));
  sel.value = e.provider || "";
  updateProviderHint();
  $("#e-interval").value = e.interval;
  syncIntervalSeg();
  $("#e-notify-on").checked = e.notify.enabled;
  $("#e-op").value = e.notify.op;
  $("#e-th").value = e.notify.threshold;
  $("#e-th-out").textContent = fmt(Number(e.notify.threshold));
  $("#e-consec").value = e.notify.consecutive;
  $("#e-cool").value = e.notify.cooldown_minutes;
  $("#e-message").value = e.notify.message;
  $("#e-notify-rule").classList.toggle("off", !e.notify.enabled);
  $$("#e-add-type button").forEach((b) => b.classList.toggle("on", b.dataset.type === e.addType));
  $("#e-type-help").textContent = TYPE_HELP[e.addType];
}

function providerLabel(key) { const p = S.boot.providers.find((x) => x.key === key); return p ? p.label.replace(/（.*$/, "") : key; }
function effectiveProvider() { return S.ed.provider || S.boot.default_provider; }
function updateProviderHint() {
  const hint = $("#e-provider-hint");
  const p = effectiveProvider();
  const info = S.boot.providers.find((x) => x.key === p);
  if (p === "jev-local") { hint.hidden = false; hint.textContent = "imajev は英語中心で学習されています。AI に設計させると、問いは英語で作られます。"; }
  else if (info && !info.images) { hint.hidden = false; hint.textContent = "この判定モデルは画像を読めません。画面の範囲を見張るには、画像を読める判定モデルを選んでください。"; }
  else hint.hidden = true;
}
function syncIntervalSeg() {
  $$("#e-interval-seg button").forEach((b) => b.classList.toggle("on", Number(b.dataset.sec) === Number(S.ed.interval)));
}

function renderRegion() {
  const e = S.ed;
  const box = $("#e-region");
  box.classList.toggle("has", !!e.regionImage);
  box.replaceChildren(e.regionImage ? h("img", { src: e.regionImage, alt: "選んだ範囲" }) : h("span", { text: e.region ? "画像を読み込んでいます…" : "まだ選んでいません" }));
  $("#e-region-size").textContent = e.region ? `${e.region.width} × ${e.region.height} px` : "";
  $("#e-pick").textContent = e.region ? "範囲を選び直す" : "範囲を選ぶ";
}

function renderCaveats() {
  const box = $("#e-caveats");
  const c = S.ed.caveats || [];
  box.hidden = !c.length;
  box.replaceChildren(h("b", { text: "この判定で起きやすいこと" }), h("ul", {}, c.map((x) => h("li", { text: x }))));
}

function renderQuestions() {
  const ol = $("#e-questions");
  ol.replaceChildren();
  if (!S.ed.questions.length) {
    ol.append(h("li", { class: "qempty", text: "まだ観点がありません。左の「AI で観点に分ける」か、下の「観点を追加」から作ります。" }));
    return;
  }
  S.ed.questions.forEach((q, i) => ol.append(questionCard(q, i)));
}

function questionCard(q, i) {
  const list = S.ed.questions;
  const usable = q.type !== "choice" || q.target;
  const card = h("li", { class: `q${usable && Number(q.weight) > 0 ? "" : " rec"}` });
  const rerender = () => renderQuestions();

  // 見出しの行: 型・ID・重み・並べ替え・削除
  const seg = h("div", { class: "seg small", role: "group", "aria-label": "問いの型" },
    ...["noul", "choice", "score"].map((t) => h("button", {
      class: t === q.type ? "on" : "", text: TYPE_LABEL[t], type: "button",
      onclick: () => { if (t !== q.type) { list[i] = defaultsFor(t, q); rerender(); } },
    })));
  const idInput = h("input", { type: "text", class: "q-id", value: q.id, title: "問いのID（英数字と _ . -）", "aria-label": "問いのID" });
  idInput.addEventListener("change", () => {
    const v = idInput.value.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 60) || newId(q.type);
    if (list.some((x, j) => j !== i && x.id === v)) { toast("同じIDがあります", v, "error"); idInput.value = q.id; return; }
    q.id = v; idInput.value = v;
  });
  const w = h("input", { type: "number", min: 0, max: 5, step: 0.5, value: q.weight, disabled: !usable, "aria-label": "重み" });
  w.addEventListener("input", () => { q.weight = Number(w.value) || 0; card.classList.toggle("rec", !(Number(q.weight) > 0)); });
  const head = h("div", { class: "q-head" }, seg, idInput,
    h("label", { class: "q-weight", title: usable ? "0 にすると記録だけになります" : "丸を付けた選択肢がないので記録だけです" }, "重み", w),
    h("span", { class: "q-tools" },
      h("button", { type: "button", title: "上へ", text: "↑", disabled: i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; rerender(); } }),
      h("button", { type: "button", title: "下へ", text: "↓", disabled: i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; rerender(); } }),
      h("button", { type: "button", class: "del", title: "この観点を消す", text: "✕", onclick: () => { list.splice(i, 1); rerender(); } }),
    ));
  card.append(head);

  const text = h("textarea", { class: "q-text", rows: 2, placeholder: q.type === "noul" ? "例: 画面に「在庫あり」と表示されているか" : q.type === "choice" ? "例: いちばん目立つ表示はどれか" : "例: 画面の散らかり具合はどの段階か" });
  text.value = q.instructions;
  text.addEventListener("input", () => (q.instructions = text.value));
  card.append(text);

  if (q.type === "noul") {
    const yes = h("input", { type: "text", placeholder: "「はい」に当たる見た目（任意）", value: q.criteria.true });
    const no = h("input", { type: "text", placeholder: "「いいえ」に当たる見た目（任意）", value: q.criteria.false });
    yes.addEventListener("input", () => (q.criteria.true = yes.value));
    no.addEventListener("input", () => (q.criteria.false = no.value));
    card.append(h("div", { class: "q-sub" }, h("div", { class: "q-pair" }, yes, no)));
  } else if (q.type === "choice") {
    const sub = h("div", { class: "q-sub" }, h("div", { class: "q-sub-title" }, h("span", { text: "選択肢（丸を付けた選択肢の確率を値にします）" })));
    const entries = Object.entries(q.criteria);
    entries.forEach(([key, desc], j) => {
      const radio = h("input", { type: "radio", name: `target-${i}`, checked: q.target === key, title: "この選択肢の確率を値にする" });
      radio.addEventListener("click", () => { q.target = q.target === key ? null : key; rerender(); });
      const k = h("input", { type: "text", value: key, placeholder: "ID", "aria-label": "選択肢のID" });
      const d = h("input", { type: "text", value: desc || "", placeholder: "説明（どんな見た目か）", "aria-label": "選択肢の説明" });
      k.addEventListener("change", () => {
        const nk = k.value.trim().replace(/\s+/g, "_");
        if (!nk || (nk !== key && nk in q.criteria)) { k.value = key; return; }
        q.criteria = Object.fromEntries(Object.entries(q.criteria).map(([a, b]) => (a === key ? [nk, b] : [a, b])));
        if (q.target === key) q.target = nk;
        rerender();
      });
      d.addEventListener("input", () => (q.criteria[key] = d.value));
      const del = h("button", { type: "button", class: "mini", text: "✕", title: "消す", disabled: entries.length <= 2,
        onclick: () => { delete q.criteria[key]; if (q.target === key) q.target = null; rerender(); } });
      sub.append(h("div", { class: "opt" }, radio, k, d, del));
    });
    sub.append(h("div", {}, h("button", { type: "button", class: "link", text: "＋ 選択肢を足す", onclick: () => {
      let n = 1; while (`option${n}` in q.criteria) n++; q.criteria[`option${n}`] = ""; rerender(); } })));
    card.append(sub);
  } else {
    const sub = h("div", { class: "q-sub" }, h("div", { class: "q-sub-title" }, h("span", { text: "段階（上ほど低く、下ほど高い値）" })));
    q.criteria.forEach((c, j) => {
      const inp = h("input", { type: "text", value: c, placeholder: j === 0 ? "いちばん低い状態" : j === q.criteria.length - 1 ? "いちばん高い状態" : "中間の状態" });
      inp.addEventListener("input", () => (q.criteria[j] = inp.value));
      const swap = (a, b) => { [q.criteria[a], q.criteria[b]] = [q.criteria[b], q.criteria[a]]; rerender(); };
      sub.append(h("div", { class: "lvl" }, h("span", { class: "lvl-n", text: j }), inp,
        h("button", { type: "button", class: "mini", text: "↑", title: "上へ", disabled: j === 0, onclick: () => swap(j, j - 1) }),
        h("button", { type: "button", class: "mini", text: "↓", title: "下へ", disabled: j === q.criteria.length - 1, onclick: () => swap(j, j + 1) }),
        h("button", { type: "button", class: "mini", text: "✕", title: "消す", disabled: q.criteria.length <= 2, onclick: () => { q.criteria.splice(j, 1); rerender(); } })));
    });
    if (q.criteria.length < 10) sub.append(h("div", {}, h("button", { type: "button", class: "link", text: "＋ 段階を足す", onclick: () => { q.criteria.push(""); rerender(); } })));
    card.append(sub);
  }
  if (q.note) card.append(h("p", { class: "q-note", text: q.note }));
  if (q.result !== undefined) {
    card.append(h("div", { class: "q-result" }, h("span", { text: "試し判定" }),
      h("span", { class: "facet-bar" }, h("i", { style: `width:${q.result === null ? 0 : Math.round(q.result * 100)}%` })),
      h("b", { text: q.result === null ? "判断保留" : fmt(q.result) })));
  }
  return card;
}

function questionsDict() {
  try { return buildPlan().questions; } catch { return {}; }
}

async function checkDraft() {
  const plan = buildPlan();
  if (!Object.keys(plan.questions).length) throw new Error("観点を1つ以上作ってください");
  const r = await api.call("check_plan", plan);
  return r.plan;
}

function draft(plan) {
  const e = S.ed;
  return { name: e.name, request: e.request, region: e.region, plan, interval_seconds: Number(e.interval) || 300, provider: e.provider };
}

function bindEditor() {
  const e = () => S.ed;
  const chips = $("#e-examples");
  chips.replaceChildren(...EXAMPLES.map((x) => h("button", { type: "button", class: "chip", text: x, onclick: () => { $("#e-request").value = x; e().request = x; } })));
  $("#e-request").addEventListener("input", (ev) => (e().request = ev.target.value));
  $("#e-name").addEventListener("input", (ev) => (e().name = ev.target.value));
  $("#e-metric-name").addEventListener("input", (ev) => (e().metricName = ev.target.value));
  $("#e-provider").addEventListener("change", (ev) => { e().provider = ev.target.value; updateProviderHint(); });
  $("#e-interval").addEventListener("input", (ev) => { e().interval = Number(ev.target.value) || 300; syncIntervalSeg(); });
  $$("#e-interval-seg button").forEach((b) => (b.onclick = () => { e().interval = Number(b.dataset.sec); $("#e-interval").value = b.dataset.sec; syncIntervalSeg(); }));
  $("#e-notify-on").addEventListener("change", (ev) => { e().notify.enabled = ev.target.checked; $("#e-notify-rule").classList.toggle("off", !ev.target.checked); });
  $("#e-op").addEventListener("change", (ev) => (e().notify.op = ev.target.value));
  $("#e-th").addEventListener("input", (ev) => { e().notify.threshold = Number(ev.target.value); $("#e-th-out").textContent = fmt(Number(ev.target.value)); });
  $("#e-consec").addEventListener("input", (ev) => (e().notify.consecutive = Number(ev.target.value) || 1));
  $("#e-cool").addEventListener("input", (ev) => (e().notify.cooldown_minutes = Number(ev.target.value) || 0));
  $("#e-message").addEventListener("input", (ev) => (e().notify.message = ev.target.value));
  $$("#e-add-type button").forEach((b) => (b.onclick = () => {
    e().addType = b.dataset.type; store.set("addType", b.dataset.type);
    $$("#e-add-type button").forEach((x) => x.classList.toggle("on", x === b));
    $("#e-type-help").textContent = TYPE_HELP[b.dataset.type];
  }));

  $("#e-pick").onclick = (ev) => busy(ev.currentTarget, async () => {
    const r = await api.call("pick_region");
    if (r.cancelled) return;
    e().region = r.region;
    e().regionImage = r.image;
    renderRegion();
  });

  $("#e-design").onclick = (ev) => busy(ev.currentTarget, async () => {
    if (!e().request.trim()) throw new Error("「知りたいこと」を書いてください");
    if (e().questions.length) {
      const yes = await ask({ title: "観点を作り直しますか？", text: "今ある観点は、AI が作った観点に置き換わります。", ok: "作り直す" });
      if (!yes) return;
    }
    const r = await api.call("design", e().request, e().region, effectiveProvider());
    loadPlanIntoEditor(r.plan, { keepName: true });
    fillEditorFields();
    renderQuestions(); renderCaveats();
    $("#e-design-note").textContent = r.note;
  });

  $("#e-add-blank").onclick = () => {
    const q = defaultsFor(e().addType);
    q.id = newId(e().addType);
    e().questions.push(q);
    renderQuestions();
    const cards = $$("#e-questions .q");
    const last = cards[cards.length - 1];
    if (last) { last.scrollIntoView({ block: "nearest", behavior: "smooth" }); $("textarea", last).focus(); }
  };
  $("#e-add-ai").onclick = (ev) => busy(ev.currentTarget, async () => {
    const r = await api.call("suggest", e().request, e().addType, questionsDict(), e().region, effectiveProvider());
    const q = planToQuestions({ questions: { [r.id]: r.question }, metric: { weights: { [r.id]: r.weight } } })[0];
    q.note = r.reason ? `AI の提案理由: ${r.reason}` : "";
    e().questions.push(q);
    renderQuestions();
    toast(`${TYPE_LABEL[e().addType]}の観点を足しました`, clip(q.instructions, 50), "ok");
  });

  $("#e-json-load").onclick = () => { $("#e-json").value = JSON.stringify(buildPlan(), null, 2); };
  $("#e-json-apply").onclick = (ev) => busy(ev.currentTarget, async () => {
    let parsed;
    try { parsed = JSON.parse($("#e-json").value); } catch (er) { throw new Error(`JSON を読めません: ${er.message}`); }
    const r = await api.call("check_plan", parsed);
    loadPlanIntoEditor(r.plan, { keepName: true });
    fillEditorFields(); renderQuestions(); renderCaveats();
    toast("JSON を反映しました", "", "ok");
  });

  $("#e-try").onclick = (ev) => busy(ev.currentTarget, async () => {
    if (!e().region) throw new Error("見張る範囲を選んでください");
    const plan = await checkDraft();
    const r = await api.call("trial", draft(plan));
    const box = $("#e-trial");
    if (r.status === "error") { box.replaceChildren(h("span", { class: "err", text: `判定できませんでした: ${r.error}` })); return; }
    for (const q of e().questions) {
      const a = r.answers[q.id] || {};
      q.result = a.abstained ? null : (r.values[q.id] ?? undefined);
    }
    renderQuestions();
    box.replaceChildren(
      r.image ? h("img", { src: r.image, alt: "" }) : null,
      h("span", { class: `trial-value${r.hit && e().notify.enabled ? " hit" : ""}`, text: fmt(r.metric) }),
      h("span", {}, h("b", { text: e().metricName || "指標" }), h("br"),
        h("span", { class: "muted small", text: `${e().notify.enabled ? (r.hit ? "通知の条件を満たしています" : "通知の条件は満たしていません") : "通知はオフ"}（${r.provider}、${r.latency_ms} ms）${r.error ? ` ${r.error}` : ""}` })),
    );
  });

  const save = (start) => async (ev) => busy(ev.currentTarget, async () => {
    if (!e().region) throw new Error("見張る範囲を選んでください");
    const plan = await checkDraft();
    const d = draft(plan);
    let id;
    if (e().mode === "edit") {
      const r = await api.call("update", e().id, d);
      upsertSummary(r.summary);
      id = e().id;
      if (start && !r.summary.running) upsertSummary((await api.call("start", id)).summary);
    } else {
      const r = await api.call("create", d, start);
      upsertSummary(r.summary);
      id = r.id;
    }
    toast(e().mode === "edit" ? "保存しました" : start ? "見張りを開始しました" : "保存しました", d.name || plan.title, "ok");
    S.ed = null;
    await reloadAll();
    select(id);
  });
  $("#e-save").onclick = save(false);
  $("#e-save-start").onclick = save(true);
  $("#e-cancel").onclick = () => { S.ed = null; S.selected ? select(S.selected) : show(S.monitors.length ? "detail" : "empty"); if (!S.selected) select(S.monitors[0] && S.monitors[0].id); };
}

/* ================================================================== 設定 */
let settingsData = null;
async function openSettings() {
  try { settingsData = await api.call("get_settings"); } catch (e) { fail(e); return; }
  const form = $("#settings-form");
  const s = settingsData.settings;
  const sel = $("#s-default-provider");
  sel.replaceChildren(...settingsData.providers.map((p) => h("option", { value: p.key, text: p.label })));
  for (const el of $$("[name]", form)) {
    if (el.dataset.secret !== undefined) {
      const sec = settingsData.secrets[el.name] || {};
      el.value = "";
      el.dataset.clear = "";
      el.placeholder = sec.set ? `設定済み（${sec.hint}）・変えるときだけ入力` : (el.getAttribute("placeholder") || "未設定");
      let wrap = el.parentElement.querySelector(".secret-clear");
      if (!wrap && sec.set) {
        const b = h("button", { type: "button", class: "link secret-clear", text: "消去", onclick: () => { el.dataset.clear = "1"; el.value = ""; el.placeholder = "保存すると消去します"; b.remove(); } });
        el.parentElement.style.position = "relative";
        el.parentElement.append(b);
      }
    } else if (s[el.name] !== undefined) el.value = s[el.name];
  }
  syncPreset();
  $("#s-path").textContent = `保存先: ${settingsData.path}`;
  $("#s-version").textContent = `imiwatch ${S.boot.version}${S.boot.browser_mode ? "（ブラウザで表示中）" : ""}`;
  $$(".test-out").forEach((x) => { x.textContent = ""; x.className = "test-out"; });
  $("#dlg-settings").showModal();
}
function llmEndpoint(base, fmt) {
  let u = (base || "").trim().replace(/\/+$/, "");
  if (!u) return "";
  if (fmt === "anthropic") return u.endsWith("/messages") ? u : u.endsWith("/v1") ? `${u}/messages` : `${u}/v1/messages`;
  return u.endsWith("/chat/completions") ? u : `${u}/chat/completions`;
}
function syncPreset() {
  const url = $("[name=llm_base_url]").value.trim();
  const fmt = $("#s-llm-format").value;
  const key = `${fmt}|${url}`;
  const opts = $$("#s-llm-preset option").map((o) => o.value);
  $("#s-llm-preset").value = opts.includes(key) ? key : "custom";
  const ep = llmEndpoint(url, fmt);
  $("#s-llm-endpoint").textContent = ep ? `送り先: ${ep}` : "";
}
function collectSettings() {
  const patch = {};
  for (const el of $$("#settings-form [name]")) {
    if (el.dataset.secret !== undefined) {
      if (el.dataset.clear === "1") patch[el.name] = "__clear__";
      else if (el.value.trim()) patch[el.name] = el.value.trim();
    } else patch[el.name] = el.value;
  }
  return patch;
}
async function saveSettings(quiet = false) {
  await api.call("save_settings", collectSettings());
  S.boot = await api.call("bootstrap");
  if (!quiet) toast("設定を保存しました", "", "ok");
}
function bindSettings() {
  $("#btn-settings").onclick = openSettings;
  $$(".settings-nav button").forEach((b) => (b.onclick = () => {
    $$(".settings-nav button").forEach((x) => x.classList.toggle("on", x === b));
    $$(".settings-body section").forEach((s) => (s.hidden = s.dataset.sec !== b.dataset.sec));
  }));
  $("#s-llm-preset").onchange = (e) => {
    if (e.target.value !== "custom") {
      const [fmt, url] = e.target.value.split("|");
      $("[name=llm_base_url]").value = url;
      $("#s-llm-format").value = fmt;
      syncPreset();
      const isOR = url.includes("openrouter");
      for (const k of ["planner_model", "llm_judge_model"]) {
        const el = $(`[name=${k}]`);
        if (isOR && !el.value) el.value = "openrouter/auto";
        if (!isOR && el.value === "openrouter/auto") el.value = "";
      }
    }
    else $("[name=llm_base_url]").focus();
  };
  $("[name=llm_base_url]").addEventListener("input", syncPreset);
  $("#s-llm-format").addEventListener("change", syncPreset);
  $("#s-cancel").onclick = () => $("#dlg-settings").close();
  $("#s-save").onclick = (e) => busy(e.currentTarget, async () => { await saveSettings(); $("#dlg-settings").close(); if (S.selected) renderHeader(); });
  $$("[data-test]").forEach((b) => (b.onclick = (e) => busy(e.currentTarget, async () => {
    const out = b.parentElement.querySelector(".test-out");
    out.className = "test-out"; out.textContent = "保存して確かめています…";
    try {
      await saveSettings(true);
      const r = await api.call("test_judge", b.dataset.test);
      out.className = "test-out ok";
      out.textContent = `つながりました（${r.latency_ms} ms、白い四角がある確率 ${r.answer === null ? "—" : typeof r.answer === "number" ? fmt(r.answer) : r.answer}）`;
    } catch (er) { out.className = "test-out ng"; out.textContent = er.message; }
  })));
  $("#s-test-llm").onclick = (e) => busy(e.currentTarget, async () => {
    const out = $("#s-test-llm-out");
    out.className = "test-out"; out.textContent = "保存して確かめています…";
    try {
      await saveSettings(true);
      const r = await api.call("test_llm");
      out.className = "test-out ok"; out.textContent = `つながりました（${r.model}、${r.latency_ms} ms）`;
    } catch (er) { out.className = "test-out ng"; out.textContent = er.message; }
  });
  $("#s-open-dir").onclick = () => api.call("open_data_dir").catch(fail);
  $("#s-quit").onclick = async () => {
    const yes = await ask({ title: "imiwatch を終了しますか？", text: "見張りも止まります。", ok: "終了する", danger: true });
    if (!yes) return;
    await api.call("quit").catch(() => {});
    document.body.replaceChildren(h("div", { class: "view-empty", style: "height:100vh" }, h("div", { class: "empty-inner" }, h("h1", { text: "終了しました" }), h("p", { text: "このタブは閉じて構いません。" }))));
  };
}

/* ================================================================== 起動 */
async function main() {
  bindDetail();
  bindEditor();
  bindSettings();
  $("#btn-new").onclick = () => openEditor("new");
  $$("[data-action=new]").forEach((b) => (b.onclick = () => openEditor("new")));
  try {
    S.boot = await api.call("bootstrap");
  } catch (e) {
    document.body.replaceChildren(h("div", { class: "view-empty", style: "height:100vh" }, h("div", { class: "empty-inner" }, h("h1", { text: "起動できませんでした" }), h("p", { text: e.message }))));
    return;
  }
  S.monitors = S.boot.monitors;
  S.seq = S.boot.seq;
  S.range = store.get("range", "all");
  renderList();
  const last = store.get("selected", null);
  const first = S.monitors.find((m) => m.id === last) || S.monitors[0];
  if (first) select(first.id); else show("empty");
  pollLoop();
}
main();
