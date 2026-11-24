from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from freya import voice
from freya.bank import Bank
from freya.engine import Engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FREYA_DB", str(tmp_path / "api.sqlite"))
    from freya import app

    monkeypatch.setattr(app, "engine", Engine(Bank(tmp_path / "api.sqlite")))
    return TestClient(app.app)


def say(client, call, phrase, key=None):
    headers = {"Content-Type": "audio/wav"}
    if key:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/calls/{call['id']}/turn",
        params={"expected": call["turn"]},
        headers=headers,
        content=phrase.encode(),
    )


@pytest.fixture
def text_audio(monkeypatch):
    monkeypatch.setattr(voice, "transcribe", lambda blob, phase: (blob.decode(), 1))


def verified_call(client, customer=1, birth_date="14 Mart 1988"):
    call = client.post("/api/calls", json={"customer": customer}).raise_for_status().json()
    call = say(client, call, "Evet").raise_for_status().json()
    return say(client, call, birth_date).raise_for_status().json()


def test_reset_while_tts_is_running(client, monkeypatch):
    started, release = Event(), Event()

    def delayed(_):
        started.set()
        assert release.wait(3)
        return b"old audio"

    monkeypatch.setattr(voice, "speak", delayed)
    cid = client.post("/api/calls", json={"customer": 1}).json()["id"]
    path = f"/api/calls/{cid}"
    with ThreadPoolExecutor(1) as pool:
        request = pool.submit(client.get, path + "/speech/0")
        assert started.wait(3)
        events = client.get(path).json()["events"]
        assert (
            events[-1]["component"] == "Piper / Antalia"
            and events[-1]["status"] == "working"
        )
        assert client.post("/api/reset").json()["active"] == 1
        release.set()
        assert request.result().status_code == 410
    reopened = client.get(path).raise_for_status().json()
    assert reopened["id"] != cid and reopened["customer"] == 1
    assert reopened["turn"] == 0 and reopened["phase"] == "Görüşme izni"
    assert "kurye beklemeyin" in reopened["messages"][0]["text"]


def test_preference_with_assent_requires_separate_confirmation(client, text_audio, monkeypatch):
    monkeypatch.setattr(voice, "ask_qwen", lambda *args: {
        "Perşembe": "UNCLEAR", "Cuma": "ACCEPTED",
    })
    initial = client.get("/api/bank").json()
    call = verified_call(client)
    call = say(client, call, "Cuma olsun, onaylıyorum").raise_for_status().json()
    pending = client.get("/api/bank").json()
    assert call["phase"] == "Onay bekleniyor" and not pending["bookings"]
    assert [slot["remaining"] for slot in pending["slots"]] == [
        slot["remaining"] for slot in initial["slots"]
    ]
    assert [slot["held"] for slot in pending["slots"][:2]] == [0, 1]

    done = say(client, call, "Onaylıyorum", key="final-approval").raise_for_status().json()
    assert say(client, call, "Onaylıyorum", key="final-approval").json() == done
    state = client.get("/api/bank").json()
    assert state["bookings"] == [{"id": 1, "customer": 1, "slot": 2}]
    assert [(slot["remaining"], slot["held"]) for slot in state["slots"][:2]] == [(1, 0), (0, 0)]
    assert len([msg for msg in done["messages"] if msg["role"] == "customer"]) == 4


def test_active_customer_has_one_call_even_with_simultaneous_starts(client):
    gate = Barrier(2)

    def start():
        gate.wait(2)
        return client.post("/api/calls", json={"customer": 1})

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(start) for _ in range(2)]
        responses = sorted((job.result() for job in jobs), key=lambda result: result.status_code)
    assert [response.status_code for response in responses] == [200, 409]
    assert "zaten bir görüşme" in responses[1].json()["detail"]
    assert client.get("/api/bank").json()["active"] == 1


def test_customer_cannot_receive_interval_held_in_another_call(client, text_audio):
    first = verified_call(client)
    first = say(client, first, "Perşembe").raise_for_status().json()
    second = verified_call(client, 2, "2 Kasım 1975")
    assert "Cuma" in second["messages"][-1]["text"]
    assert "Perşembe" not in second["messages"][-1]["text"]
    assert not client.get("/api/bank").json()["bookings"]
    done = say(client, first, "Onaylıyorum").raise_for_status().json()
    assert done["phase"] == "Tamamlandı"
    assert client.get("/api/bank").json()["bookings"] == [{"id": 1, "customer": 1, "slot": 1}]


