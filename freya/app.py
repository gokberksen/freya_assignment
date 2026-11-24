import os
from time import monotonic
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import voice
from .bank import ActiveCall, Bank, BankError
from .engine import Engine, Expired, Occupied, RestartRequired, elapsed

engine = Engine(Bank(os.getenv("FREYA_DB", voice.ROOT / ".runtime/bank.sqlite")))
app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method != "GET"
        and origin
        and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "Yerel panelden işlem yapın."}, status_code=403)
    res = await call_next(request)
    if request.url.path.startswith("/api/"):
        res.headers["Cache-Control"] = "no-store"
    return res


@app.exception_handler(Expired)
async def expired(request, exc):
    return JSONResponse({"detail": "Görüşme geçersiz veya kapalı."}, status_code=410)


@app.exception_handler(RestartRequired)
async def restart_required(request, exc):
    return JSONResponse(
        {
            "detail": "Önceki randevu iptal edildi. Görüşme yeniden başlatılmalı.",
            "restart_required": True,
        },
        status_code=410,
    )


@app.exception_handler(Occupied)
async def occupied(request, exc):
    return JSONResponse(
        {"detail": "Tur zaten işlendi veya devam ediyor."}, status_code=409
    )


@app.exception_handler(BankError)
async def bank_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ActiveCall)
async def active_call(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


class Start(BaseModel):
    customer: int


class Device(BaseModel):
    kind: Literal["playing", "played", "blocked", "mic_error", "recording"]
    turn: int
    duration_ms: int | None = None


@app.get("/api/bank")
def snapshot():
    return engine.bank.snapshot()


@app.post("/api/reset")
def reset():
    with engine.lock:
        engine.reset()
        return snapshot()


@app.post("/api/calls")
def start(data: Start):
    with engine.lock:
        return engine.view(engine.start(data.customer))


@app.get("/api/history")
def history():
    return engine.bank.history()


@app.get("/api/calls/{cid}")
def view(cid: str, after: int = 0):
    return engine.view_by_id(cid, max(0, after))


@app.post("/api/calls/{cid}/restart")
def restart(cid: str):
    return engine.restart_after_reset(cid)


@app.post("/api/calls/{cid}/end")
def end(cid: str):
    with engine.lock:
        c = engine.get(cid)
        if c.active:
            engine.finish(c, "Operatör görüşmeyi bitirdi.")
        c.ended_by_operator = True
        c.audio.clear()
        return engine.view(c)


@app.post("/api/calls/{cid}/skip-birth-date")
def skip_birth_date(cid: str, expected: int):
    return engine.skip_birth_date(cid, expected)


@app.post("/api/calls/{cid}/turn")
async def turn(cid: str, expected: int, request: Request):
    mime = request.headers.get("content-type", "").split(";")[0]
    if mime not in {
        "audio/webm",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "video/mp4",
    }:
        raise HTTPException(415, "Desteklenmeyen ses biçimi.")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 4_000_000:
            raise HTTPException(413, "Ses kaydı çok büyük.")
    key = request.headers.get("idempotency-key")
    if key and len(key) > 80:
        raise HTTPException(400, "İstek anahtarı geçersiz.")
    return await run_in_threadpool(
        engine.process, cid, expected, bytes(chunks), mime, key
    )


@app.get("/api/calls/{cid}/speech/{turn}")
def speech(cid: str, turn: int):
    with engine.lock:
        c = engine.get(cid)
        if turn != c.turn or c.ended_by_operator:
            raise Expired
        msg = next(
            (
                m
                for m in reversed(c.messages)
                if m["turn"] == turn and m["role"] == "assistant"
            ),
            None,
        )
        if not msg:
            raise HTTPException(404)
        if turn in c.audio:
            return Response(c.audio[turn], media_type="audio/wav")
        step = engine.event(
            c, "Piper / Antalia", "Yanıt seslendiriliyor…", status="working"
        )
    start = monotonic()
    try:
        data = voice.speak(msg["text"])
    except Exception:
        with engine.lock:
            engine.get(cid)
            if c.turn != turn or c.ended_by_operator:
                raise Expired
            engine.event(
                c,
                "Piper / Antalia",
                "Ses üretilemedi. Yazılı yanıt ekranda.",
                status="error",
                step=step,
                ms=elapsed(start),
                turn=turn,
            )
        raise HTTPException(503, "Asistan sesi üretilemedi.")
    with engine.lock:
        engine.get(cid)
        if c.turn != turn or c.ended_by_operator:
            raise Expired
        c.audio = {turn: data}
        engine.event(
            c,
            "Piper / Antalia",
            "Yanıt sesi hazır.",
            {"metin": msg["text"], "biçim": "WAV"},
            step=step,
            ms=elapsed(start),
        )
    return Response(data, media_type="audio/wav")


@app.post("/api/calls/{cid}/device")
def device(cid: str, data: Device):
    with engine.lock:
        c = engine.get(cid)
        if data.turn != c.turn or c.ended_by_operator:
            raise Expired
        labels = {
            "playing": "Ses tarayıcıda oynatılıyor.",
            "played": "Ses oynatma tamamlandı.",
            "recording": "Mikrofon kaydı başladı.",
            "blocked": "Ses oynatılamadı. Elle tekrar deneyin.",
            "mic_error": "Mikrofon erişimi veya kaydı başarısız.",
        }
        engine.event(
            c,
            "Mikrofon" if data.kind in {"mic_error", "recording"} else "Tarayıcı",
            labels[data.kind],
            status="error" if data.kind in {"mic_error", "blocked"} else "ok",
            turn=c.turn + 1 if data.kind == "recording" else c.turn,
            ms=data.duration_ms,
        )
        return {"ok": True}


app.mount("/", StaticFiles(directory=voice.ROOT / "web", html=True), name="panel")
