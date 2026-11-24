"use strict";
const $ = (id) => document.getElementById(id);
const paths = {
  reset: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/>',
  phone:
    '<path d="m5 3 4 1 1 4-2 2c1.4 2.8 3.2 4.6 6 6l2-2 4 1 1 4c-.6 2-2.2 2.5-4.5 2C8 19 3 13 2.7 6.5 2.5 4.8 3 3.6 5 3Z"/>',
  mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
  sound:
    '<path d="M3 9h4l5-4v14l-5-4H3ZM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  wave: '<path d="M2 11v2m4-6v10m4-14v18m4-15v12m4-9v6m4-4v2"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5m-18 5 9 5 9-5"/>',
  trace:
    '<circle cx="5" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="18" cy="12" r="2"/><path d="M5 8v8m2-10c6 0 3 6 9 6"/>',
};
const icon = (k) =>
  '<svg aria-hidden="true" viewBox="0 0 24 24">' + paths[k] + "</svg>";
document
  .querySelectorAll("[data-icon]")
  .forEach((x) => (x.innerHTML = icon(x.dataset.icon)));
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (s) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        s
      ],
  );
let bank,
  call,
  currentId = sessionStorage.getItem("freya-call"),
  seq = 0,
  epoch = 0,
  tab = "slots",
  pinned = null;
let entries = new Map(),
  highlights = new Set(),
  busy = false,
  polling = false,
  recorder,
  stream,
  timeout,
  sound,
  soundURL;
let messageKey = "",
  eventKey = "",
  initializing = true;
function notify(s = "") {
  $("notice").textContent = s;
  $("notice").hidden = !s;
}
async function api(path, options = {}) {
  const response = await fetch("/api/" + path, {
    cache: "no-store",
    ...options,
  });
  if (!response.ok) {
    let error;
    try {
      error = await response.json();
    } catch {}
    const e = new Error(error?.detail || "Servis yanıt vermedi.");
    e.status = response.status;
    e.restartRequired = error?.restart_required === true;
    throw e;
  }
  return response;
}
const post = (path, data) =>
  api(path, {
    method: "POST",
    ...(data
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }
      : {}),
  });
