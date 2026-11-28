"""HUMAIN Voice text-to-speech CLI (preset platform voices only, model Nebula).

Runs in the isolated venv `.venv-humain` (SDK `humain-voice==0.18.1`), never in the main `.venv`:

    .venv-humain\\Scripts\\python.exe tools\\audio\\humain_voice_cli.py <command> [...]

Commands
    has-key                     exit 0 if a key is configured (no network)
    voices [--json]             list the platform's preset voices
    test [--force] [--remember] two-voice comparison into outputs/humain-test (reuses saved samples)
    say --text-file F --voice V --out O.wav [--emotion happy] [--per-line]
                                read a UTF-8 Arabic script with one voice (id or label). Put delivery tags
                                in the text: [happy] [sad] [angry] [fearful] [surprised] [whisper] [neutral]
                                [news] [storytelling] [advertising] [call_center] [conversational].
                                ElevenLabs-style tags ([excited], [laughs]…) are translated; unknown ones dropped.
    forget                      delete the key saved by --remember

Key, first found wins: HUMAIN_VOICE_API_KEY in the environment, the key file `HUMAIN_API_KEY.env` in the
repo root (Fawaz pastes the key there; `*.env` is git-ignored), then the Windows Credential Manager entry saved
by `--remember`. The key is never printed or logged.
URL: HUMAIN_VOICE_API_URL, default https://api.voice.humain.com.

Exit codes: 0 ok · 1 other failure · 2 key missing/invalid (401) · 3 key lacks permission (403)
            4 connection/timeout · 5 out of credit / billing (stopped, nothing charged after it)
            6 API rejected the request (voice, text, policy)

The SDK is imported lazily so OpenMontage's tool registry can import this module from the main venv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = REPO_ROOT / "outputs" / "humain-test"
DEFAULT_URL = "https://api.voice.humain.com"
KEY_ENV, URL_ENV = "HUMAIN_VOICE_API_KEY", "HUMAIN_VOICE_API_URL"
KEY_FILE = REPO_ROOT / "HUMAIN_API_KEY.env"
KEYRING_SERVICE, KEYRING_USER = "fawaz-studio/humain-voice", "api_key"
TEST_TEXT = (
    "أقولك، قد صار لك موقف طلبت فيه من الذكاء الاصطناعي يختصر عليك شغلة، وبعدين جلست ساعة تشرح له إيش تبغى؟ "
    "طيب اسمع هذي… أنا سويت نفس الحركة، والمفاجأة إن الحل كان في كلمتين!"
)
# BCP-47 dialect tags documented at https://docs.voice.humain.com/en/models#voice-list
DIALECTS = {"ars": "Saudi Arabic (سعودي)", "apc": "Levantine Arabic (شامي)", "ary": "Moroccan Arabic (مغربي)",
            "arz": "Egyptian Arabic (مصري)", "ar": "Arabic"}
BILLING_CODES = {"CREDITS_EXHAUSTED", "BILLING_AUTHORIZATION_UNAVAILABLE"}
TTS_TEXT_LIMIT = 1500
# Emotion and speaking style (docs: api-guides/asyncapi/tts#emotion-and-speaking-style). Only these 12 names are
# tags; any other [name] is SPOKEN by the platform, so unknown tags must never reach it.
EMOTIONS = ("neutral", "happy", "sad", "angry", "fearful", "surprised", "whisper")
STYLES = ("news", "storytelling", "advertising", "call_center", "conversational")
DELIVERIES = set(EMOTIONS + STYLES)
# ElevenLabs v4-style tags -> the closest HUMAIN delivery (None = drop silently)
EL_MAP = {
    **dict.fromkeys(["excited", "cheerful", "playful", "laughs", "laughing", "laugh", "chuckles", "giggles", "joyful",
                     "warmly", "warm", "amused", "delighted", "smiling", "enthusiastic", "energetic"], "happy"),
    **dict.fromkeys(["amazed", "gasps", "gasp", "shocked", "astonished", "surprise", "wow"], "surprised"),
    **dict.fromkeys(["sighs", "sigh", "disappointed", "crying", "sorrowful", "upset", "regretful"], "sad"),
    **dict.fromkeys(["frustrated", "annoyed", "furious", "shouting", "angrily"], "angry"),
    **dict.fromkeys(["nervous", "scared", "anxious", "worried", "panicked", "afraid", "urgent", "tense"], "fearful"),
    **dict.fromkeys(["whispers", "whispering", "softly", "quietly", "secretive"], "whisper"),
    **dict.fromkeys(["thoughtful", "calm", "serious", "focused", "flat", "matter-of-fact"], "neutral"),
    **dict.fromkeys(["curious", "casual", "chatty", "confident", "friendly", "explaining"], "conversational"),
    **dict.fromkeys(["narrating", "narration", "storyteller"], "storytelling"),
    **dict.fromkeys(["emphatic", "promo", "salesy", "hype"], "advertising"),
    **dict.fromkeys(["reporting", "newscaster", "announcer"], "news"),
}
TAG_RE = re.compile(r"\[([A-Za-z][A-Za-z _-]{0,28})\]")
_API_URL_USED = DEFAULT_URL  # code points per request (docs: errors-and-rate-limits)


class CliError(Exception):
    def __init__(self, exit_code: int, kind: str, message: str, code: str | None = None):
        super().__init__(message)
        self.exit_code, self.kind, self.code = exit_code, kind, code


# ---------------------------------------------------------------- key handling
def _keyring():
    try:
        import keyring  # installed only in .venv-humain

        return keyring
    except Exception:
        return None


def key_from_file(path: Path | None = None) -> str | None:
    """Read the key from the key file: `HUMAIN_VOICE_API_KEY=<key>`, or the key alone on a line."""
    path = path or KEY_FILE
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_ENV:
            value = value.strip().strip("'\"").strip()
        elif not sep and re.fullmatch(r"[A-Za-z0-9_\-.]{16,}", line):
            value = line
        else:
            continue
        if value:
            return value
    return None


def get_key() -> str | None:
    key = os.environ.get(KEY_ENV, "").strip() or key_from_file()
    if key:
        return key
    kr = _keyring()
    if kr is not None:
        try:
            return (kr.get_password(KEYRING_SERVICE, KEYRING_USER) or "").strip() or None
        except Exception:
            return None
    return None


def scrub(text: str, key: str | None) -> str:
    text = str(text)
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(x-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1***", text, flags=re.I)


# ---------------------------------------------------------------- errors
def classify(exc: BaseException, key: str | None, code: str | None = None) -> CliError:
    msg = scrub(f"{type(exc).__name__}: {exc}", key)
    up = f"{code or ''} {msg}".upper()
    has = lambda n: re.search(rf"(?<![0-9A-F-]){n}(?![0-9A-F-])", up) is not None  # HTTP status, not part of a UUID
    if code in BILLING_CODES or "CREDITS_EXHAUSTED" in up or has("402") or "PAYMENT REQUIRED" in up:
        return CliError(5, "billing", "الرصيد خلص أو الفوترة ما وافقت — وقفت قبل أي توليد إضافي. " + msg, code)
    if "AUTH_UNAUTHORIZED" in up or has("401") or "UNAUTHORIZED" in up or "INVALID API KEY" in up:
        return CliError(2, "auth", "المفتاح غلط أو منتهي أو من بيئة ثانية (401). " + msg, code)
    if "AUTH_FORBIDDEN" in up or has("403") or "FORBIDDEN" in up:
        return CliError(3, "permission", "المفتاح شغال بس ما عنده صلاحية TTS (403) — افتح المفتاح في الكونسول وشيّك Scopes. " + msg, code)
    if code:
        return CliError(6, "api", f"المنصة رفضت الطلب ({code}). " + msg, code)
    net = ("ConnectionError", "TimeoutError", "ClientConnector", "ClientOSError", "gaierror",
           "Cannot connect", "timed out", "Timeout", "Connection refused", "SSL")
    if any(n.lower() in msg.lower() for n in net):
        return CliError(4, "connection", "ما قدرت أوصل للخدمة (شبكة/مهلة). " + msg, code)
    return CliError(1, "error", msg, code)


# ---------------------------------------------------------------- SDK wrapper
class Humain:
    # The docs give https://api.voice.humain.com; the console's snippets use https://voice.humain.com.
    # A key works only on the environment that issued it, so without HUMAIN_VOICE_API_URL try both (connect is free).
    FALLBACK_URLS = (DEFAULT_URL, "https://voice.humain.com")

    def __init__(self, key: str):
        from humain_voice import tts  # lazy: only available in .venv-humain

        self.tts, self.key = tts, key
        self.last_error = None
        self.client = None
        env_url = os.environ.get(URL_ENV, "").strip().rstrip("/")
        self.urls = (env_url,) if env_url else self.FALLBACK_URLS
        self.url = self.urls[0]

    def _on_error(self, err) -> None:
        if err is not None:
            self.last_error = err

    def __enter__(self):
        first_err = None
        for url in self.urls:
            self.last_error = None
            self.client = self.tts.TTSClient(api_url=url, api_key=self.key, on_error=self._on_error)
            try:
                self.client.__enter__()  # a bad key fails here
                self.url = url
                return self
            except BaseException as exc:
                err = classify(exc, self.key, getattr(self.last_error, "code", None))
                first_err = first_err or err
                try:
                    self.client.close_sync()
                except Exception:
                    pass
                if err.kind not in ("auth", "connection", "error"):
                    break
        raise first_err

    def __exit__(self, *exc):
        try:
            if self.client is not None:
                self.client.close_sync()
        except Exception:
            pass

    def voices(self) -> list[dict]:
        try:
            return list(self.client.list_voices_sync(timeout_seconds=20.0))
        except BaseException as exc:
            raise classify(exc, self.key, getattr(self.last_error, "code", None)) from None

    def synth(self, text: str, voice_id: str) -> bytes:
        self.last_error = None
        model = self.tts.TtsModel.Nebula
        try:
            pcm = self.client.synthesize_sync(text, voice_id=voice_id, model=model, timeout_seconds=30.0,
                                              on_error=self._on_error)
        except BaseException as exc:
            raise classify(exc, self.key, getattr(self.last_error, "code", None)) from None
        if not pcm:
            raise CliError(6, "api", "رجع صوت فاضي من المنصة.")
        return pcm

    def rate(self) -> int:
        return int(self.tts.get_sample_rate(self.tts.TtsModel.Nebula))


# ---------------------------------------------------------------- helpers
def voice_row(v: dict) -> dict:
    prof = v.get("profile") or {}
    spk = prof.get("speaker") or {}
    return {"label": v.get("label", ""), "id": v.get("id", ""), "languages": list(prof.get("languages") or []),
            "dialect": spk.get("dialect", ""), "dialect_meaning": DIALECTS.get(spk.get("dialect", ""), ""),
            "gender": spk.get("gender", ""), "emotions": prof.get("emotions"), "styles": prof.get("styles")}


def is_arabic(r: dict) -> bool:
    return any(l == "ar" or l.startswith("ar-") for l in r["languages"]) or r["dialect"].startswith("ar")


def is_saudi(r: dict) -> bool:
    return r["dialect"] == "ars" or r["dialect"].lower().startswith("ar-sa")


def pick_two(rows: list[dict]) -> tuple[list[dict], str]:
    rows = sorted(rows, key=lambda r: r["label"])
    saudi = [r for r in rows if is_saudi(r)]
    arabic = [r for r in rows if is_arabic(r)]
    if len(saudi) >= 2:
        fem = next((r for r in saudi if r["gender"] == "female"), None)
        mal = next((r for r in saudi if r["gender"] == "male"), None)
        pair = [fem, mal] if fem and mal else saudi[:2]
        return pair, "صوتين سعوديين (ars) — واحد نسائي وواحد رجالي إذا توفّروا"
    if len(saudi) == 1:
        other = next((r for r in arabic if r["id"] != saudi[0]["id"]), None)
        return ([saudi[0], other] if other else saudi), "صوت سعودي واحد فقط، والثاني عربي للمقارنة"
    if arabic:
        return arabic[:1], "ما فيه صوت سعودي — صوت عربي واحد"
    return [], "ما فيه أي صوت عربي"


def safe_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", label).strip("_") or "voice"


def write_wav(path: Path, pcm: bytes, rate: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return verify_wav(path, rate)


def verify_wav(path: Path, rate: int | None = None) -> float:
    with wave.open(str(path), "rb") as w:
        ok = w.getnchannels() == 1 and w.getsampwidth() == 2 and (rate is None or w.getframerate() == rate)
        frames, fr = w.getnframes(), w.getframerate()
    if not ok or frames <= 0:
        raise CliError(1, "error", f"الملف {path.name} مو PCM16 أحادي صالح أو مدته صفر")
    return round(frames / fr, 2)


def split_text(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    parts, cur = [], ""
    for piece in re.split(r"(?<=[.!؟?…\n])\s*", text):
        piece = piece.strip()
        if not piece:
            continue
        while len(piece) > max_chars:  # very long sentence: cut at the last comma/space
            cut = max(piece.rfind("،", 0, max_chars), piece.rfind(",", 0, max_chars), piece.rfind(" ", 0, max_chars))
            cut = cut if cut > 0 else max_chars
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(piece[: cut + 1].strip())
            piece = piece[cut + 1:].strip()
        if cur and len(cur) + 1 + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts


def normalize_tags(text: str, supported: set[str] | None = None) -> tuple[str, list[str]]:
    """Keep the 12 HUMAIN tags, translate ElevenLabs-style tags, drop anything else (it would be spoken)."""
    notes: list[str] = []

    def fix(m: re.Match) -> str:
        name = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
        if name in DELIVERIES:
            new = name
        elif name.replace("_", "-") in EL_MAP or name in EL_MAP:
            new = EL_MAP.get(name) or EL_MAP.get(name.replace("_", "-"))
            notes.append(f"[{m.group(1)}] → [{new}]")
        else:
            notes.append(f"[{m.group(1)}] حُذف (مو وسم معروف وكان بينقرأ كلام)")
            return ""
        if supported is not None and new not in supported:
            notes.append(f"[{new}] الصوت ما يدعمه، حُذف")
            return ""
        return f"[{new}]"

    out_text = TAG_RE.sub(fix, text)
    # a tag directly before another tag: the last one wins anyway; collapse doubles like "[a][b]" -> "[b]"
    out_text = re.sub(r"(\[[a-z_]+\]\s*)+(?=\[[a-z_]+\])", "", out_text)
    return re.sub(r"[ \t]{2,}", " ", out_text), notes


def carry_tags(chunks: list[str], start: str | None = None) -> list[str]:
    """Each request starts neutral, so open every chunk with the delivery active at its start."""
    active, outl = start, []
    for c in chunks:
        if active and not re.match(r"\s*\[[a-z_]+\]", c):
            c = f"[{active}] {c}"
        outl.append(c)
        tags = re.findall(r"\[([a-z_]+)\]", c)
        if tags:
            active = tags[-1]
    return outl


def spoken_len(text: str) -> int:
    return len(re.sub(r"\[[a-z_]+\]\s*", "", text))


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def require_key() -> str:
    key = get_key()
    if not key:
        raise CliError(2, "auth", f"ما فيه مفتاح: الصقه في {KEY_FILE.name} بعد علامة = واحفظ الملف.")
    return key


def print_voices(rows: list[dict]) -> None:
    print(f"\nالأصوات الجاهزة في المنصة ({len(rows)}):")
    for r in rows:
        extra = f" | emotions={','.join(r['emotions'])}" if r.get("emotions") else ""
        extra += f" | styles={','.join(r['styles'])}" if r.get("styles") else ""
        print(f"  - {r['label']} | id={r['id']} | lang={','.join(r['languages'])} | dialect={r['dialect'] or '—'}"
              f" ({r['dialect_meaning'] or 'غير موثّق'}) | gender={r['gender'] or '—'}{extra}")


def cached_samples(text: str) -> dict | None:
    man = TEST_DIR / "manifest.json"
    if not man.is_file():
        return None
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
        if data.get("text_sha") != text_sha(text) or not data.get("samples"):
            return None
        for s in data["samples"]:
            s["duration_s"] = verify_wav(TEST_DIR / s["file"], s.get("sample_rate"))
        return data
    except Exception:
        return None


def show_samples(data: dict, cached: bool) -> None:
    print("\n" + ("العينات محفوظة من قبل — ما صرفت رصيد جديد:" if cached else "العينات الجديدة:"))
    for s in data["samples"]:
        p = (TEST_DIR / s["file"]).resolve()
        print(f"  - {s['label']} | لهجة معلنة: {s['dialect'] or '—'} ({s['dialect_meaning'] or 'غير موثّق'})"
              f" | {s['duration_s']} ث | {p.as_uri()}")
    if data.get("selection_note"):
        print("  ملاحظة الاختيار:", data["selection_note"])


def log_run(payload: dict) -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    payload["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (TEST_DIR / "last_run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- commands
def cmd_has_key(_a) -> int:
    return 0 if get_key() else 2


def cmd_forget(_a) -> int:
    kr = _keyring()
    try:
        if kr is not None:
            kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
        print("انمسح المفتاح من Windows Credential Manager.")
    except Exception:
        print("ما فيه مفتاح محفوظ.")
    return 0


def cmd_voices(a) -> int:
    key = require_key()
    with Humain(key) as h:
        rows = [voice_row(v) for v in h.voices()]
    out(rows) if a.json else print_voices(rows)
    return 0


def cmd_test(a) -> int:
    if not a.force:
        data = cached_samples(TEST_TEXT)
        if data:
            show_samples(data, cached=True)
            log_run({"status": "cached", "samples": data["samples"]})
            return 0
    key = require_key()
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "sample_text.txt").write_text(TEST_TEXT, encoding="utf-8")
    with Humain(key) as h:
        global _API_URL_USED
        _API_URL_USED = h.url
        rows = [voice_row(v) for v in h.voices()]  # a valid key is proven here
        print(f"اتصلت بنجاح: {h.url}")
        if a.remember and not os.environ.get("HUMAIN_VOICE_NO_REMEMBER"):
            kr = _keyring()
            if kr is None:
                print("تنبيه: keyring غير متوفر، ما حفظت المفتاح.")
            else:
                kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)
                print("حفظت المفتاح مشفّر في Windows Credential Manager (للاستخدام لاحقاً). للحذف: الأمر forget.")
        (TEST_DIR / "voices.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print_voices(rows)
        chosen, note = pick_two(rows)
        if not chosen:
            log_run({"status": "no_arabic_voice", "voices": len(rows)})
            raise CliError(6, "api", "ما لقيت صوت عربي في القائمة، ما ولّدت شي.")
        rate = h.rate()
        samples = []
        for r in chosen:
            print(f"\nأولّد بصوت {r['label']} ...", flush=True)
            try:
                pcm = h.synth(TEST_TEXT, r["id"])
            except CliError as e:
                log_run({"status": e.kind, "code": e.code, "voice": r["label"], "done": samples})
                if samples:
                    _save_manifest(samples, note, rate)
                raise
            f = TEST_DIR / f"{safe_name(r['label'])}.wav"
            dur = write_wav(f, pcm, rate)
            samples.append({**{k: r[k] for k in ("label", "id", "dialect", "dialect_meaning", "gender", "languages")},
                            "file": f.name, "duration_s": dur, "sample_rate": rate})
        data = _save_manifest(samples, note, rate)
    show_samples(data, cached=False)
    log_run({"status": "ok", "samples": samples})
    return 0


def _save_manifest(samples: list[dict], note: str, rate: int) -> dict:
    data = {"model": "nebula", "api_url": _API_URL_USED, "sample_rate": rate, "text": TEST_TEXT, "text_sha": text_sha(TEST_TEXT),
            "selection_note": note, "samples": samples}
    (TEST_DIR / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def cmd_say(a) -> int:
    text = Path(a.text_file).read_text(encoding="utf-8-sig").strip()
    if not text:
        raise CliError(6, "api", "ملف النص فاضي.")
    if not re.search(r"[\u0600-\u06FF]", text):
        print("تنبيه: النص ما فيه حروف عربية، فالصوت بيقرأ إنجليزي.")
    if not 50 <= a.max_chars <= TTS_TEXT_LIMIT:
        raise CliError(6, "api", f"--max-chars لازم بين 50 و {TTS_TEXT_LIMIT}")
    if a.emotion and a.emotion not in DELIVERIES:
        raise CliError(6, "api", f"--emotion لازم واحد من: {', '.join(EMOTIONS + STYLES)}")
    out_path = Path(a.out)
    if out_path.suffix.lower() != ".wav":
        raise CliError(6, "api", "مسار الإخراج لازم ينتهي بـ .wav")
    key = require_key()
    with Humain(key) as h:
        voice_id, label, supported = a.voice, a.voice, None
        rows = [voice_row(v) for v in h.voices()]
        hit = next((r for r in rows if a.voice.lower() in (r["label"].lower(), r["id"].lower())), None)
        if not hit and not re.fullmatch(r"[0-9a-fA-F-]{36}", a.voice):
            raise CliError(6, "api", f"ما لقيت صوت باسم {a.voice}. الأصوات: " + ", ".join(r["label"] for r in rows))
        if hit:
            voice_id, label = hit["id"], hit["label"]
            if hit.get("emotions") or hit.get("styles"):
                supported = set(hit.get("emotions") or []) | set(hit.get("styles") or [])
            else:
                supported = set()  # docs: no profile.emotions/styles -> don't send tags
        text, notes = normalize_tags(text, supported)
        for n in notes:
            print("وسم:", n, file=sys.stderr)
        if a.per_line:
            chunks = [l.strip() for l in text.splitlines() if spoken_len(l.strip()) > 0]
            chunks = [p for l in chunks for p in (split_text(l, a.max_chars) if len(l) > a.max_chars else [l])]
        else:
            chunks = split_text(text, a.max_chars)
        chunks = carry_tags(chunks, a.emotion)
        rate = h.rate()
        gap_ms = a.gap_ms if a.gap_ms is not None else (300 if a.per_line else 120)
        gap = b"\x00\x00" * int(rate * gap_ms / 1000)
        pcm = bytearray()
        for i, c in enumerate(chunks, 1):
            print(f"[{i}/{len(chunks)}] {spoken_len(c)} حرف | {', '.join(re.findall(r'\[([a-z_]+)\]', c)) or 'neutral'}",
                  file=sys.stderr, flush=True)
            pcm += h.synth(c, voice_id)
            if i < len(chunks):
                pcm += gap
    dur = write_wav(out_path, bytes(pcm), rate)
    out({"output": str(out_path.resolve()), "uri": out_path.resolve().as_uri(), "voice": label, "voice_id": voice_id,
         "model": "nebula", "sample_rate": rate, "duration_s": dur, "chunks": len(chunks),
         "characters": sum(spoken_len(c) for c in chunks), "tag_notes": notes})
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="HUMAIN Voice TTS (preset voices, Nebula)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("has-key").set_defaults(fn=cmd_has_key)
    sub.add_parser("forget").set_defaults(fn=cmd_forget)
    v = sub.add_parser("voices")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_voices)
    t = sub.add_parser("test")
    t.add_argument("--force", action="store_true", help="regenerate even if saved samples exist")
    t.add_argument("--remember", action="store_true", help="save the key in Windows Credential Manager after it works")
    t.set_defaults(fn=cmd_test)
    s = sub.add_parser("say")
    s.add_argument("--text-file", required=True)
    s.add_argument("--voice", required=True, help="voice id (UUID) or label, e.g. mul_nourah")
    s.add_argument("--out", required=True)
    s.add_argument("--max-chars", type=int, default=600, help="characters per request (limit 1500)")
    s.add_argument("--emotion", help="starting delivery: " + ", ".join(EMOTIONS + STYLES))
    s.add_argument("--per-line", action="store_true", help="one request per line, joined with a pause (scripts)")
    s.add_argument("--gap-ms", type=int, help="pause between requests (default 120, or 300 with --per-line)")
    s.set_defaults(fn=cmd_say)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except CliError as e:
        print(f"✗ [{e.kind}{'/' + e.code if e.code else ''}] {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except BaseException as exc:  # never leak the key
        e = classify(exc, get_key())
        print(f"✗ [{e.kind}] {e}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
