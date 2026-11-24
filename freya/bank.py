"""The bank owns identity, offers, confirmation and persistent capacity."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

CUSTOMERS = [
    ("Ayşe Kaya", "1988-03-14", "Kadıköy", "Ev"),
    ("Mehmet Demir", "1975-11-02", "Kadıköy", "İş yeri"),
    ("Zeynep Arslan", "1993-06-27", "Kadıköy", "Ev"),
    ("Can Yıldız", "1981-01-09", "Ataşehir", "Ev"),
    ("Elif Şahin", "1990-08-30", "Ataşehir", "İş yeri"),
    ("Burak Çelik", "1969-12-18", "Şişli", "Ev"),
    ("Selin Aydın", "1999-04-05", "Şişli", "Ev"),
    ("Ali Öztürk", "1984-07-21", "Beşiktaş", "İş yeri"),
]
SLOTS = [
    ("Kadıköy", "Perşembe", "09:00–13:00", 1),
    ("Kadıköy", "Cuma", "14:00–18:00", 1),
    ("Ataşehir", "Perşembe", "14:00–18:00", 2),
    ("Şişli", "Cuma", "09:00–13:00", 2),
    ("Beşiktaş", "Perşembe", "09:00–13:00", 1),
]
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, dob TEXT, district TEXT, address TEXT, phone TEXT);
CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY, district TEXT, day TEXT, hours TEXT, capacity INTEGER);
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, customer INTEGER UNIQUE REFERENCES customers(id),
 slot INTEGER REFERENCES slots(id), proposal TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, customer INTEGER REFERENCES customers(id),
 verified INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, open INTEGER DEFAULT 1, offered TEXT DEFAULT '',
 proposal TEXT, proposed_slot INTEGER REFERENCES slots(id));
CREATE TABLE IF NOT EXISTS slot_holds(
 slot INTEGER PRIMARY KEY REFERENCES slots(id),
 call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS slot_holds_by_call ON slot_holds(call_id);
CREATE TABLE IF NOT EXISTS call_trace(
 id INTEGER PRIMARY KEY, call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('event','message')), payload TEXT NOT NULL,
 recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE INDEX IF NOT EXISTS call_trace_by_call ON call_trace(call_id, id);
"""
SLOT_QUERY = """SELECT s.*, count(b.id) used, s.capacity-count(b.id) remaining,
    h.slot IS NOT NULL AS held
FROM slots s LEFT JOIN bookings b ON s.id=b.slot LEFT JOIN slot_holds h ON s.id=h.slot
GROUP BY s.id ORDER BY s.id"""


class BankError(Exception):
    pass


class ActiveCall(BankError):
    pass


