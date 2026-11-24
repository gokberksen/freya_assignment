"""Preferences cited by the customer during a single delivery call."""

import re
from dataclasses import dataclass, field

from .voice import words

WEEKDAYS = re.compile(
    r"\b(pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar)"
    r"(?:ya|ye|yı|yi|yü|nın|nin|nün|nı|ni)?\b"
)
CLOCKS = re.compile(r"\b(\d{1,2})(?: 00)? (?:ile|ve) (\d{1,2})(?: 00)?\b")
PERIODS = {
    "sabah": (0, 13),
    "öğleden sonra": (13, 24),
    "dokuz ile on üç": (9, 13),
    "on dört ile on sekiz": (14, 18),
}
CORRECTION = re.compile(
    r"\b(?:fikrimi değiştirdim|vazgeçtim|düzeltiyorum|düzeltmek istiyorum|"
    r"yanlış söyledim|artık|önce\w* .{1,80} şimdi)\b"
)
ONLY = re.compile(r"\b(?:sadece|yalnızca|yalnız)\b")
PAST_CORRECTION = re.compile(
    r"\b(?:daha önce|az önce|önce) (?P<before>.+?) "
    r"(?:demiştim|söylemiştim) (?:ama |fakat )?şimdi (?P<after>.+)"
)
UNCERTAIN = re.compile(
    r"\b(?:belki|emin değilim|kesin değil|bilmiyorum|olabilir|eğer|"
    r"olursa|değilse|şartıyla|gerekirse|mı|mi|mu|mü)\b"
)
INJECTION = re.compile(r"\b(?:talimat|sınıflandırıcı|approve)\b")
DECLARATION = re.compile(
    r"\b(?:uygun değil(?:im)?|müsait değil(?:im)?|evde değilim|"
    r"evde olmayacağım|müsait olmayacağım|evde olacağım|evde olurum|müsait olacağım|"
    r"olmaz|uymaz|uymuyor|gelemem|istemiyorum|uygun(?:um|dur)?|"
    r"müsaitim|evdeyim|uyar|olur|tercih ederim|tercih ediyorum|istiyorum)\b"
)
NEGATIVE = re.compile(r"değil|olmaz|olmayacağım|uymaz|uymuyor|gelemem|istemiyorum")
ADDRESS_CHANGE = re.compile(
    r"\b(?:(?:başka|farklı|yeni) (?:bir )?adres\w*|"
    r"adres(?:imi|i|im) değiş\w*|(?:iş yerine|ofise|eve) (?:gönder\w*|getir\w*))\b"
)
REGISTERED_ADDRESS = re.compile(
    r"\bkayıtlı adres\w* (?:uygun|olur|kalsın|gönder\w*|getir\w*|teslim ed\w*)\b"
)
SELF_HANDOVER = re.compile(
    r"\b(?:ben(?: kendim)?|kendim) (?:teslim )?alacağım\b"
    r"|\bkartı (?:ben )?(?:kendim )?teslim alacağım\b"
)
CALLBACK = re.compile(
    r"\b(?:tekrar arayın|geri arayın|sonra arayın|"
    r"sonra konuşalım|(?:yarın|akşam|daha sonra) arayın)\b"
)


@dataclass(frozen=True)
class DeliveryTime:
    day: str = ""
    hours: tuple[int, int] | None = None

    @classmethod
    def from_slot(cls, slot):
        start, end = re.findall(r"(\d{2}):\d{2}", slot["hours"])
        return cls(words(slot["day"]), (int(start), int(end)))

    def overlaps(self, other):
        return not (self.day and other.day and self.day != other.day) and (
            not self.hours or not other.hours
            or max(self.hours[0], other.hours[0]) < min(self.hours[1], other.hours[1])
        )

    def includes(self, other):
        return (not self.day or self.day == other.day) and (
            not self.hours or (
                other.hours and self.hours[0] <= other.hours[0]
                and other.hours[1] <= self.hours[1]
            )
        )

    def label(self):
        start_end = ""
        if self.hours:
            start, end = self.hours
            start_end = {(0, 13): "sabah", (13, 24): "öğleden sonra"}.get(
                self.hours, f"{start:02d}:00–{end:02d}:00"
            )
        return " ".join(part for part in (self.day.capitalize(), start_end) if part)


@dataclass(frozen=True)
class Availability:
    time: DeliveryTime
    available: bool
    turn: int
    revised: bool = False
    only: bool = False


