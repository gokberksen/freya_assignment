import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
const resetGreeting = "Teknik bir hata nedeniyle görüşmeyi baştan başlatıyorum. Kurye beklemeyin.";
const customer = { id: 1, name: "Ayşe Kaya", district: "Kadıköy" };
const before = { customers: [customer], slots: [], bookings: [] };
const booked = { ...before, bookings: [{ id: 1, customer: 1, slot: 2 }] };
const json = (data, status = 200) => Response.json(data, { status });
const wav = () => new Response("audio", { headers: { "Content-Type": "audio/wav" } });

function call(id, turn, phase, message, active = true) {
  return {
    id, customer: 1, turn, phase, active, replay_available: true, events: [],
    messages: [{ turn, role: "assistant", text: message }],
  };
}

async function panel(storage, respond, play = async () => {}) {
  const elements = new Map();
  const requests = [];
  const played = [];
  let poll;
  const element = (id) => {
    if (!elements.has(id)) {
      const classList = { toggle() {} };
      elements.set(id, {
        classList, parentElement: { classList }, options: [],
        querySelectorAll: () => [], close() {}, showModal() {},
      });
    }
    return elements.get(id);
  };
  runInNewContext(source, {
    document: { getElementById: element, querySelectorAll: () => [] },
    window: { addEventListener() {} },
    sessionStorage: {
      getItem: (key) => storage.get(key),
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    fetch: (path, options) => {
      const method = options.method || "GET";
      const url = new URL(path, "http://localhost");
      requests.push([method, url.pathname]);
      return respond(method, url.pathname);
    },
    setInterval: (callback) => { poll = callback; },
    clearTimeout,
    URL: { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL },
    Audio: class {
      play() {
        played.push("play");
        return play();
      }
      pause() {}
    },
  }, { filename: "web/app.js" });
  await setImmediate();
  return { element, poll, played, requests };
}

test("a reset in another tab adopts the fresh call and discards delayed old audio", async () => {
  const storage = new Map([["freya-call", "old"]]);
  const old = call("old", 3, "Onay bekleniyor", "Randevuyu onaylıyor musunuz?");
  const fresh = call("new", 0, "Görüşme izni", resetGreeting);
  let reset = false;
  let releaseOldAudio;
  const oldAudio = new Promise((resolve) => { releaseOldAudio = resolve; });
  const ui = await panel(storage, (_method, path) => {
    if (path === "/api/bank") return json(before);
    if (path === "/api/calls/old") return json(reset ? fresh : old);
    if (path === "/api/calls/old/speech/3") return oldAudio;
    if (path === "/api/calls/new/speech/0") return wav();
    if (path === "/api/calls/new/device") return json({ ok: true });
    assert.fail(path);
  });
  ui.element("replay").onclick();
  reset = true;
  await ui.poll();
  releaseOldAudio(wav());
  await setImmediate();
  assert.equal(storage.get("freya-call"), "new");
  assert.equal(ui.element("turn").textContent, "Tur 0");
  assert.match(ui.element("messages").innerHTML, /Kurye beklemeyin/);
  assert.doesNotMatch(ui.element("messages").innerHTML, /onaylıyor musunuz/);
  assert.deepEqual(ui.played, ["play"]);
});

test("the reset button restarts a booking panel and announces the cancellation", async () => {
  const storage = new Map([["freya-call", "finished"]]);
  const done = call("finished", 4, "Tamamlandı", "Randevunuz kaydedildi.", false);
  const fresh = call("new", 0, "Görüşme izni", resetGreeting);
  let reset = false;
  const ui = await panel(storage, (method, path) => {
    if (path === "/api/bank") return json(reset ? before : booked);
    if (method === "POST" && path === "/api/reset") {
      reset = true;
      return json(before);
    }
    if (path === "/api/calls/finished") {
      return reset ? json({ restart_required: true }, 410) : json(done);
    }
    if (method === "POST" && path === "/api/calls/finished/restart") return json(fresh);
    if (path === "/api/calls/new") return json(fresh);
    if (path === "/api/calls/new/speech/0") return wav();
    if (path === "/api/calls/new/device") return json({ ok: true });
    assert.fail(path);
  });
  await ui.element("confirmReset").onclick();
  await ui.poll();
  assert.equal(storage.get("freya-call"), "new");
  assert.match(ui.element("notice").textContent, /Kurye beklemeyin/);
  assert.match(ui.element("counts").innerHTML, /<b>0<\/b> randevu/);
  assert.equal(ui.requests.filter(([, path]) => path.endsWith("/restart")).length, 1);
  assert.deepEqual(ui.played, ["play"]);
});

test("the written booking remains visible after playback rejection and refresh", async () => {
  const storage = new Map([["freya-call", "finished"]]);
  const done = call("finished", 4, "Tamamlandı", "Randevunuz kaydedildi.", false);
  const respond = (_method, path) => {
    if (path === "/api/bank") return json(booked);
    if (path === "/api/calls/finished") return json(done);
    if (path === "/api/calls/finished/speech/4") return wav();
    if (path === "/api/calls/finished/device") return json({ ok: true });
    assert.fail(path);
  };
  const ui = await panel(storage, respond, async () => { throw Error("Playback blocked"); });
  ui.element("replay").onclick();
  await setImmediate();
  assert.match(ui.element("notice").textContent, /Ses otomatik oynatılamadı/);
  assert.match(ui.element("messages").innerHTML, /Randevunuz kaydedildi/);
  const refreshed = await panel(storage, respond);
  assert.match(refreshed.element("messages").innerHTML, /Randevunuz kaydedildi/);
  assert.match(refreshed.element("counts").innerHTML, /<b>1<\/b> randevu/);
  assert.ok(ui.requests.every(([method, path]) => method === "GET" || path.endsWith("/device")));
});

test("the panel identifies a held interval and excludes it from offerable capacity", async () => {
  const slots = [
    { id: 1, district: "Kadıköy", day: "Perşembe", hours: "09:00–13:00", capacity: 1, used: 0, remaining: 1, held: 1 },
    { id: 2, district: "Kadıköy", day: "Cuma", hours: "14:00–18:00", capacity: 1, used: 0, remaining: 1, held: 0 },
  ];
  const ui = await panel(new Map(), (_method, path) => {
    assert.equal(path, "/api/bank");
    return json({ ...before, slots });
  });
  assert.match(ui.element("counts").innerHTML, /<b>1<\/b> teklif edilebilir yer/);
  assert.match(ui.element("table").innerHTML, /Perşembe.*Teklifte.*Cuma/);
});