@pytest.mark.parametrize("interrupt", ["end", "reset"])
def test_interrupt_during_approval_starts_next_call_at_zero(client, text_audio, monkeypatch, interrupt):
    call = verified_call(client)
    call = say(client, call, "Cuma").raise_for_status().json()
    path = f"/api/calls/{call['id']}"
    started, release = Event(), Event()
    transcribe = voice.transcribe

    def delayed(blob, phase):
        if phase == "confirm":
            started.set()
            assert release.wait(3)
        return transcribe(blob, phase)

    monkeypatch.setattr(voice, "transcribe", delayed)
    with ThreadPoolExecutor(1) as pool:
        request = pool.submit(say, client, call, "Onaylıyorum")
        try:
            assert started.wait(3)
            if interrupt == "end":
                old_view = client.post(path + "/end").raise_for_status().json()
                assert not old_view["active"] and not old_view["replay_available"]
                new = client.post("/api/calls", json={"customer": 1}).raise_for_status().json()
            else:
                client.post("/api/reset").raise_for_status()
                old_view = new = client.get(path).raise_for_status().json()
                assert "kurye beklemeyin" in new["messages"][0]["text"]
            assert client.get(path + f"/speech/{call['turn'] + 1}").status_code == 410
            assert not any(slot["held"] for slot in client.get("/api/bank").json()["slots"])
            assert new["id"] != call["id"] and new["phase"] == "Görüşme izni"
            assert new["turn"] == 0 and len(new["messages"]) == 1
        finally:
            release.set()
        assert request.result().status_code == 410
    assert client.get(path).json() == old_view
    assert not client.get("/api/bank").json()["bookings"]


def test_reset_invalidates_confirmed_booking_and_explains_restart(client, text_audio, monkeypatch):
    call = verified_call(client)
    call = say(client, call, "Cuma").raise_for_status().json()
    completed = say(client, call, "Onaylıyorum", key="before-reset").raise_for_status().json()
    assert "kaydedildi" in completed["messages"][-1]["text"]

    reset = client.post("/api/reset").raise_for_status().json()
    assert reset["active"] == 0 and not reset["bookings"]
    assert (len(reset["customers"]), len(reset["slots"]), sum(s["remaining"] for s in reset["slots"])) == (8, 5, 7)
    assert say(client, call, "Onaylıyorum", key="before-reset").status_code == 410
    assert client.get("/api/history").json() == []

    path = f"/api/calls/{call['id']}"
    assert client.get(path).json()["restart_required"] is True
    assert client.get("/api/bank").json()["active"] == 0
    opened = client.post(path + "/restart").raise_for_status().json()
    assert client.post(path + "/restart").json() == opened
    assert opened["customer"] == call["customer"] and opened["id"] != call["id"]
    assert opened["turn"] == 0 and opened["phase"] == "Görüşme izni"
    notice = opened["messages"][0]["text"]
    assert all(text in notice for text in ["baştan", "iptal edildi", "kurye beklemeyin"])
    monkeypatch.setattr(voice, "speak", lambda text: text.encode())
    assert client.get(f"/api/calls/{opened['id']}/speech/0").content.decode() == notice
    assert client.post("/api/calls", json={"customer": 1}).status_code == 409
    assert not client.get("/api/bank").json()["bookings"]


def test_restart_keeps_completed_booking_and_archives_pending_call(client, text_audio, monkeypatch):
    from freya import app

    booked = verified_call(client)
    booked = say(client, booked, "Cuma").raise_for_status().json()
    say(client, booked, "Onaylıyorum").raise_for_status()
    pending = verified_call(client, 2, "2 Kasım 1975")
    pending = say(client, pending, "Perşembe").raise_for_status().json()
    assert [slot["held"] for slot in client.get("/api/bank").json()["slots"][:2]] == [1, 0]

    monkeypatch.setattr(app, "engine", Engine(Bank(app.engine.bank.path)))
    state = client.get("/api/bank").json()
    assert state["bookings"] == [{"id": 1, "customer": 1, "slot": 2}]
    assert state["active"] == 0 and not any(slot["held"] for slot in state["slots"])
    archived = client.get(f"/api/calls/{pending['id']}").raise_for_status().json()
    assert archived["phase"] == "Görüşme geçmişi" and not archived["active"]
    assert say(client, pending, "Onaylıyorum").status_code == 410
    restarted = client.post("/api/calls", json={"customer": 2}).raise_for_status().json()
    assert restarted["turn"] == 0 and restarted["id"] != pending["id"]