def availability_in(transcript, turn):
    transcript = re.sub(r"(?<=\d)\s*[–—-]\s*(?=\d)", " ile ", transcript)
    text = words(transcript)
    if "?" in transcript or UNCERTAIN.search(text) or INJECTION.search(text):
        return []
    past_correction = PAST_CORRECTION.fullmatch(text)
    if past_correction:
        before, after = past_correction.group("before", "after")
        if not WEEKDAYS.search(after) and len(set(WEEKDAYS.findall(before))) == 1:
            day = WEEKDAYS.search(before).group(1)
            period = next((phrase for phrase in PERIODS if phrase in before), "")
            clock = CLOCKS.search(before)
            if CLOCKS.search(after) or any(phrase in after for phrase in PERIODS):
                period = ""
            elif clock:
                period = clock.group()
            after = f"{day} {period} {after}"
        transcript = "Düzeltiyorum, " + after
    clauses = re.split(r"[,.;!?]|\b(?:ama|fakat|ancak)\b", transcript, flags=re.I)
    facts = []
    revision_follows = False
    for clause in clauses:
        clause = words(clause)
        if not clause:
            continue
        days = set(WEEKDAYS.findall(clause))
        statements = list(DECLARATION.finditer(clause))
        revised = revision_follows or bool(CORRECTION.search(clause))
        revision_follows = bool(CORRECTION.fullmatch(clause))
        only = bool(ONLY.search(clause))
        if len(days) > 1 or len(statements) > 1 or (not statements and not only):
            continue
        hours = next((hours for phrase, hours in PERIODS.items() if phrase in clause), None)
        clock = CLOCKS.search(clause)
        if clock:
            start, end = map(int, clock.groups())
            if not 0 <= start < end <= 24:
                continue
            hours = (start, end)
        if days or hours:
            available = not statements or not NEGATIVE.search(statements[0].group())
            if re.search(r"\b(?:hariç|dışında)\b", clause):
                if not available or not days:
                    continue
                available = False
            facts.append(
                Availability(
                    DeliveryTime(next(iter(days), ""), hours),
                    available,
                    turn,
                    revised,
                    available and only,
                )
            )
    return facts


@dataclass
class DeliveryMemory:
    availability: list[Availability] = field(default_factory=list)
    requests: dict[str, tuple[int, str]] = field(default_factory=dict)
    recent: list[tuple[int, str]] = field(default_factory=list)
    limit: Availability | None = None
    clarification: DeliveryTime | None = None

    def within_limit(self, time):
        return (
            not self.limit
            or self.limit.time.includes(time)
            or any(
                fact.available and fact.revised and fact.turn >= self.limit.turn
                and fact.time.includes(time)
                for fact in self.availability
            )
        )

    def allows(self, slot):
        time = DeliveryTime.from_slot(slot)
        if not self.within_limit(time):
            return False
        # A positive revision releases a rejection only for slots it fully covers.
        for fact in reversed(self.availability):
            if fact.available and fact.time.includes(time):
                return True
            if not fact.available and fact.time.overlaps(time):
                return False
        return True

    def prefers(self, slot):
        time = DeliveryTime.from_slot(slot)
        return any(fact.available and fact.time.includes(time) for fact in self.availability)

    def conflict(self, facts):
        for index, fact in enumerate(facts):
            if (
                fact.available and not fact.revised and not self.within_limit(fact.time)
            ):
                return fact
            prior = [*self.availability, *facts[:index]]
            for previous in reversed(prior):
                if (
                    fact.only and previous.available and not fact.revised
                    and not fact.time.overlaps(previous.time)
                ):
                    return fact
                if not fact.time.overlaps(previous.time):
                    continue
                if previous.available != fact.available and (
                    not fact.revised or previous.turn == fact.turn
                ):
                    return fact
                if previous.time.includes(fact.time):
                    break
        return None

    def add(self, facts):
        for fact in facts:
            if fact.only:
                self.limit = fact
            self.availability[:] = [
                previous for previous in self.availability
                if not fact.time.includes(previous.time)
                or (fact.available and not fact.revised and previous.revised)
            ]
            self.availability.append(fact)

    def context(self):
        return {
            "uygunluklar": [
                {"zaman": f.time.label(), "uygun": f.available, "tur": f.turn}
                for f in self.availability
            ],
            "talepler": {
                kind: {"tur": turn, "ifade": text}
                for kind, (turn, text) in self.requests.items()
            },
            "yalnızca": self.limit.time.label() if self.limit else None,
            "açıklama_beklenen_zaman": (
                self.clarification.label() if self.clarification else None
            ),
            "önceki_yanıtlar": [
                {"tur": turn, "ifade": text} for turn, text in self.recent
            ],
        }

    def remember_reply(self, turn, transcript):
        self.recent.append((turn, transcript[:300]))
        del self.recent[:-6]
