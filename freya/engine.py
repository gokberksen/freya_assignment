import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from . import memory, voice
from .bank import BankError
from .memory import WEEKDAYS

YES = {
    "evet",
    "evet uygunum",
    "evet müsaitim",
    "evet konuşabiliriz",
    "konuşabiliriz",
    "müsaitim",
    "evet konuşmamız uygun",
    "şu anda konuşmamız uygun",
    "evet şu anda konuşmamız uygun",
    "uygun",
    "uygunum",
    "tabii",
    "olur",
    "tabi",
    "buyurun",
    "buyrun",
}
CONFIRM = {
    "evet",
    "kesinlikle",
    "evet kesinlikle",
    "tabii",
    "tabii ki",
    "elbette",
    "olur",
    "uygun",
    "uygundur",
    "bana uygun",
    "bana uygundur",
    "benim için uygun",
    "benim için uygundur",
    "bu benim için uygun",
    "bu benim için uygundur",
    "onaylıyorum",
    "evet onaylıyorum",
    "randevuyu onaylıyorum",
    "evet randevuyu onaylıyorum",
    "onay veriyorum",
    "onu alıyorum",
    "evet onu alıyorum",
    "onu ayırabilirsiniz",
    "randevuyu alıyorum",
}
NO = {
    "hayır",
    "istemiyorum",
    "hayır istemiyorum",
    "uygun değilim",
    "müsait değilim",
    "yanlış numara",
    "yanlış kişi",
}
PHASES = {
    "consent": "Görüşme izni",
    "verify": "Kimlik kontrolü",
    "choose": "Tercih bekleniyor",
    "confirm": "Onay bekleniyor",
    "closed": "Tamamlandı",
}
GREETING = "Merhaba, ben bankanın sesli asistanıyım. Şu an konuşmanız uygun mu?"
RESET_GREETING = (
    "Teknik bir hata nedeniyle görüşmeyi baştan başlatıyorum. "
    "Önceki randevu onayınız varsa iptal edildi. Lütfen kurye beklemeyin. "
    "Şu an konuşmanız uygun mu?"
)


class Expired(Exception):
    pass


class RestartRequired(Expired):
    pass


class Occupied(Exception):
    pass


@dataclass
class Call:
    id: str
    customer: int
    phase: str = "consent"
    turn: int = 0
    events: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    selected: dict = field(default_factory=dict)
    delivery: memory.DeliveryMemory = field(default_factory=memory.DeliveryMemory)
    token: str = ""
    active: bool = True
    turn_lock: Lock = field(default_factory=Lock)
    audio: dict = field(default_factory=dict)
    completed_requests: dict = field(default_factory=dict)
    ended_by_operator: bool = False
    birth_date_skipped: bool = False
    booking_confirmed: bool = False


def label(slot):
    return f"{slot['day']} {slot['hours']}"


def spoken(slot):
    hours = (
        "dokuz ile on üç" if slot["hours"].startswith("09") else "on dört ile on sekiz"
    )
    return f"{slot['day']} günü saat {hours} arası"


THIRD_PARTY = (
    "komşu",
    "eşim",
    "annem",
    "babam",
    "resepsiyon",
    "kapıcı",
    "başkası",
    "arkadaşım",
    "abim",
    "ablam",
    "kardeşim",
    "oğlum",
    "kızım",
    "yakınım",
    "başkasına",
)
TENTATIVE = ("belki", "kesin değil", "emin değil", "olabilir", "bilmiyorum")
ORDINAL_OPTIONS = (
    r"(?:ilk|birinci(?:yi|ye|si|sini)?)",
    r"(?:ikinci(?:yi|ye|si|sini)?|sonraki)",
)
OPTION_QUALIFIER = (
    r"(?: seçeneği(?:ni)?| seçenek| dediğiniz(?:i)?| söylediğiniz(?:i)?)?"
)
OPTION_AGREEMENT = (
    r"(?: (?:benim için |bana )?(?:uygun(?:dur|um)?|uyar|olur)"
    r"| (?:olsun|tercih ederim|istiyorum))?"
)
QUESTION = re.compile(r"\b(mı|mi|mu|mü|mısınız|misiniz|musunuz|müsünüz)\b")
AGREEMENT = re.compile(
    r"\b(?:uygun(?:um|dur)|uygun|uyar|müsaitim|kesinlikle|elbette|tabii|"
    r"olur(?:um)?|kabul ediyorum|onay(?:lıyorum| veriyorum)|"
    r"tamam(?:dır)?|anlaştık|sorun yok|alıyorum|(?:ayır|kaydet|kayded|oluştur)(?:in|ebilirsiniz|abilirsiniz)|"
    r"dinliyorum|dinleyebilirim|konuşabiliriz|başlayabiliriz|buyurun|buyrun)\b"
)
REJECTION = re.compile(
    r"\b(?:hayır|olmaz|uymaz|uymuyor|uygun değil\w*|müsait değil\w*|"
    r"gelemem|istemiyorum|onaylamıyorum|vazgeçtim|kabul etmiyorum)\b"
)
REJECT_ALL = re.compile(
    r"(?:hayır )?(?:(?:bu|o|saydığınız|sunduğunuz) "
    r"(?:tarihler|tazihler|saatler|günler|seçenekler)|"
    r"bu seçeneklerin hiçbiri|hiçbiri|ikisi de) "
    r"(?:(?:benim için|bana) )?(?:uygun değil|uymaz|uymuyor|olmaz)"
)
CHANGE_REQUEST = re.compile(
    r"\b(?:eğer|şartıyla|koşuluyla|olursa|değilse|\w+(?:abilir|ebilir)seniz|"
    r"başka (?:bir )?(?:gün|saat|zaman)|(?:günü|saati) değiştir\w*)\b"
)
UNCERTAINTY = re.compile(r"\b(?:belki|emin değilim|kesin değil|bilmiyorum|olabilir)\b")
HANDOVER = re.compile(
    r"\b(?:alsın|alabilir|teslim alır|teslim edin|verin|bırakın|verebilir\w*|bırakabilir\w*)\b"
)
INTENT_LABELS = {
    "CONTINUE": "Görüşmeye devam etmek istiyor",
    "LATER": "Daha sonra konuşmak istiyor",
    "DECLINE": "Reddetti",
    "DECLINE_ALL": "Sunulan zamanların tümünü reddetti",
    "APPROVE": "Sunulan randevuyu onayladı",
    "CHANGE": "Farklı bir seçenek istiyor",
    "THIRD_PARTY": "Başkasına teslim istiyor",
    "UNCLEAR": "Yanıt belirsiz",
}


