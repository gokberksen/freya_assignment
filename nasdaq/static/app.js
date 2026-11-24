import { createChart } from "./chart.js";

const byId = id => document.getElementById(id);
const units = ["1D", "1W", "1M", "1Y"];
const price = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const count = new Intl.NumberFormat("tr-TR");
const change = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
const fullDate = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const chart = createChart(byId("price-chart"), byId("chart-tooltip"));
const tbody = byId("leaders-body");
const marketError = byId("market-error");
const alertsError = byId("alarm-error");
const alertsList = byId("alert-list");
const alarmDialog = byId("alarm-dialog");
const callDialog = byId("call-dialog");
const form = byId("alarm-form");
const callActions = byId("call-actions");
const callMessages = byId("call-messages");
const callAudioError = byId("call-audio-error");
const callAudio = byId("call-audio");
const periodButtons = [...document.querySelectorAll("[data-period]")];

let data;
let selected;
let period = "1D";
let detailVersion = 0;
let ratingsVersion = 0;
let loadingMarket = false;
let alertsVersion = 0;
let alertsKey;
let formStock;
let call;
let listening;
let callVersion = 0;
const pending = new Set();

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, className, action) {
  const node = element("button", text, className);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function message(node, text) {
  node.textContent = text || "";
  node.hidden = !text;
}

async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return;
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "İşlem tamamlanamadı. Tekrar deneyin.");
  }
  return response.json();
}

function returnText(value) {
  return value == null ? "—" : change.format(value) + "%";
}