def test_audio_failure_preserves_bank_result(client, monkeypatch):
    monkeypatch.setattr(voice, "transcribe", lambda b, phase: (b.decode(), 1))
    monkeypatch.setattr(
        voice,
        "classify",
        lambda *args: ({"Cuma": "ACCEPTED"}, {"1": "uygun_değil", "2": "uygun"}),
    )
    cid = client.post("/api/calls", json={"customer": 1}).json()["id"]
    path = f"/api/calls/{cid}"
    for i, phrase in enumerate(["Evet", "14 Mart 1988", "Cuma olur", "Onaylıyorum"]):
        res = client.post(
            path + f"/turn?expected={i}",
            headers={"content-type": "audio/wav"},
            content=phrase.encode(),
        )
        assert res.status_code == 200

    def fail(_):
        raise OSError

    monkeypatch.setattr(voice, "speak", fail)
    assert client.get(path + "/speech/4").status_code == 503
    assert client.get("/api/bank").json()["bookings"][0]["slot"] == 2
    assert client.get(path).json()["events"][-1]["status"] == "error"


def test_client_boundaries(client):
    assert (
        client.post(
            "/api/reset", headers={"Origin": "https://elsewhere.test"}
        ).status_code
        == 403
    )
    assert client.post("/api/calls", json={"customer": 42}).status_code == 400
    cid = client.post("/api/calls", json={"customer": 1}).json()["id"]
    assert (
        client.post(f"/api/calls/{cid}/turn?expected=0", content="hello").status_code
        == 415
    )
    assert client.get(f"/api/calls/{cid}?after=1").json()["events"] == []


def test_demo_birth_date_skip_only_at_verification_step(client, monkeypatch):
    monkeypatch.setattr(voice, "transcribe", lambda blob, phase: (blob.decode(), 1))
    call = client.post("/api/calls", json={"customer": 1}).json()
    path = f"/api/calls/{call['id']}"
    assert not call["can_skip_birth_date"]
    assert client.post(path + "/skip-birth-date?expected=0").status_code == 409

    def say(turn, phrase):
        response = client.post(
            path + f"/turn?expected={turn}",
            headers={"content-type": "audio/wav"},
            content=phrase.encode(),
        )
        assert response.status_code == 200
        return response.json()

    assert say(0, "Evet")["can_skip_birth_date"]
    assert client.post(path + "/skip-birth-date?expected=0").status_code == 409
    skipped = client.post(path + "/skip-birth-date?expected=1").json()
    assert skipped["phase"] == "Tercih bekleniyor"
    assert not skipped["can_skip_birth_date"]
    assert any(event["component"] == "Demo operatörü" for event in skipped["events"])
    assert client.post(path + "/skip-birth-date?expected=2").status_code == 409

    assert say(2, "Cuma")["phase"] == "Onay bekleniyor"
    assert not client.get("/api/bank").json()["bookings"]
    assert say(3, "Onaylıyorum")["phase"] == "Tamamlandı"
    assert len(client.get("/api/bank").json()["bookings"]) == 1


def test_history_keeps_model_output_and_applied_reason_until_reset(client, monkeypatch):
    from freya import app

    monkeypatch.setattr(voice, "transcribe", lambda blob, phase: (blob.decode(), 1))
    monkeypatch.setattr(voice, "interpret_intent", lambda *args: "THIRD_PARTY")
    cid = client.post("/api/calls", json={"customer": 1}).json()["id"]
    path = f"/api/calls/{cid}"
    for turn, text in enumerate(["Evet", "14 Mart 1988", "Cuma", "Tüm nükleer."]):
        assert (
            client.post(
                path + f"/turn?expected={turn}",
                headers={"content-type": "audio/wav"},
                content=text.encode(),
            ).status_code
            == 200
        )

    assert client.get("/api/history").json() == []
    # A new engine simulates a restart while this call still has a pending confirmation.
    monkeypatch.setattr(app, "engine", Engine(Bank(app.engine.bank.path)))

    assert [item["id"] for item in client.get("/api/history").json()] == [cid]
    archived = client.get(path).json()
    assert not archived["active"] and not archived["replay_available"]
    assert any(message["text"] == "Tüm nükleer." for message in archived["messages"])
    observed = next(
        event
        for event in archived["events"]
        if event["component"] == "Qwen" and event["status"] == "ok"
    )
    assert observed["detail"]["model_json"] == {"intent": "THIRD_PARTY"}
    assert observed["detail"]["uygulanan_niyet"] == "UNCLEAR"
    assert "başka bir teslim alıcısı" in observed["detail"]["karar_gerekçesi"]
    tail = client.get(path + f"?after={observed['seq'] - 1}").json()
    assert tail["events"][0] == observed

    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/history").json() == []
    assert client.get(path).status_code == 410