# A request to keep it brief can still contain explicit permission to continue.
BRIEF_CONSENT = re.compile(
    r"(?:(?:şu an |şu anda )?(?:çok |fazla |pek )?(?:vaktim|zamanım) yok "
    r"(?:ama|fakat|yine de) |(?:kısa|hızlı) (?:olursa|olacaksa) )?"
    r"(?:(?:evet|olur|tabii|tamam) )?"
    r"(?:(?:şu an|şu anda|şimdi) )?"
    r"(?:uygunum|müsaitim|konuşabiliriz|sizi dinliyorum|buyurun|buyrun)"
)


def mentions_third_party(transcript):
    return any(word.startswith(THIRD_PARTY) for word in voice.words(transcript).split())


def asks_third_party_delivery(transcript):
    return mentions_third_party(transcript) and bool(
        HANDOVER.search(voice.words(transcript))
    )


def option_indices(text):
    return {
        index
        for index, ordinal in enumerate(ORDINAL_OPTIONS)
        if re.search(rf"\b{ordinal}\b", text)
    }


def confirmation_question(slot):
    return spoken(slot) + " için randevuyu onaylıyor musunuz?"


def availability_question(slots):
    options = " veya ".join(spoken(slot) for slot in slots)
    question = "Bu saatler size uyar mı?" if len(slots) == 1 else "Size hangisi uyar?"
    return f"Teslimat için {options} uygun. {question}"


def match_phrase(phase, transcript, offers):
    text = voice.words(transcript)
    if phase == "consent":
        if text in YES or BRIEF_CONSENT.fullmatch(text):
            return "CONTINUE"
        if text in NO or "yanlış numara" in text:
            return "DECLINE"
        if text in {"sonra konuşalım", "şu an müsait değilim", "şu an meşgulüm"}:
            return "LATER"
    if phase in {"choose", "confirm"} and asks_third_party_delivery(transcript):
        return "THIRD_PARTY"
    if (
        phase in {"choose", "confirm"}
        and REJECT_ALL.fullmatch(text)
        and "?" not in transcript
    ):
        return "DECLINE_ALL"
    if phase == "confirm":
        if text in CONFIRM and "?" not in transcript:
            return "APPROVE"
        if text in NO or text in {"onaylamıyorum", "vazgeçtim"}:
            return "DECLINE"
    if phase == "choose" and "?" not in transcript:
        for index, slot in enumerate(offers):
            day = voice.words(slot["day"])
            if index < len(ORDINAL_OPTIONS):
                ordinal = ORDINAL_OPTIONS[index]
                if re.fullmatch(
                    ordinal + OPTION_QUALIFIER + OPTION_AGREEMENT,
                    text,
                ):
                    return {
                        str(s["id"]): "uygun" if s == slot else "belirsiz"
                        for s in offers
                    }
            if text in {day, f"{day} uygun", f"{day} bana uygun", f"{day} olur"}:
                return {
                    str(s["id"]): "uygun" if s == slot else "belirsiz" for s in offers
                }
    return None