function returnClass(value) {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

function markSelected() {
  for (const row of tbody.querySelectorAll("tr[data-symbol]")) {
    const active = row.dataset.symbol === selected?.symbol;
    row.classList.toggle("selected", active);
    row.querySelector("button").setAttribute("aria-pressed", String(active));
  }
}

function writeRows(stocks) {
  const focusSymbol = tbody.contains(document.activeElement) ? document.activeElement.closest("tr")?.dataset.symbol : null;
  const rows = stocks.map(stock => {
    const row = element("tr");
    row.dataset.symbol = stock.symbol;
    const symbolButton = button(undefined, "symbol", () => selectStock(stock, true));
    symbolButton.setAttribute("aria-label", stock.symbol + ", " + stock.name + ", detayları göster");
    symbolButton.append(element("strong", stock.symbol), element("small", stock.name));
    const nameCell = element("td");
    nameCell.append(symbolButton);
    row.append(nameCell, element("td", price.format(stock.price), "price"),
      element("td", count.format(stock.volume), "vol"));
    units.forEach(unit => {
      const value = stock.changes[unit];
      row.append(element("td", returnText(value), "return " + returnClass(value)));
    });
    row.addEventListener("click", event => {
      if (!event.target.closest("button")) selectStock(stock, true);
    });
    return row;
  });
  tbody.replaceChildren(...rows);
  markSelected();
  if (focusSymbol) rows.find(row => row.dataset.symbol === focusSymbol)?.querySelector("button").focus({ preventScroll: true });
}

function showStockHeader(stock) {
  byId("stock-title").textContent = stock.symbol;
  byId("stock-name").textContent = stock.name;
  byId("stock-price").textContent = price.format(stock.price);
  const value = stock.changes[period];
  const delta = byId("stock-change");
  delta.textContent = returnText(value);
  delta.className = "return " + returnClass(value);
  byId("open-alarm").disabled = false;
}

async function loadChart(stock, unit, clear) {
  const request = ++detailVersion;
  if (clear) {
    message(byId("chart-message"), "Fiyat geçmişi yükleniyor…");
    chart([], unit);
  }
  try {
    const detail = await api("/stocks/" + encodeURIComponent(stock.symbol) + "?period=" + unit);
    if (request !== detailVersion) return;
    const hasChart = detail.points.length > 1;
    chart(hasChart ? detail.points : [], unit);
    message(byId("chart-message"), hasChart ? "" : "Bu dönem için yeterli fiyat geçmişi bulunamadı.");
  } catch (error) {
    if (request === detailVersion) {
      chart([], unit);
      message(byId("chart-message"), error.message);
    }
  }
}

async function loadRatings(stock, clear) {
  const request = ++ratingsVersion;
  if (clear) {
    byId("ratings-data").hidden = true;
    message(byId("ratings-message"), "Analist görüşleri yükleniyor…");
    message(byId("ratings-source"), "");
  }
  let ratings;
  try {
    ({ ratings } = await api("/stocks/" + encodeURIComponent(stock.symbol) + "/ratings"));
  } catch {
    ratings = null;
  }
  if (request !== ratingsVersion) return;
  const total = ratings ? ratings.buy + ratings.hold + ratings.sell : 0;
  byId("ratings-data").hidden = !total;
  message(byId("ratings-message"), total ? "" : "Bu hisse için analist dağılımı bulunamadı.");
  message(byId("ratings-source"), total ? (data.mode === "demo" ? "Örnek veri" : "FMP · Güçlü görüşler dahil") : "");
  if (!total) return;
  const bars = [...document.querySelectorAll(".rating-bar i")];
  ["buy", "hold", "sell"].forEach((key, index) => {
    byId("rating-" + key).textContent = count.format(ratings[key]);
    bars[index].style.flexBasis = (ratings[key] / total * 100) + "%";
    bars[index].hidden = ratings[key] === 0;
  });
}

function selectStock(stock, fromUser = false) {
  const changed = stock.symbol !== selected?.symbol;
  selected = stock;
  showStockHeader(stock);
  markSelected();
  loadChart(stock, period, changed || fromUser);
  loadRatings(stock, changed);
  if (fromUser && matchMedia("(max-width: 1080px)").matches) {
    byId("hisse-detayi").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
}

periodButtons.forEach(node => node.addEventListener("click", () => {
  period = node.dataset.period;
  periodButtons.forEach(item => item.setAttribute("aria-pressed", String(item === node)));
  if (selected) {
    showStockHeader(selected);
    loadChart(selected, period, true);
  }
}));

async function refreshMarket(force = false) {
  if (loadingMarket) return;
  loadingMarket = true;
  const refresh = byId("refresh");
  refresh.disabled = true;
  try {
    const next = await api("/market");
    const changed = JSON.stringify(next.stocks) !== JSON.stringify(data?.stocks);
    data = next;
    message(marketError, "");
    const tag = byId("market-mode");
    tag.textContent = next.mode === "demo" ? "Demo verisi" : next.delay_minutes ? next.delay_minutes + " dk gecikmeli" : "Anlık veri";
    tag.classList.toggle("live", next.mode !== "demo");
    const date = byId("market-date");
    date.dateTime = next.session;
    date.textContent = fullDate.format(new Date(next.session + "T12:00:00Z")) + " · New York seansı";
    byId("data-source").textContent = next.mode === "demo" ? "Fiyatlar ve görüşler örnektir." : "Fiyat ve hacim: Massive · Analist görüşleri: FMP";
    if (changed || force || !selected) {
      if (changed) writeRows(next.stocks);
      const stock = next.stocks.find(item => item.symbol === selected?.symbol) ?? next.stocks[0];
      if (stock) selectStock(stock);
    }
  } catch (error) {
    message(marketError, error.message);
    if (!data) {
      tbody.rows[0].cells[0].textContent = "Hisse listesi şu anda yüklenemiyor.";
      byId("market-mode").textContent = "Veri alınamadı";
      message(byId("chart-message"), "Hisse listesi bekleniyor.");
      message(byId("ratings-message"), "Hisse listesi bekleniyor.");
    }
  } finally {
    loadingMarket = false;
    refresh.disabled = false;
  }
}

function renderAlerts(items) {
  const key = JSON.stringify(items);
  if (alertsKey === key) return;
  const activeAlertButton = alertsList.contains(document.activeElement) ? document.activeElement : null;
  const activeAlertRow = activeAlertButton?.closest("[data-alert-id]");
  const activeAction = activeAlertButton?.dataset.action;
  alertsKey = key;
  const triggered = items.filter(item => item.triggered_at).length;
  const counter = byId("alert-count");
  counter.textContent = triggered;
  counter.hidden = triggered === 0;
  if (!items.length) {
    alertsList.replaceChildren(element("p", "Henüz alarm yok. Bir hisse seçip Alarm kur düğmesine basın.", "empty-alerts"));
    return;
  }
  alertsList.replaceChildren(...items.map(item => {
    const row = element("div", undefined, "alert-row");
    row.dataset.alertId = item.id;
    const info = element("div");
    const condition = item.direction === "above" ? "ve üzeri" : "ve altı";
    info.append(element("strong", item.symbol), element("small", price.format(item.target) + " USD " + condition));
    const controls = element("div", undefined, "alert-actions");
    const triggeredLabel = item.trigger_source === "manual" ? "Elle tetiklendi" : "Hedefe ulaştı";
    controls.append(element("span", item.triggered_at ? triggeredLabel : "Hedef bekleniyor",
      item.triggered_at ? "alert-status triggered" : "alert-status"));
    const action = button(item.triggered_at ? "Çağrıyı başlat" : "Alarmı tetikle",
      "button " + (item.triggered_at ? "primary" : "quiet"), () => {
        const path = "/alerts/" + item.id;
        if (item.triggered_at) startCall(item);
        else updateAlert(item.id, path + "/trigger", "POST");
      });
    action.dataset.action = "main";
    const remove = button("Sil", "button quiet", () => updateAlert(item.id, "/alerts/" + item.id, "DELETE"));
    remove.setAttribute("aria-label", item.symbol + " alarmını sil");
    remove.dataset.action = "delete";
    action.disabled = pending.has(item.id);
    remove.disabled = pending.has(item.id);
    controls.append(action, remove);
    row.append(info, controls);
    return row;
  }));
  if (activeAlertRow && activeAction) {
    [...alertsList.children].find(row => row.dataset.alertId === activeAlertRow.dataset.alertId)
      ?.querySelector('[data-action="' + activeAction + '"]')?.focus({ preventScroll: true });
  }
}

async function refreshAlerts() {
  const version = ++alertsVersion;
  try {
    const items = await api("/alerts");
    if (version !== alertsVersion) return;
    renderAlerts(items);
    if (!pending.size) message(alertsError, "");
  } catch (error) {
    if (version === alertsVersion) message(alertsError, error.message);
  }
}

function setAlertBusy(id, busy) {
  if (busy) pending.add(id);
  else pending.delete(id);
  for (const row of alertsList.children) {
    if (row.dataset.alertId === id) {
      row.querySelectorAll("button").forEach(node => { node.disabled = busy; });
    }
  }
}

async function updateAlert(id, path, method) {
  if (pending.has(id)) return;
  setAlertBusy(id, true);
  try {
    await api(path, method);
    await refreshAlerts();
  } catch (error) {
    message(alertsError, error.message);
  } finally {
    setAlertBusy(id, false);
  }
}

byId("open-alarm").addEventListener("click", () => {
  if (!selected) return;
  formStock = selected;
  form.reset();
  byId("alarm-symbol").textContent = selected.symbol + " · " + price.format(selected.price) + " USD";
  message(byId("form-error"), "");
  alarmDialog.showModal();
  byId("target").focus();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const targetText = form.elements.target.value.trim();
  const target = Number(targetText.replace(",", "."));
  const errorNode = byId("form-error");
  if (!/^\d+(?:[,.]\d{1,4})?$/.test(targetText) || target <= 0 || target > 1000000) {
    message(errorNode, "0 ile 1.000.000 USD arasında, en çok dört ondalık basamaklı bir fiyat girin.");
    return;
  }
  const save = form.querySelector('[type="submit"]');
  if (save.disabled) return;
  save.disabled = true;
  try {
    await api("/alerts", "POST", { symbol: formStock.symbol, target, direction: form.elements.direction.value });
    alarmDialog.close();
    await refreshAlerts();
    byId("alarmlar").scrollIntoView({ block: "nearest" });
  } catch (error) {
    message(errorNode, error.message);
  } finally {
    save.disabled = false;
  }
});

async function playCallAudio() {
  const currentCall = call;
  const cue = call?.lastCue;
  if (!cue) return;
  const url = "/api/alerts/" + encodeURIComponent(call.id) + "/speech/" + cue;
  message(callAudioError, "");
  if (callAudio.getAttribute("src") !== url || callAudio.error) {
    callAudio.src = url;
  } else {
    callAudio.currentTime = 0;
  }
  try {
    await callAudio.play();
  } catch (error) {
    if (call !== currentCall || call.lastCue !== cue || error.name === "AbortError") return;
    message(callAudioError, error.name === "NotAllowedError"
      ? "Antalia sesini dinlemek için Sesi yeniden çal düğmesine basın."
      : "Antalia sesi oynatılamadı. Sesi yeniden çalmayı deneyin.");
  }
}

function sayInCall(cue) {
  call.lastCue = cue;
  const text = cue === "report" ? call.script.report + "\n\n" + call.script.question : call.script[cue];
  addLine("Freya", text);
  playCallAudio();
}

function addLine(actor, text) {
  const node = element("li", undefined, actor === "Siz" ? "caller" : "");
  node.append(element("small", actor), document.createTextNode(text));
  callMessages.append(node);
  callMessages.scrollTop = callMessages.scrollHeight;
}

function endCall(cue) {
  call.phase = "closed";
  sayInCall(cue);
  showCallActions();
}

function answer(reply) {
  if (!call) return;
  const phase = call.phase;
  if (phase === "closed" || phase === "loading") return;
  if (listening) {
    listening.abort();
    listening = null;
  }
  if (phase === "availability" && reply === "Evet, müsaitim.") {
    addLine("Siz", reply);
    call.phase = "questions";
    sayInCall("report");
    showCallActions();
  } else if (phase === "availability") {
    addLine("Siz", reply);
    endCall("unavailable");
  } else if (phase === "questions") {
    addLine("Siz", reply);
    endCall("goodbye");
  }
}

function hearAnswer() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition || listening || !call) return;
  callAudio.pause();
  const recognition = new SpeechRecognition();
  const phase = call.phase;
  listening = recognition;
  recognition.lang = "tr-TR";
  recognition.maxAlternatives = 1;
  message(byId("call-state"), "Dinliyorum…");
  recognition.onresult = event => {
    if (listening !== recognition || !call || phase !== call.phase) return;
    const words = event.results[0][0].transcript.toLocaleLowerCase("tr-TR").replace(/[^\p{L} ]/gu, " ").trim();
    if (phase === "availability" && /\b(değil(?:im)?|hayır|meşgulüm|uygun değilim)\b/u.test(words)) {
      answer("Şu an müsait değilim.");
    } else if (phase === "availability" && /\b(evet|müsaitim|uygunum|buyurun|dinliyorum)\b/u.test(words)) {
      answer("Evet, müsaitim.");
    } else if (phase === "questions" && /\b(hayır|yok)\b/u.test(words)) {
      answer("Hayır.");
    } else {
      message(byId("call-state"), "Yanıt anlaşılamadı. Düğmelerden birini kullanabilirsiniz.");
    }
  };
  recognition.onerror = () => {
    if (listening === recognition) message(byId("call-state"), "Sesli yanıt alınamadı. Düğmelerden birini kullanabilirsiniz.");
  };
  recognition.onend = () => {
    if (listening !== recognition) return;
    listening = null;
    if (byId("call-state").textContent === "Dinliyorum…") showCallActions();
  };
  try {
    recognition.start();
  } catch {
    listening = null;
    message(byId("call-state"), "Mikrofon başlatılamadı. Düğmelerden birini kullanabilirsiniz.");
  }
}

