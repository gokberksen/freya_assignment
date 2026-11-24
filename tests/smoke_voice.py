"""Run in a separate Python process. Uses local models and an isolated demo database."""

import os
import tempfile
import subprocess
from pathlib import Path
from statistics import median
from time import monotonic

from fastapi.testclient import TestClient


def main():
    with tempfile.TemporaryDirectory() as temp:
        os.environ["FREYA_DB"] = str(Path(temp) / "voice.sqlite")
        from freya.app import app
        from freya import voice

        client = TestClient(app)
        call = client.post("/api/calls", json={"customer": 1}).json()
        cid = call["id"]
        times = []
        for n, phrase in enumerate(
            (
                "Evet, uygunum.",
                "On dört Mart bin dokuz yüz seksen sekiz.",
                "Perşembe olmaz, cuma öğleden sonra uygunum.",
                "Evet.",
            )
        ):
            customer_audio = Path(temp) / "customer.wav"
            subprocess.run(
                [
                    "say",
                    "-v",
                    "Yelda",
                    "-r",
                    "150",
                    "-o",
                    str(customer_audio),
                    "--data-format=LEI16@16000",
                    phrase,
                ],
                check=True,
            )
            # Exercise the two container formats used by browser recorders.
            if n % 2 == 0:
                codec, muxer, mime, extra = "libopus", "webm", "audio/webm", []
            else:
                codec, muxer, mime = "aac", "mp4", "audio/mp4"
                extra = ["-movflags", "frag_keyframe+empty_moov"]
            data = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(customer_audio),
                    "-c:a",
                    codec,
                    "-f",
                    muxer,
                    *extra,
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            ).stdout
            start = monotonic()
            res = client.post(
                f"/api/calls/{cid}/turn?expected={n}",
                content=data,
                headers={"content-type": mime},
            )
            res.raise_for_status()
            call = res.json()
            audio = client.get(f"/api/calls/{cid}/speech/{n + 1}")
            audio.raise_for_status()
            assert audio.content.startswith(b"RIFF")
            times.append(round(monotonic() - start, 2))
            print(
                n + 1,
                call["phase"],
                times[-1],
                [m["text"] for m in call["messages"] if m["turn"] == n + 1],
                flush=True,
            )
        assert client.get("/api/bank").json()["bookings"] == [
            {"id": 1, "customer": 1, "slot": 2}
        ]
        print("HTTP tur ve yanıt sesi hazır (sn), medyan:", median(times), flush=True)
        slots = client.get("/api/bank").json()["slots"][:2]
        cases = [
            ("Perşembe olmaz, cuma öğleden sonra uygunum.", ["uygun_değil", "uygun"]),
            ("Cuma da perşembe de bana uymaz.", ["uygun_değil", "uygun_değil"]),
            ("Belki cuma gelebilirim, kesin değil.", ["belirsiz", "belirsiz"]),
            ("Perşembe uygunum, cuma gelemem.", ["uygun", "uygun_değil"]),
        ]
        for phrase, expected in cases:
            results = [voice.classify(phrase, slots)[1] for _ in range(3)]
            print(
                phrase,
                sum([r["1"], r["2"]] == expected for r in results),
                "/ 3",
                results[0],
                flush=True,
            )


if __name__ == "__main__":
    main()