def confirmation_override(transcript, proposal, decision, offers):
    """Check that a model decision can be supported by the current answer."""
    text = voice.words(transcript)
    if decision not in {"APPROVE", "CHANGE", "DECLINE"}:
        return None
    if re.search(r"\b(talimat|sınıflandırıcı|approve)\b", text):
        return "UNCLEAR", "Yanıt işlem talimatı içeriyor."
    proposed_day = voice.words(proposal["day"])
    mentioned_days = set(WEEKDAYS.findall(text))
    other_day = bool(mentioned_days and proposed_day not in mentioned_days)
    ordinals = option_indices(text)
    if any(index >= len(offers) for index in ordinals):
        return (
            "UNCLEAR",
            "Yanıttaki sıra numarası sunulan bir seçeneğe karşılık gelmiyor.",
        )
    ordinal_slots = {offers[index]["id"] for index in ordinals}
    if len(ordinal_slots) > 1:
        return "UNCLEAR", "Yanıtta birden fazla seçenek sırası belirtiliyor."
    other_option = bool(ordinal_slots and proposal["id"] not in ordinal_slots)
    proposal_rejected = bool(
        REJECTION.search(text)
        or re.search(rf"\b{proposed_day}(?: günü)? değil\b", text)
    )
    period = "sabah" if proposal["hours"].startswith("09") else "öğleden sonra"
    other_period = "öğleden sonra" if period == "sabah" else "sabah"
    different_period = bool(
        re.search(rf"\b{proposed_day}(?: günü)? {other_period}\b", text)
        and period not in text.replace(other_period, "")
    )
    changed = (
        other_day
        or other_option
        or different_period
        or bool(CHANGE_REQUEST.search(text))
    )
    if decision == "CHANGE" and (changed or proposal_rejected):
        return None
    if decision == "DECLINE" and proposal_rejected:
        return None
    if decision != "APPROVE":
        return "UNCLEAR", "Yanıtta açık ret veya teklif değişikliği doğrulanamadı."
    if "?" in transcript or QUESTION.search(text):
        return "UNCLEAR", "Yanıt soru içeriyor."
    if changed or proposal_rejected:
        return "CHANGE", "Yanıtta başka bir zaman, ret veya koşul var."
    if UNCERTAINTY.search(text):
        return "UNCLEAR", "Yanıt kesinlik belirtmiyor."
    if not AGREEMENT.search(text):
        return "UNCLEAR", "Yanıtta bekleyen randevuya açık bir kabul bulunamadı."
    return None


def consent_override(transcript, decision):
    text = voice.words(transcript)
    if decision == "CONTINUE" and (
        "?" in transcript
        or QUESTION.search(text)
        or re.search(r"\b(?:yarın|sonra konuşalım|geri arayın|şu an olmaz)\b", text)
        or not (AGREEMENT.search(text) or re.match(r"evet\b", text))
    ):
        return "UNCLEAR", "Şu an görüşmeye izin verildiği doğrulanamadı."
    return None