function showCallActions() {
  const actions = [];
  let status = "";
  if (call.phase === "availability") {
    actions.push(button("Şu an müsait değilim", "button quiet", () => answer("Şu an müsait değilim.")),
      button("Evet, müsaitim", "button primary", () => answer("Evet, müsaitim.")));
  } else if (call.phase === "questions") {
    actions.push(button("Hayır", "button primary", () => answer("Hayır.")));
  } else if (call.phase === "closed") {
    status = "Görüşme sona erdi.";
    actions.push(button("Kapat", "button primary", () => callDialog.close()));
  } else if (call.phase === "loading") {
    status = "Görüşme hazırlanıyor…";
  }
  if (call.lastCue) {
    actions.unshift(button("Sesi yeniden çal", "button quiet", playCallAudio));
  }
  if (["availability", "questions"].includes(call.phase) && (window.SpeechRecognition || window.webkitSpeechRecognition)) {
    actions.unshift(button("Sesli yanıtla", "button quiet", hearAnswer));
  }
  callActions.replaceChildren(...actions);
  message(byId("call-state"), status);
}

async function startCall(item) {
  const version = ++callVersion;
  call = { phase: "loading" };
  callMessages.replaceChildren();
  message(callAudioError, "");
  byId("call-subtitle").textContent = item.symbol + " · Simüle görüşme";
  callDialog.showModal();
  showCallActions();
  try {
    const script = await api("/alerts/" + item.id + "/call", "POST");
    if (version !== callVersion || !callDialog.open) return;
    call = { id: item.id, script, phase: "availability" };
    sayInCall("greeting");
    showCallActions();
  } catch (error) {
    if (version === callVersion && callDialog.open) message(byId("call-state"), error.message);
  }
}

callDialog.addEventListener("close", () => {
  ++callVersion;
  call = null;
  callAudio.removeAttribute("src");
  callAudio.load();
  listening?.abort();
  listening = null;
});
for (const dialog of [alarmDialog, callDialog]) {
  dialog.querySelectorAll("[data-close]").forEach(node => node.addEventListener("click", () => dialog.close()));
}

byId("refresh").addEventListener("click", () => refreshMarket(true));
refreshMarket();
refreshAlerts();
setInterval(refreshMarket, 60_000);
setInterval(() => { if (!document.hidden) refreshAlerts(); }, 8_000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshAlerts(); });