function alive(id, stamp) {
  return currentId === id && epoch === stamp;
}
function stopAudio() {
  if (sound) {
    sound.onended = null;
    sound.pause();
    sound = null;
  }
  if (soundURL) {
    URL.revokeObjectURL(soundURL);
    soundURL = null;
  }
}
function stopDevice() {
  clearTimeout(timeout);
  if (recorder?.state === "recording") {
    recorder.onstop = null;
    recorder.stop();
  }
  recorder = null;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  stopAudio();
}
function clearCall() {
  epoch++;
  stopDevice();
  currentId = null;
  sessionStorage.removeItem("freya-call");
  call = null;
  seq = 0;
  entries.clear();
  pinned = null;
  busy = false;
  messageKey = "";
  eventKey = "";
  draw();
}
function setControls() {
  const live = !!call?.active,
    recording = recorder?.state === "recording";
  $("start").disabled = initializing || live || busy || !bank;
  $("end").disabled = !live;
  $("history").disabled = initializing || live || busy;
  $("customer").disabled = live || busy;
  $("mic").disabled = !live || busy;
  $("skip-birth-date").hidden = !call?.can_skip_birth_date;
  $("skip-birth-date").disabled = busy || recording;
  $("mic").classList.toggle("recording", recording);
  $("miclabel").textContent = recording
    ? "Kaydı bitir ve gönder"
    : busy
      ? "Tur işleniyor…"
      : "Mikrofonu başlat";
  $("phase").textContent = call?.phase || "Görüşme bekleniyor";
  $("phase").parentElement.classList.toggle("live", live);
  if (live) $("customer").value = String(call.customer);
  $("turn").textContent = "Tur " + (call?.turn || 0);
  $("replay").disabled =
    !call?.replay_available ||
    !call.messages.some(
      (m) => m.role === "assistant" && m.turn === call.turn,
    ) ||
    busy ||
    recording;
  $("identity").textContent = call
    ? bank?.customers.find((c) => c.id === call.customer)?.name || "Ses kanalı"
    : "Ses kanalı";
}
function draw() {
  setControls();
  const ms = call?.messages || [],
    selected =
      pinned ??
      (recorder?.state === "recording" ? (call?.turn || 0) + 1 : call?.turn) ??
      0;
  const key = JSON.stringify([ms, selected]);
  if (key !== messageKey) {
    const target = $("messages"),
      atEnd = target.scrollHeight - target.scrollTop - target.clientHeight < 45;
    target.innerHTML = ms.length
      ? ms
          .map(
            (m) =>
              '<button class="bubble ' +
              esc(m.role) +
              (m.turn === selected ? " sel" : "") +
              '" data-turn="' +
              m.turn +
              '"><small>' +
              (m.role === "assistant" ? "Asistan" : "Müşteri · Whisper") +
              " · Tur " +
              m.turn +
              "</small>" +
              esc(m.text) +
              "</button>",
          )
          .join("")
      : '<div class="empty"><div class="waveform" aria-hidden="true">' +
        [6, 12, 8, 20, 29, 14, 24, 34, 17, 9, 23, 13, 7]
          .map((h) => '<i style="height:' + h + 'px"></i>')
          .join("") +
        "</div><strong>Görüşme bekleniyor</strong><p>Bir müşteri seçip görüşmeyi başlatın.</p></div>";
    if (atEnd || pinned === null) target.scrollTop = target.scrollHeight;
    messageKey = key;
  }
  const list = [...entries.values()].filter((e) => e.turn === selected);
  const ekey = JSON.stringify([selected, list]);
  if (eventKey !== ekey) {
    const box = $("events"),
      open = new Set(
        [...box.querySelectorAll("details[open]")].map((d) => d.dataset.step),
      );
    box.innerHTML = list.length
      ? list
          .map(
            (e) =>
              '<details class="event ' +
              esc(e.status) +
              '" data-step="' +
              e.step +
              '" ' +
              (open.has(String(e.step)) ? "open" : "") +
              "><summary><span><strong>" +
              esc(e.component) +
              "</strong><p>" +
              esc(e.summary) +
              "</p></span><time>" +
              (e.ms != null ? e.ms + " ms" : "Tur " + e.turn) +
              "</time></summary><pre>" +
              esc(
                JSON.stringify(
                  {
                    zaman: e.at
                      ? historyTime.format(new Date(e.at))
                      : undefined,
                    ...(e.detail || { sonuç: e.summary }),
                  },
                  null,
                  2,
                ),
              ) +
              "</pre></details>",
          )
          .join("")
      : '<div class="empty"><div class="traceglyph">' +
        icon("trace") +
        "</div><strong>Henüz işlem yok</strong><p>Ses, model kararları ve banka kontrolleri burada görünür.</p></div>";
    eventKey = ekey;
  }
  $("follow").textContent =
    pinned === null ? "Son tur" : "Tur " + pinned + " · Son tura dön";
}
function accept(c) {
  if (c.id !== currentId || (call && call.turn > c.turn)) return;
  for (const e of c.events) {
    const prior = entries.get(e.step);
    if (!prior || e.seq > prior.seq) entries.set(e.step, e);
    seq = Math.max(seq, e.seq);
  }
  // A full response can arrive just after a newer poll for the same turn.
  if (!call || c.turn > call.turn || c.messages.length >= call.messages.length)
    call = c;
  draw();
}
function drawTable() {
  if (!bank) return;
  const slots = Object.fromEntries(bank.slots.map((s) => [s.id, s])),
    customers = Object.fromEntries(bank.customers.map((c) => [c.id, c]));
  const left = bank.slots.reduce((a, s) => a + (s.held ? 0 : s.remaining), 0);
  $("counts").innerHTML =
    "<span><b>" +
    bank.customers.length +
    "</b> müşteri</span><span><b>" +
    left +
    "</b> teklif edilebilir yer</span><span><b>" +
    bank.bookings.length +
    "</b> randevu</span>";
  let heads, rows;
  if (tab === "slots") {
    heads = [
      "İlçe",
      "Teslimat günü",
      "Saat aralığı",
      "Toplam",
      "Dolu",
      "Kalan yer",
      "Geçici ayrım",
    ];
    rows = bank.slots.map((s) => [
      s.id,
      [s.district, s.day, s.hours, s.capacity, s.used, s.remaining, s.held ? "Teklifte" : "—"],
    ]);
  } else if (tab === "customers") {
    heads = ["Müşteri", "İlçe", "Adres türü", "Telefon", "Teslimat durumu"];
    rows = bank.customers.map((c) => [
      c.id,
      [
        c.name,
        c.district,
        c.address,
        c.phone,
        c.booking ? "Randevu alındı" : "Randevu bekliyor",
      ],
    ]);
  } else {
    heads = ["Randevu", "Müşteri", "İlçe", "Teslimat günü", "Saat aralığı"];
    rows = bank.bookings.map((b) => [
      b.id,
      [
        "#" + b.id,
        customers[b.customer].name,
        slots[b.slot].district,
        slots[b.slot].day,
        slots[b.slot].hours,
      ],
    ]);
  }
  $("table").innerHTML =
    "<table><thead><tr>" +
    heads.map((h) => '<th scope="col">' + h + "</th>").join("") +
    "</tr></thead><tbody>" +
    rows
      .map(
        ([id, vals]) =>
          '<tr class="' +
          (highlights.has(tab + id) ? "hilite" : "") +
          '">' +
          vals
            .map(
              (v, i) =>
                "<td>" +
                (tab === "slots" && i === 5
                  ? '<span class="pill">' + esc(v) + "</span>"
                  : esc(v)) +
                "</td>",
            )
            .join("") +
          "</tr>",
      )
      .join("") +
    "</tbody></table>" +
    (!rows.length ? '<p class="noitems">Henüz randevu oluşturulmadı.</p>' : "");
}
let bankRequest = 0;
async function loadBank() {
  const ticket = ++bankRequest,
    next = await (await api("bank")).json();
  if (ticket !== bankRequest) return;
  if (bank) {
    const diff = new Set();
    for (const k of ["customers", "bookings", "slots"])
      for (const r of next[k]) {
        const old = bank[k].find((x) => x.id === r.id);
        if (JSON.stringify(old) !== JSON.stringify(r)) diff.add(k + r.id);
      }
    if (diff.size) highlights = diff;
  }
  const changed = JSON.stringify(next) !== JSON.stringify(bank);
  bank = next;
  $("server").className = "server online";
  $("server").innerHTML = "<i></i> Yerel bağlantı";
  if (!$("customer").options.length)
    $("customer").innerHTML = bank.customers
      .map(
        (c) =>
          '<option value="' +
          c.id +
          '">' +
          esc(c.name) +
          " · " +
          esc(c.district) +
          "</option>",
      )
      .join("");
  if (changed) drawTable();
  setControls();
}
async function syncCall(id, stamp) {
  if (!id) return;
  let c;
  try {
    c = await (await api("calls/" + id + "?after=" + seq)).json();
  } catch (e) {
    if (!alive(id, stamp)) return;
    if (e.restartRequired) {
      c = await (await post("calls/" + id + "/restart")).json();
    } else if (e.status === 410) {
      clearCall();
      return;
    } else {
      throw e;
    }
  }
  if (!alive(id, stamp)) return;
  if (c.id === id) {
    accept(c);
    return;
  }
  clearCall();
  currentId = c.id;
  sessionStorage.setItem("freya-call", c.id);
  accept(c);
  notify(c.messages[0].text);
  if (c.replay_available) void speakTurn(c.id, c.turn, epoch);
}
async function sync() {
  if (polling) return;
  polling = true;
  try {
    await syncCall(currentId, epoch);
    await loadBank();
  } catch {
    $("server").className = "server offline";
    $("server").innerHTML = "<i></i> Bağlantı kesildi";
  } finally {
    polling = false;
  }
}
function report(id, turn, kind, duration_ms = null) {
  return post("calls/" + id + "/device", { turn, kind, duration_ms }).catch(
    () => {},
  );
}
async function speakTurn(id, turn, stamp) {
  if (!alive(id, stamp) || call?.turn !== turn) return;
  stopAudio();
  try {
    const r = await api("calls/" + id + "/speech/" + turn),
      blob = await r.blob();
    if (!alive(id, stamp) || call?.turn !== turn) return;
    soundURL = URL.createObjectURL(blob);
    const player = new Audio(soundURL);
    sound = player;
    player.onended = () => {
      if (alive(id, stamp))
        report(id, turn, "played", Math.round(player.currentTime * 1000));
    };
    await player.play();
    if (alive(id, stamp)) await report(id, turn, "playing");
  } catch (e) {
    if (alive(id, stamp)) {
      if (!e.status) await report(id, turn, "blocked");
      notify(
        e.status
          ? e.message
          : "Ses otomatik oynatılamadı. Hoparlör düğmesiyle dinleyebilirsiniz.",
      );
    }
  }
}
$("start").onclick = async () => {
  const customer = Number($("customer").value);
  clearCall();
  const stamp = epoch;
  busy = true;
  setControls();
  notify();
  try {
    const c = await (await post("calls", { customer })).json();
    if (stamp !== epoch) return;
    currentId = c.id;
    sessionStorage.setItem("freya-call", c.id);
    accept(c);
    await speakTurn(c.id, 0, stamp);
  } catch (e) {
    if (stamp === epoch) notify(e.message);
  } finally {
    if (stamp === epoch) {
      busy = false;
      draw();
    }
  }
};
$("end").onclick = async () => {
  if (!currentId) return;
  const id = currentId,
    stamp = ++epoch;
  stopDevice();
  busy = true;
  setControls();
  try {
    accept(await (await post("calls/" + id + "/end")).json());
  } catch (e) {
    notify(e.message);
  } finally {
    if (stamp === epoch) {
      busy = false;
      draw();
    }
  }
};
$("mic").onclick = async () => {
  if (recorder?.state === "recording") {
    recorder.stop();
    return;
  }
  const id = currentId,
    stamp = epoch,
    t = call.turn;
  stopAudio();
  notify();
  busy = true;
  setControls();
  try {
    const input = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!alive(id, stamp)) {
      input.getTracks().forEach((track) => track.stop());
      return;
    }
    stream = input;
    const mime = [
      "audio/webm;codecs=opus",
      "audio/mp4",
      "audio/ogg;codecs=opus",
    ].find((m) => MediaRecorder.isTypeSupported(m));
    recorder = new MediaRecorder(input, mime ? { mimeType: mime } : undefined);
    const parts = [],
      format = recorder.mimeType;
    recorder.ondataavailable = (e) => {
      if (e.data.size) parts.push(e.data);
    };
    recorder.onstop = async () => {
      clearTimeout(timeout);
      input.getTracks().forEach((track) => track.stop());
      stream = null;
      recorder = null;
      if (!alive(id, stamp)) return;
      busy = true;
      setControls();
      pinned = null;
      try {
        const r = await api("calls/" + id + "/turn?expected=" + t, {
          method: "POST",
          headers: {
            "Content-Type": format,
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: new Blob(parts, { type: format }),
        });
        const c = await r.json();
        if (!alive(id, stamp)) return;
        accept(c);
        await loadBank();
        await speakTurn(id, c.turn, stamp);
      } catch (e) {
        if (alive(id, stamp)) notify(e.message);
      } finally {
        if (alive(id, stamp)) {
          busy = false;
          draw();
        }
      }
    };
    recorder.onerror = () => {
      if (alive(id, stamp)) {
        stopDevice();
        busy = false;
        draw();
        notify("Mikrofon kaydı kesildi. Tekrar deneyin.");
        report(id, t, "mic_error");
      }
    };
    recorder.start();
    report(id, t, "recording");
    busy = false;
    setControls();
    timeout = setTimeout(() => {
      if (recorder?.state === "recording") recorder.stop();
    }, 29500);
  } catch {
    if (alive(id, stamp)) {
      stopDevice();
      busy = false;
      draw();
      notify("Mikrofon açılamadı. Tarayıcıdaki mikrofon iznini kontrol edin.");
      await report(id, t, "mic_error");
    }
  }
};
$("skip-birth-date").onclick = async () => {
  const id = currentId,
    stamp = epoch;
  stopAudio();
  notify();
  pinned = null;
  busy = true;
  draw();
  try {
    const c = await (
      await post("calls/" + id + "/skip-birth-date?expected=" + call.turn)
    ).json();
    if (!alive(id, stamp)) return;
    accept(c);
    await speakTurn(id, c.turn, stamp);
  } catch (error) {
    if (alive(id, stamp)) notify(error.message);
  } finally {
    if (alive(id, stamp)) {
      busy = false;
      draw();
    }
  }
};
$("replay").onclick = () => {
  if (currentId) speakTurn(currentId, call.turn, epoch);
};
const historyTime = new Intl.DateTimeFormat("tr-TR", {
  dateStyle: "medium",
  timeStyle: "medium",
  timeZone: "Europe/Istanbul",
});
$("history").onclick = async () => {
  const status = $("history-status"),
    list = $("history-list");
  status.textContent = "Yükleniyor…";
  list.replaceChildren();
  $("history-dialog").showModal();
  try {
    const history = await (await api("history")).json();
    status.textContent = history.length ? "" : "Henüz kayıtlı görüşme yok.";
    for (const item of history) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.className = "history-entry";
      button.dataset.callId = item.id;
      const name = document.createElement("strong");
      name.textContent = item.name;
      const time = document.createElement("time");
      time.dateTime = item.started_at;
      time.textContent = historyTime.format(new Date(item.started_at));
      button.append(name, time);
      row.append(button);
      list.append(row);
    }
  } catch (error) {
    status.textContent = error.message;
  }
};
$("history-list").onclick = async (event) => {
  const id = event.target.closest("[data-call-id]")?.dataset.callId;
  if (!id) return;
  $("history-dialog").close();
  clearCall();
  notify();
  currentId = id;
  sessionStorage.setItem("freya-call", id);
  const stamp = epoch;
  busy = true;
  draw();
  try {
    const archived = await (await api("calls/" + id)).json();
    if (alive(id, stamp)) accept(archived);
  } catch (error) {
    if (alive(id, stamp)) {
      clearCall();
      notify(error.message);
    }
  } finally {
    if (alive(id, stamp)) {
      busy = false;
      draw();
    }
  }
};
$("close-history").onclick = () => $("history-dialog").close();
$("reset").onclick = () => $("dialog").showModal();
$("cancel").onclick = () => $("dialog").close();
$("confirmReset").onclick = async () => {
  $("dialog").close();
  const id = currentId,
    stamp = ++epoch;
  stopDevice();
  busy = true;
  setControls();
  notify();
  try {
    await post("reset");
    await syncCall(id, stamp);
    bank = null;
    highlights.clear();
    await loadBank();
    if (currentId === null) notify("Veritabanı başlangıç durumuna döndü.");
  } catch (e) {
    notify("Sıfırlama doğrulanamadı. " + e.message);
    await sync();
  } finally {
    busy = false;
    draw();
  }
};
$("messages").onclick = (e) => {
  const b = e.target.closest("[data-turn]");
  if (b) {
    pinned = Number(b.dataset.turn);
    draw();
  }
};
$("follow").onclick = () => {
  pinned = null;
  draw();
};
$("tabs").onclick = (e) => {
  const b = e.target.closest("[data-tab]");
  if (!b) return;
  tab = b.dataset.tab;
  for (const x of $("tabs").children) x.classList.toggle("selected", x === b);
  drawTable();
};
window.addEventListener("pagehide", stopDevice);
draw();
sync().finally(() => {
  initializing = false;
  draw();
});
setInterval(sync, 500);