class Engine:
    def __init__(self, bank):
        self.bank = bank
        self.lock = RLock()
        self.calls = {}
        self.restarted_calls = {}
        # A closed booking panel restarts only if a client is still viewing it.
        self.cancelled_bookings = {}

    def get(self, cid):
        if cid not in self.calls:
            raise Expired
        return self.calls[cid]

    def valid(self, c):
        if self.get(c.id) is not c or not c.active:
            raise Expired

    def event(
        self,
        c,
        component,
        summary,
        detail=None,
        status="ok",
        ms=None,
        step=None,
        turn=None,
    ):
        with self.lock:
            self.get(c.id)
            seq = len(c.events) + 1
            entry = dict(
                seq=seq,
                step=step or seq,
                turn=c.turn if turn is None else turn,
                component=component,
                summary=summary,
                detail=detail,
                status=status,
                ms=ms,
                at=datetime.now(UTC).isoformat(),
            )
            self.bank.record_trace(c.id, "event", entry)
            c.events.append(entry)
            return seq

    def start(self, customer):
        with self.lock:
            cancelled = [
                cid
                for cid, owner in self.cancelled_bookings.items()
                if owner == customer
            ]
            c = Call(self.bank.start(customer), customer)
            self.calls[c.id] = c
            self.event(
                c,
                "Banka servisi",
                "Müşteri kaydına bağlı görüşme açıldı.",
                {"müşteri": customer},
            )
            self.reply(c, RESET_GREETING if cancelled else GREETING)
            for cid in cancelled:
                self.restarted_calls[cid] = c.id
                del self.cancelled_bookings[cid]
            return c

    def reply(self, c, text):
        if c.phase == "closed":
            text += " İyi günler dilerim."
        self.message(c, "assistant", text)

    def message(self, c, role, text):
        entry = dict(turn=c.turn, role=role, text=text)
        self.bank.record_trace(c.id, "message", entry)
        c.messages.append(entry)

    def change(self, c, phase, why):
        self.event(
            c,
            "Konuşma yöneticisi",
            why,
            {"önce": PHASES[c.phase], "sonra": PHASES[phase]},
        )
        c.phase = phase

    def finish(self, c, why):
        self.bank.end(c.id)
        c.active = False
        self.change(c, "closed", why)

    def reset(self):
        with self.lock:
            replacements = {}
            affected = dict(self.cancelled_bookings)
            for call in self.calls.values():
                if call.active:
                    new = Call(uuid4().hex, call.customer)
                    new.messages.append(
                        dict(turn=0, role="assistant", text=RESET_GREETING)
                    )
                    replacements[call.customer] = new
                if call.active or call.booking_confirmed:
                    affected[call.id] = call.customer
            for old, current in self.restarted_calls.items():
                if current in affected:
                    affected[old] = affected[current]
            self.bank.reset(
                (c.id, c.customer, c.messages[0]) for c in replacements.values()
            )
            self.restarted_calls = {
                old: replacements[customer].id
                for old, customer in affected.items()
                if customer in replacements
            }
            self.cancelled_bookings = {
                old: customer
                for old, customer in affected.items()
                if customer not in replacements
            }
            self.calls = {c.id: c for c in replacements.values()}

    def restart_after_reset(self, cid):
        with self.lock:
            if cid in self.restarted_calls:
                return self.view(self.get(self.restarted_calls[cid]))
            if cid not in self.cancelled_bookings:
                raise Expired
            return self.view(self.start(self.cancelled_bookings[cid]))

    def view(self, c, after=0):
        with self.lock:
            self.get(c.id)
            return dict(
                id=c.id,
                customer=c.customer,
                phase=PHASES[c.phase],
                active=c.active,
                can_skip_birth_date=c.active and c.phase == "verify",
                replay_available=not c.ended_by_operator,
                turn=c.turn,
                events=c.events[after:],
                messages=list(c.messages),
            )

    def view_by_id(self, cid, after=0):
        with self.lock:
            if cid in self.restarted_calls:
                return self.view(self.get(self.restarted_calls[cid]))
            if cid in self.cancelled_bookings:
                raise RestartRequired
            if cid in self.calls:
                return self.view(self.calls[cid], after)
            archive = self.bank.archived_trace(cid)
            if archive is None:
                raise Expired
            customer, events, messages = archive
            turn = max((entry["turn"] for entry in events + messages), default=0)
            return dict(
                id=cid,
                customer=customer,
                phase="Görüşme geçmişi",
                active=False,
                can_skip_birth_date=False,
                replay_available=False,
                turn=turn,
                events=events[after:],
                messages=messages,
            )

    def skip_birth_date(self, cid, expected):
        with self.lock:
            c = self.get(cid)
            self.valid(c)
            if c.turn != expected or c.turn_lock.locked() or c.phase != "verify":
                raise Occupied
            self.bank.skip_birth_date_for_demo(c.id)
            c.birth_date_skipped = True
            c.turn += 1
            self.event(c, "Demo operatörü", "Doğum tarihi kontrolü demoda atlandı.")
            self.reply(c, self.offer(c))
            return self.view(c)

    def offer(self, c, prefix=""):
        start = monotonic()
        c.slots, existing, temporarily_held = self.bank.offers(
            c.id, allows_slot=c.delivery.allows
        )
        c.slots.sort(key=c.delivery.prefers, reverse=True)
        self.event(
            c,
            "Banka servisi",
            "Randevu, ilçe ve kalan yer kontrol edildi. Sunulan aralıklar geçici ayrıldı.",
            {
                "okunan": ["customers", "bookings", "slots", "slot_holds"],
                "seçenekler": [label(s) for s in c.slots],
                "mevcut_randevu": label(existing) if existing else None,
                "başka_görüşmede_ayrılmış": temporarily_held,
            },
            ms=elapsed(start),
        )
        c.token = ""
        if existing:
            c.booking_confirmed = True
            self.finish(c, "Mevcut randevu korundu.")
            return f"Kayıtlı randevunuz {spoken(existing)}. Kartı kimliğinizle kendiniz teslim alabilirsiniz."
        if not c.slots:
            if temporarily_held:
                self.finish(
                    c, "Uygun aralıklar geçici olarak başka görüşmelere ayrıldı."
                )
                return prefix + (
                    "Uygun saatler şu anda başka görüşmeler için ayrıldı. "
                    "Randevu oluşturulmadı. Daha sonra tekrar deneyebilirsiniz."
                )
            if c.delivery.limit:
                self.change(c, "choose", "Müşterinin saat sınırına uyan teklif yok.")
                return prefix + (
                    "Belirttiğiniz gün ve saatler için şu anda teslimat seçeneği yok. "
                    "Uygunluğunuz değiştiyse yeni günü veya saat aralığını söyleyebilirsiniz."
                )
            if any(not fact.available for fact in c.delivery.availability):
                self.finish(c, "Reddedilen aralıklar dışında teklif kalmadı.")
                return (
                    prefix
                    + "Şu anda teklif edebileceğim başka bir teslimat aralığı yok."
                )
            self.finish(c, "İlçede uygun yer kalmadı.")
            return (
                prefix + "Bu hafta ilçenizde uygun yer kalmadı. Randevu oluşturulmadı."
            )
        self.change(c, "choose", "Güncel seçenekler müşteriye sunuldu.")
        return prefix + availability_question(c.slots)

    def update_delivery_memory(self, c, transcript, decision):
        text = voice.words(transcript)
        delivery = c.delivery
        delivery.remember_reply(c.turn, transcript)
        if memory.INJECTION.search(text):
            return None

        previous_requests = set(delivery.requests)
        revised_request = bool(memory.CORRECTION.search(text))
        if decision == "THIRD_PARTY" and (
            "teslim_alıcısı" not in delivery.requests or revised_request
        ):
            delivery.requests["teslim_alıcısı"] = c.turn, transcript[:300]
        if memory.SELF_HANDOVER.search(text) and not (
            mentions_third_party(transcript)
            or "?" in transcript
            or memory.UNCERTAIN.search(text)
            or REJECTION.search(text)
        ):
            delivery.requests.pop("teslim_alıcısı", None)
        if memory.ADDRESS_CHANGE.search(text):
            if "adres" not in delivery.requests or revised_request:
                delivery.requests["adres"] = c.turn, transcript[:300]
        elif memory.REGISTERED_ADDRESS.search(text) and not (
            "?" in transcript
            or memory.UNCERTAIN.search(text)
            or REJECTION.search(text)
        ):
            delivery.requests.pop("adres", None)
        if memory.CALLBACK.search(text):
            delivery.requests["geri_arama"] = c.turn, transcript[:300]
            self.event(
                c, "Görüşme hafızası", "Müşteri tekrar aranmayı istedi.",
                {"talep": delivery.requests["geri_arama"][1]},
            )
            self.finish(c, "Müşteri görüşmeyi başka bir zamana ertelemek istedi.")
            return "Anladım. Teslimat randevusu oluşturmadan görüşmeyi sonlandırıyorum."

        facts = memory.availability_in(transcript, c.turn)
        if (
            c.phase == "choose"
            and "?" not in transcript
            and not memory.UNCERTAIN.search(text)
        ):
            facts.extend(
                memory.Availability(
                    memory.DeliveryTime.from_slot(c.slots[index]), False, c.turn
                )
                for index in option_indices(text)
                if index < len(c.slots)
                and re.search(
                    rf"\b{ORDINAL_OPTIONS[index]}{OPTION_QUALIFIER}"
                    r"(?: benim için| bana)? (?:olmaz|uymaz|uygun değil|istemiyorum)\b",
                    text,
                )
            )
        conflict = delivery.conflict(facts)
        if conflict:
            delivery.clarification = conflict.time
            self.event(
                c, "Görüşme hafızası", "Önceki uygunluk korundu.",
                {"çelişen_zaman": conflict.time.label()},
            )
        if delivery.clarification and (
            conflict or not any(fact.time.overlaps(delivery.clarification) for fact in facts)
        ):
            return (
                f"{delivery.clarification.label()} için önceki yanıtınızla farklı bir tercih duydum. "
                "Tercihinizi değiştirdiyseniz hangi zamanın artık uygun olduğunu "
                "veya artık uygun olmadığını açıkça söyler misiniz?"
            )
        delivery.clarification = None
        delivery.add(facts)
        if facts or previous_requests != set(delivery.requests):
            context = delivery.context()
            context.pop("önceki_yanıtlar")
            self.event(
                c, "Görüşme hafızası",
                "Teslimat tercihleri ve talepleri değerlendirildi.", context,
            )
        if any(fact.revised for fact in facts):
            return self.offer(c, "Uygunluk bilginizi güncelledim. ")
        if previous_requests - set(delivery.requests) and not delivery.requests:
            if c.phase == "confirm" and delivery.allows(c.selected):
                return confirmation_question(c.selected)
            return self.offer(c)
        positives = [fact.time for fact in facts if fact.available and fact.time.hours]
        offered_times = [memory.DeliveryTime.from_slot(slot) for slot in c.slots]
        if positives and any(
            not when.day or any(when.day == offered.day for offered in offered_times)
            for when in positives
        ) and not any(
            when.includes(offered) for when in positives for offered in offered_times
        ):
            current = [slot for slot in c.slots if delivery.allows(slot)]
            if not current:
                return self.offer(c)
            return (
                "İstediğiniz saat aralığı için şu anda teslimat seçeneği yok. "
                + availability_question(current)
            )
        return None

    def decide(self, c, transcript, decision):
        text = voice.words(transcript)
        if c.phase == "consent":
            if decision == "CONTINUE":
                self.change(c, "verify", "Müşteri görüşmeyi kabul etti.")
                return "Teşekkürler. Doğum tarihinizi gün, ay ve yıl olarak söyler misiniz?"
            if decision == "LATER":
                self.finish(c, "Müşteri görüşmeyi ertelemek istedi.")
                return "Tabii, sizi daha fazla meşgul etmeyeyim."
            if decision == "DECLINE":
                self.finish(c, "Görüşme izni alınamadı.")
                return "Anladım."
            return (
                "Şimdi kısaca konuşabilir miyiz, yoksa başka bir zaman mı uygun olur?"
            )
        if c.phase == "verify":
            start = monotonic()
            ok, tries = self.bank.verify(c.id, decision)
            self.event(
                c,
                "Banka servisi",
                "Kimlik doğrulandı." if ok else "Kimlik doğrulanamadı.",
                {
                    "kontrol": "customers · doğum tarihi eşleşmesi",
                    "deneme": tries,
                    "karşılaştırılan_tarih": decision,
                },
                ms=elapsed(start),
            )
            if ok:
                return self.offer(c, "Teşekkürler. ")
            if tries >= 2:
                self.finish(c, "Doğrulama deneme sınırına ulaşıldı.")
                return "Kimliğinizi doğrulayamadım. Görüşmeyi sonlandırıyorum."
            return "Doğrulayamadım. Doğum tarihinizi gün, ay adı ve yıl olarak tekrar söyler misiniz?"
        if decision == "DECLINE_ALL":
            self.finish(c, "Sunulan zamanlar reddedildi. Randevu yazılmadı.")
            return "Anladım. Bu hafta için randevu oluşturulmadı."
        memory_reply = self.update_delivery_memory(c, transcript, decision)
        if memory_reply:
            return memory_reply
        if "adres" in c.delivery.requests:
            return (
                "Bu görüşmede teslimat adresini değiştiremiyorum. "
                "Kayıtlı adres uygunsa bunu açıkça belirtir misiniz?"
            )
        if "teslim_alıcısı" in c.delivery.requests:
            rule = "Kartı yalnız siz, kimliğinizi göstererek teslim alabilirsiniz. "
            if c.phase == "confirm":
                rule += f"{spoken(c.selected).capitalize()} kartı kendiniz alabilir misiniz?"
                return rule
            return rule + "Kartı kendiniz teslim alabileceğinizi belirtir misiniz?"
        if c.phase == "confirm":
            if decision in {"CHANGE", "DECLINE"} or not c.delivery.allows(c.selected):
                c.delivery.add([
                    memory.Availability(memory.DeliveryTime.from_slot(c.selected), False, c.turn)
                ])
                return self.offer(c, "Randevu oluşturmadım. ")
            if decision != "APPROVE":
                self.event(
                    c,
                    "Konuşma yöneticisi",
                    "Yanıt belirsiz. Bekleyen teklif korundu.",
                    {"teklif": label(c.selected)},
                )
                return "Yanıtınızı anlayamadım. " + confirmation_question(c.selected)
            start = monotonic()
            result = self.bank.confirm(c.id, c.token)
            c.booking_confirmed = result["result"] in {"booked", "existing"}
            self.event(
                c,
                "Banka servisi",
                {
                    "booked": "Randevu kaydedildi · bookings +1",
                    "existing": "Mevcut randevu bulundu.",
                    "full": "Son yer doldu · randevu eklenmedi.",
                }[result["result"]],
                {
                    "kontroller": [
                        "Demo geçişi" if c.birth_date_skipped else "Doğrulanmış oturum",
                        "Geçerli teklif",
                        "Mevcut randevu kontrolü",
                    ],
                    **result,
                },
                ms=elapsed(start),
            )
            if result["result"] == "full":
                return self.offer(c, "Bu aralık az önce doldu. ")
            if result["result"] == "existing":
                return self.offer(c)
            self.finish(c, "Banka kaydı kesinleşti.")
            return f"Randevunuz {spoken(c.selected)} için kaydedildi. Kartınızı kimliğinizle kendiniz teslim alabilirsiniz."
        if not c.slots:
            return self.offer(c)
        joint_no = any(
            phrase in text
            for phrase in (
                "ikisi de",
                "hiçbiri",
                "hiç bir",
                "cuma da perşembe de",
                "perşembe de cuma da",
            )
        ) and REJECTION.search(text)
        named_days = set(WEEKDAYS.findall(text))
        ordinals = option_indices(text)
        referenced_days = named_days | {
            voice.words(c.slots[index]["day"])
            for index in ordinals
            if index < len(c.slots)
        }
        offered_days = {voice.words(slot["day"]) for slot in c.slots}
        negated_days = {
            slot["id"]
            for slot in c.slots
            if re.search(
                rf"\b{voice.words(slot['day'])}\b(?: günü)?(?: sabah| öğleden sonra)?(?: bana)? "
                r"(?:olmaz|uymaz|uygun değil|gelemem)",
                text,
            )
        }
        negated_days.update(
            c.slots[index]["id"]
            for index in ordinals
            if index < len(c.slots)
            and re.search(
                rf"\b{ORDINAL_OPTIONS[index]}{OPTION_QUALIFIER}(?: benim için| bana)? "
                r"(?:olmaz|uymaz|uygun değil|değil|istemiyorum)",
                text,
            )
        )
        if joint_no or (
            c.slots and all(slot["id"] in negated_days for slot in c.slots)
        ):
            self.finish(c, "Sunulan zamanlar reddedildi. Randevu yazılmadı.")
            return "Anladım. Bu hafta için randevu oluşturulmadı."
        unavailable_days = named_days - offered_days
        if unavailable_days and c.slots:
            current = [slot for slot in c.slots if c.delivery.allows(slot)]
            if not current:
                return self.offer(c)
            self.event(
                c,
                "Konuşma yöneticisi",
                "Yanıtta sunulmayan bir gün geçiyor. Mevcut teklifler tekrarlandı.",
                {"sunulmayan_günler": sorted(unavailable_days)},
            )
            days = " ve ".join(day.capitalize() for day in sorted(unavailable_days))
            return f"{days} için şu anda teslimat seçeneği yok. " + availability_question(
                current
            )
        if decision is None:
            if any(not c.delivery.allows(slot) for slot in c.slots):
                return self.offer(c)
            return (
                "Tercihinizi anlayamadım. Uygun olduğunuz günü tekrar söyler misiniz?"
            )
        accepted = [slot for slot in c.slots if decision[str(slot["id"])] == "uygun"]
        requested_times = [
            fact.time for fact in c.delivery.availability
            if fact.turn == c.turn and fact.available and fact.time.hours
        ]
        reason = None
        if (
            "?" in transcript
            or QUESTION.search(text)
            or any(term in text for term in TENTATIVE)
        ):
            reason = "Müşteri henüz kesin bir tercih belirtmedi."
        elif any(index >= len(c.slots) for index in ordinals):
            reason = "Yanıtta sunulmayan bir aralık geçiyor."
        elif any(
            slot["id"] in negated_days or not c.delivery.allows(slot)
            for slot in accepted
        ):
            reason = "Modelin seçtiği aralık müşteri tarafından reddedildi."
        elif requested_times and any(
            not any(when.includes(memory.DeliveryTime.from_slot(slot)) for when in requested_times)
            for slot in accepted
        ):
            reason = "Modelin seçtiği aralık müşterinin belirttiği saatle uyuşmuyor."
        elif referenced_days and any(
            voice.words(slot["day"]) not in referenced_days for slot in accepted
        ):
            reason = "Model önerisi müşterinin gün veya sıra referansıyla uyuşmuyor."
        elif len(accepted) > 1:
            reason = "Birden fazla aralık uygun görünüyor, tek tercih belirsiz."
        elif not referenced_days and accepted and not AGREEMENT.search(text):
            reason = "Yanıtta teklif edilen bir zaman veya açık kabul belirtilmedi."
        if reason:
            self.event(
                c,
                "Konuşma yöneticisi",
                reason,
                {
                    "model_önerisi": [slot["day"] for slot in accepted],
                    "karar_gerekçesi": reason,
                },
            )
            if any(not c.delivery.allows(slot) for slot in c.slots):
                return self.offer(c)
            return "Hangi zamanı tercih edersiniz? Günü veya seçenek sırasını söyleyebilirsiniz."
        if not accepted:
            if any(not c.delivery.allows(slot) for slot in c.slots):
                return self.offer(c)
            self.event(
                c, "Konuşma yöneticisi", "Açık tercih bulunamadı. Yeniden soruldu."
            )
            return "Hangi zamanı tercih edersiniz? Uygun bir seçenek yoksa bunu da söyleyebilirsiniz."
        selected = accepted[0]
        start = monotonic()
        c.token = self.bank.propose(c.id, selected["id"])
        c.selected = selected
        if not c.delivery.prefers(selected):
            c.delivery.add([
                memory.Availability(memory.DeliveryTime.from_slot(selected), True, c.turn)
            ])
        self.event(
            c,
            "Banka servisi",
            "Seçilen aralık onay için ayrıldı. Diğer aralıklar serbest bırakıldı.",
            {"teklif": label(c.selected), "randevu_yazıldı": False},
            ms=elapsed(start),
        )
        self.change(c, "confirm", "Ayrı bir turda açık onay istenecek.")
        return confirmation_question(c.selected)

    def process(self, cid, expected, blob, mime, request_key=None):
        with self.lock:
            c = self.get(cid)
            if c.ended_by_operator:
                raise Expired
            if request_key in c.completed_requests:
                prior_turn, view = c.completed_requests[request_key]
                if prior_turn != expected:
                    raise Occupied
                return view
            self.valid(c)
            if c.turn != expected or not c.turn_lock.acquire(False):
                raise Occupied
            c.turn += 1
            phase = c.phase
        try:
            with self.lock:
                self.valid(c)
                self.event(
                    c,
                    "Mikrofon",
                    "Ses kaydı alındı.",
                    {"biçim": mime, "bayt": len(blob)},
                )
                step = self.event(
                    c, "Whisper", "Türkçe konuşma çözümleniyor…", status="working"
                )
            start = monotonic()
            try:
                transcript, seconds = voice.transcribe(blob, phase)
            except Exception as error:
                with self.lock:
                    self.valid(c)
                    self.event(
                        c,
                        "Whisper",
                        str(error)
                        if isinstance(error, ValueError)
                        else "Ses çözümlenemedi.",
                        status="error",
                        step=step,
                        ms=elapsed(start),
                    )
                    answer = "Sesinizi anlayamadım."
                    if phase == "confirm":
                        answer += " " + confirmation_question(c.selected)
                    else:
                        answer += " Tekrar söyler misiniz?"
                    self.reply(c, answer)
                    return self.complete(c, expected, request_key)
            with self.lock:
                self.valid(c)
                self.event(
                    c,
                    "Whisper",
                    transcript,
                    {
                        "ses_süresi_sn": seconds,
                        "dil": "tr",
                        "model": voice.whisper_model().name,
                    },
                    step=step,
                    ms=elapsed(start),
                )
                self.message(c, "customer", transcript)
                offers = list(c.slots)
                selected = c.selected if phase == "confirm" else None
                proposal = label(selected) if selected else None
                context = (c.delivery.context(),) if c.delivery.recent else ()
                if phase == "verify":
                    parts = voice.date_parts(transcript)
                    decision = voice.iso_date(parts)
                    interpret = parts is None
                else:
                    decision = match_phrase(phase, transcript, offers)
                    if phase == "choose" and not offers and decision is None:
                        decision = {}
                    interpret = decision is None
                if interpret:
                    step = self.event(
                        c, "Qwen", "Müşteri yanıtı yorumlanıyor…", status="working"
                    )
                elif phase == "verify":
                    self.event(
                        c,
                        "Konuşma yöneticisi",
                        "Tarih doğrudan okundu."
                        if decision
                        else "Geçersiz takvim tarihi.",
                        {"çıkarılan_tarih": decision},
                    )
                elif decision:
                    self.event(
                        c,
                        "Konuşma yöneticisi",
                        "Yanıt ifade kuralıyla eşleşti.",
                        {
                            "adım": PHASES[phase],
                            "sonuç": INTENT_LABELS.get(decision, decision)
                            if isinstance(decision, str)
                            else decision,
                        },
                    )
            if interpret:
                start = monotonic()
                try:
                    if phase == "verify":
                        decision = voice.interpret_birth_date(transcript)
                        summary = (
                            "Doğum tarihi çıkarıldı."
                            if decision
                            else "Kesin bir tarih çıkarılamadı."
                        )
                        detail = {"çıkarılan_tarih": decision}
                    elif phase == "choose":
                        raw, decision = voice.classify(transcript, offers, *context)
                        summary = " · ".join(
                            f"{s['day']}: {decision[str(s['id'])].replace('_', ' ')}"
                            for s in offers
                        )
                        detail = {
                            "teklifler": {s["id"]: label(s) for s in offers},
                            "model_json": raw,
                            "zaman_kimliğine_eşleme": decision,
                        }
                    else:
                        decision = voice.interpret_intent(phase, transcript, proposal, *context)
                        summary = INTENT_LABELS[decision]
                        detail = {
                            "model_json": {"intent": decision},
                            "bekleyen_teklif": proposal,
                        }
                        override = None
                        if phase == "confirm":
                            override = confirmation_override(
                                transcript, selected, decision, offers
                            )
                        elif phase == "consent":
                            override = consent_override(transcript, decision)
                        if decision == "THIRD_PARTY" and not asks_third_party_delivery(
                            transcript
                        ):
                            override = (
                                "UNCLEAR",
                                "Yanıtta başka bir teslim alıcısı belirtilmedi.",
                            )
                        if override:
                            decision, reason = override
                            detail.update(
                                uygulanan_niyet=decision, karar_gerekçesi=reason
                            )
                            summary = INTENT_LABELS[decision] + " · " + reason
                except Exception:
                    decision = None
                    with self.lock:
                        self.valid(c)
                        self.event(
                            c,
                            "Qwen",
                            "Model yanıtı alınamadı veya şema geçersiz.",
                            status="error",
                            step=step,
                            ms=elapsed(start),
                        )
                else:
                    with self.lock:
                        self.valid(c)
                        self.event(
                            c,
                            "Qwen",
                            summary,
                            {"adım": PHASES[phase], "metin": transcript, **detail},
                            step=step,
                            ms=elapsed(start),
                        )
            with self.lock:
                self.valid(c)
                try:
                    answer = self.decide(c, transcript, decision)
                except (BankError, OSError, sqlite3.Error):
                    self.event(
                        c,
                        "Banka servisi",
                        "İşlem sonucu doğrulanamadı.",
                        status="error",
                    )
                    answer = "İşlemin sonucunu şu an doğrulayamıyorum."
                self.reply(c, answer)
                return self.complete(c, expected, request_key)
        finally:
            c.turn_lock.release()

    def complete(self, c, expected, request_key):
        view = self.view(c)
        if request_key:
            # Only the most recent result is needed for a dropped HTTP response.
            c.completed_requests = {request_key: (expected, view)}
        return view


def elapsed(start):
    return round((monotonic() - start) * 1000)