class Bank:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)
        with self.connection(True) as db:
            if not db.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
                self.seed(db)
            db.execute("DELETE FROM slot_holds")
            db.execute("UPDATE calls SET open=0")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_open_call_per_customer
                        ON calls(customer) WHERE open=1""")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def seed(db):
        db.executemany(
            "INSERT INTO customers VALUES(?,?,?,?,?,?)",
            [(i, *row, f"053200000{i:02}") for i, row in enumerate(CUSTOMERS, 1)],
        )
        db.executemany(
            "INSERT INTO slots VALUES(?,?,?,?,?)",
            [(i, *s) for i, s in enumerate(SLOTS, 1)],
        )

    def reset(self, restarts=()):
        with self.connection(True) as db:
            for table in ("calls", "bookings", "slots", "customers"):
                db.execute(f"DELETE FROM {table}")
            self.seed(db)
            for cid, customer, message in restarts:
                db.execute(
                    "INSERT INTO calls(id,customer) VALUES(?,?)", (cid, customer)
                )
                self.write_trace(db, cid, "message", message)

    def snapshot(self):
        with self.connection() as db:
            customers = db.execute("""SELECT c.id,c.name,c.district,c.address,'05•• ••• •• '||substr(c.phone,-2) phone,
                b.id booking FROM customers c LEFT JOIN bookings b ON b.customer=c.id ORDER BY c.id""")
            return dict(
                customers=[dict(c) for c in customers],
                slots=[dict(s) for s in db.execute(SLOT_QUERY)],
                bookings=[
                    dict(b)
                    for b in db.execute(
                        "SELECT id,customer,slot FROM bookings ORDER BY id"
                    )
                ],
                active=db.execute("SELECT count(*) FROM calls WHERE open=1").fetchone()[
                    0
                ],
            )

    def start(self, customer):
        cid = uuid4().hex
        with self.connection(True) as db:
            if not db.execute(
                "SELECT 1 FROM customers WHERE id=?", (customer,)
            ).fetchone():
                raise BankError("Müşteri bulunamadı.")
            started = db.execute(
                """INSERT INTO calls(id,customer) VALUES(?,?)
                ON CONFLICT(customer) WHERE open=1 DO NOTHING""",
                (cid, customer),
            )
            if not started.rowcount:
                raise ActiveCall("Bu müşteriyle zaten bir görüşme sürüyor.")
        return cid

    @staticmethod
    def write_trace(db, cid, kind, payload):
        db.execute(
            "INSERT INTO call_trace(call_id,kind,payload) VALUES(?,?,?)",
            (cid, kind, json.dumps(payload, ensure_ascii=False)),
        )

    def record_trace(self, cid, kind, payload):
        with self.connection(True) as db:
            self.write_trace(db, cid, kind, payload)

    def history(self):
        with self.connection() as db:
            return [
                dict(c)
                for c in db.execute("""
                SELECT calls.id, customers.name, min(call_trace.recorded_at) started_at
                FROM calls
                JOIN customers ON customers.id=calls.customer
                JOIN call_trace ON call_trace.call_id=calls.id
                WHERE calls.open=0
                GROUP BY calls.id
                ORDER BY started_at DESC, calls.rowid DESC
            """)
            ]

    def archived_trace(self, cid):
        with self.connection() as db:
            call = db.execute(
                "SELECT customer FROM calls WHERE id=? AND open=0", (cid,)
            ).fetchone()
            if not call:
                return None
            rows = db.execute(
                "SELECT kind,payload FROM call_trace WHERE call_id=? ORDER BY id",
                (cid,),
            )
            trace = {"event": [], "message": []}
            for row in rows:
                trace[row["kind"]].append(json.loads(row["payload"]))
            if not any(trace.values()):
                return None
            return call["customer"], trace["event"], trace["message"]

    @staticmethod
    def call(db, cid, verified=False):
        call = db.execute(
            "SELECT * FROM calls WHERE id=? AND open=1", (cid,)
        ).fetchone()
        if not call or (verified and not call["verified"]):
            raise BankError("Görüşme kapalı veya doğrulama eksik.")
        return call

    def end(self, cid):
        with self.connection(True) as db:
            db.execute("UPDATE calls SET open=0 WHERE id=?", (cid,))
            db.execute("DELETE FROM slot_holds WHERE call_id=?", (cid,))

    def verify(self, cid, dob):
        with self.connection(True) as db:
            c = self.call(db, cid)
            ok = bool(
                db.execute(
                    "SELECT 1 FROM customers WHERE id=? AND dob=?", (c["customer"], dob)
                ).fetchone()
            )
            tries = c["attempts"] + 1
            db.execute(
                "UPDATE calls SET attempts=?,verified=?,open=? WHERE id=?",
                (tries, ok, ok or tries < 2, cid),
            )
            if not ok and tries >= 2:
                db.execute("DELETE FROM slot_holds WHERE call_id=?", (cid,))
            return ok, tries

    def skip_birth_date_for_demo(self, cid):
        with self.connection(True) as db:
            self.call(db, cid)
            db.execute("UPDATE calls SET verified=1 WHERE id=?", (cid,))

    def offers(self, cid, excluded_slots=(), *, allows_slot=None):
        with self.connection(True) as db:
            c = self.call(db, cid, True)
            db.execute("DELETE FROM slot_holds WHERE call_id=?", (cid,))
            db.execute(
                "UPDATE calls SET proposal=NULL,proposed_slot=NULL WHERE id=?",
                (cid,),
            )
            district = db.execute(
                "SELECT district FROM customers WHERE id=?", (c["customer"],)
            ).fetchone()[0]
            existing = db.execute(
                "SELECT s.* FROM bookings b JOIN slots s ON b.slot=s.id WHERE customer=?",
                (c["customer"],),
            ).fetchone()
            if existing:
                return [], dict(existing), False
            eligible = [
                dict(s)
                for s in db.execute(SLOT_QUERY)
                if s["district"] == district
                and s["remaining"] > 0
                and s["id"] not in excluded_slots
                and (allows_slot is None or allows_slot(s))
            ]
            slots = [s for s in eligible if not s["held"]]
            db.executemany(
                "INSERT INTO slot_holds(slot,call_id) VALUES(?,?)",
                [(s["id"], cid) for s in slots],
            )
            return slots, None, bool(eligible and not slots)

    def propose(self, cid, slot):
        token = uuid4().hex
        with self.connection(True) as db:
            self.call(db, cid, True)
            if not db.execute(
                "SELECT 1 FROM slot_holds WHERE slot=? AND call_id=?", (slot, cid)
            ).fetchone():
                raise BankError("Zaman bu müşteriye teklif edilmedi.")
            db.execute(
                "DELETE FROM slot_holds WHERE call_id=? AND slot<>?", (cid, slot)
            )
            db.execute(
                "UPDATE calls SET proposal=?,proposed_slot=? WHERE id=?",
                (token, slot, cid),
            )
        return token

    def confirm(self, cid, token):
        with self.connection(True) as db:
            c = self.call(db, cid, True)
            if not token or c["proposal"] != token:
                raise BankError("Geçerli bir teyit isteği bulunamadı.")
            old = db.execute(
                "SELECT id,slot,proposal FROM bookings WHERE customer=?",
                (c["customer"],),
            ).fetchone()
            if old:
                return {"result": "existing", "booking": old["id"], "slot": old["slot"]}
            slot = db.execute(
                """SELECT s.id,s.capacity-count(b.id) remaining FROM slots s
              JOIN customers c ON c.district=s.district JOIN slot_holds h ON h.slot=s.id
              LEFT JOIN bookings b ON b.slot=s.id
              WHERE c.id=? AND s.id=? AND h.call_id=? GROUP BY s.id""",
                (c["customer"], c["proposed_slot"], cid),
            ).fetchone()
            if not slot:
                raise BankError("Teklif bu görüşmeye ayrılmamış veya ilçe eşleşmedi.")
            before = slot["remaining"]
            if before <= 0:
                return {"result": "full", "slot": slot["id"], "remaining": 0}
            booking = db.execute(
                "INSERT INTO bookings(customer,slot,proposal) VALUES(?,?,?)",
                (c["customer"], slot["id"], token),
            ).lastrowid
            db.execute("DELETE FROM slot_holds WHERE call_id=?", (cid,))
            return {
                "result": "booked",
                "booking": booking,
                "customer": c["customer"],
                "slot": slot["id"],
                "remaining_before": before,
                "remaining_after": before - 1,
            }
