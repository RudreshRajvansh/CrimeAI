"""
Gemini case brief: an English reading of any FIR, for the officer who does not read its language.

The rules-based reader stays the record (sections, identifiers, links). Gemini adds what rules
cannot: a faithful English translation, a short summary, the modus operandi in plain phrases, the
facts an investigator looks for first, and what the FIR leaves out. Its section suggestions are
checked against the IPC-BNS crosswalk and labelled, never applied to the case.

Only PII-redacted text leaves the deployment: Aadhaar, phone, PAN, bank and UPI numbers are masked
before the request is built, and the instruction forbids reconstructing them. The feature stays off
until GEMINI_API_KEY is set; the endpoints that call it audit every request.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.nlp.legal.bns_crosswalk import IPC_TO_BNS, analyse_sections, governing_code
from app.nlp.multilingual import normalise_digits, normalise_for_legal
from app.nlp.privacy.redaction import redact
from app.schemas.crime import VALID_CRIME_TYPES

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MAX_CHARS = 12_000
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_SECTION = re.compile(r"[^0-9A-Za-z()]")
_WORD = re.compile(r"[a-z]{4,}")
_FILLER = {"with", "from", "under", "that", "which", "into", "their", "section", "offence", "person", "persons",
           "property", "punishment", "committed", "commission", "other", "another", "being"}

_STR = {"type": "STRING"}
_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "english": _STR,
        "summary": _STR,
        "offence": _STR,
        "modus_operandi": {"type": "ARRAY", "items": _STR},
        "facts": {
            "type": "OBJECT",
            "properties": {"when": _STR, "where": _STR, "property": _STR, "weapon": _STR},
        },
        "suggested_sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"code": _STR, "section": _STR, "reason": _STR},
                "required": ["code", "section", "reason"],
            },
        },
        "missing": {"type": "ARRAY", "items": _STR},
    },
    "required": ["english", "summary", "offence", "modus_operandi", "facts", "suggested_sections", "missing"],
}


def enabled() -> bool:
    return bool(settings.GEMINI_API_KEY)


def _instruction(code: str) -> str:
    regime = (
        "The incident is on or after 1 July 2024, so cite Bharatiya Nyaya Sanhita (BNS) sections."
        if code == "BNS" else
        "The incident is before 1 July 2024, so cite Indian Penal Code (IPC) sections."
    )
    return (
        "You help Indian police analysts read First Information Reports (FIRs) written in any Indian language. "
        "Personal identifiers are masked as [PHONE], [AADHAAR], [UPI], [BANK_ACCOUNT] and similar: keep the masks "
        "and never guess what they hide. Translate faithfully and add no facts. Do not name, profile or speculate "
        f"about suspects beyond what the FIR states. {regime} Suggest at most four sections, each with a one-line "
        "reason tied to the text, and add the IT Act only when the text supports it. List up to five details an "
        "investigator needs that the FIR does not give. Answer in English: summary under 45 words, each modus "
        "operandi item under 8 words, facts as short phrases or an empty string when the FIR is silent."
    )


def _strings(value: Any, limit: int) -> list[str]:
    items = value if isinstance(value, list) else []
    return [str(v).strip() for v in items if str(v).strip()][:limit]


def _norm(section: str) -> str:
    return _SECTION.sub("", section).upper()


def _titles() -> dict[tuple[str, str], str]:
    """Official titles the crosswalk knows, by (code, section)."""
    known: dict[tuple[str, str], str] = {}
    for ipc, (bns, title, _) in IPC_TO_BNS.items():
        known.setdefault(("IPC", _norm(ipc)), title)
        known.setdefault(("BNS", _norm(bns)), title)
    return known


_TITLES = _titles()


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _WORD.findall(text.lower()) if w not in _FILLER}


def _check_sections(suggested: list[dict], text: str, incident_date: date | datetime | None, code: str) -> list[dict]:
    """
    Label each suggestion against the law and the FIR: a section from the code that did not govern the
    incident date, one whose stated reason shares no word with its official title (a model confusing
    section numbers), one the FIR already cites, or a new suggestion to check.
    """
    legal = analyse_sections(normalise_for_legal(text), incident_date)
    cited = {_norm(m["bns"]) for m in legal["mapped"] if m.get("bns")} | {
        _norm(m["ipc"]) for m in legal["mapped"] if m.get("ipc")
    }
    checked = []
    for item in suggested[:4]:
        if not isinstance(item, dict) or not str(item.get("section", "")).strip():
            continue
        item_code = str(item.get("code", "")).strip().upper() or code
        section = str(item["section"]).strip()
        reason = str(item.get("reason", "")).strip()
        title = _TITLES.get((item_code, _norm(section)))
        if item_code in {"IPC", "BNS"} and item_code != code:
            status = "wrong_code"
        elif _norm(section) in cited:
            status = "cited_in_fir"
        elif title and not _stems(title) & _stems(reason):
            status = "title_differs"
        else:
            status = "new"
        checked.append({"code": item_code, "section": section, "reason": reason, "official_title": title,
                        "status": status})
    return checked


def _parse(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(_FENCE.sub("", raw.strip()))
    except json.JSONDecodeError as exc:
        raise ExternalServiceError("Gemini returned a reply that is not JSON") from exc
    if not isinstance(data, dict):
        raise ExternalServiceError("Gemini returned an unexpected reply")
    return data


async def case_brief(
    text: str, incident_date: date | datetime | None = None, *, client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Redact, ask Gemini for a structured English brief, and check its section suggestions."""
    if not enabled():
        raise ExternalServiceError("Gemini is not configured: set GEMINI_API_KEY")
    redaction = redact(normalise_digits(text or ""))
    sent = redaction.text[:_MAX_CHARS]
    code = governing_code(incident_date)
    payload = {
        "systemInstruction": {"parts": [{"text": _instruction(code)}]},
        "contents": [{"role": "user", "parts": [{"text": f"FIR text:\n{sent}"}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
        },
    }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=settings.GEMINI_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            _ENDPOINT.format(model=settings.GEMINI_MODEL),
            json=payload,
            # In a header, not the URL: query strings end up in proxy and access logs.
            headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError("Gemini could not be reached", detail=type(exc).__name__) from exc
    finally:
        if owned:
            await client.aclose()

    if response.status_code != 200:
        try:
            reason = response.json().get("error", {}).get("message")
        except ValueError:
            reason = None
        raise ExternalServiceError(f"Gemini returned HTTP {response.status_code}", detail=reason)
    body = response.json()
    candidates = body.get("candidates") or []
    if not candidates:
        blocked = (body.get("promptFeedback") or {}).get("blockReason")
        raise ExternalServiceError("Gemini returned no answer", detail=blocked)
    parts = (candidates[0].get("content") or {}).get("parts") or []
    data = _parse("".join(p.get("text", "") for p in parts if not p.get("thought")))

    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    return {
        "model": settings.GEMINI_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
        "governing_code": code,
        "redactions": dict(redaction.counts),
        "chars_sent": len(sent),
        "english": str(data.get("english", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "offence": str(data.get("offence", "")).strip(),
        "modus_operandi": _strings(data.get("modus_operandi"), 5),
        "facts": {k: str(facts.get(k, "") or "").strip() for k in ("when", "where", "property", "weapon")},
        "suggested_sections": _check_sections(data.get("suggested_sections") or [], text, incident_date, code),
        "missing": _strings(data.get("missing"), 5),
    }


_OCR_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "application/pdf"}
_OCR_INSTRUCTION = (
    "You are an OCR engine for Indian police First Information Report (FIR) forms. Transcribe ALL text in the "
    "image exactly as written, printed and handwritten, in its original script and language (Hindi, Marathi, "
    "Bengali, Tamil, English or any other). Do not translate, summarise, correct or add anything. Keep the reading "
    "order of the form: field label, then the value written next to it, one field per line. Write an illegible "
    "word as [?]. Return only the transcription."
)


async def read_document(data: bytes, mime_type: str, *, client: httpx.AsyncClient | None = None) -> str:
    """Transcribe a scanned or photographed FIR, handwriting included, in its own script."""
    if not enabled():
        raise ExternalServiceError("Gemini is not configured: set GEMINI_API_KEY")
    if mime_type not in _OCR_TYPES:
        raise ExternalServiceError(f"Unsupported file type for handwriting OCR: {mime_type}")
    payload = {
        "systemInstruction": {"parts": [{"text": _OCR_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(data).decode()}},
            {"text": "Transcribe this FIR."},
        ]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192},
    }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=max(settings.GEMINI_TIMEOUT_SECONDS, 90.0))
    try:
        response = await client.post(_ENDPOINT.format(model=settings.GEMINI_MODEL), json=payload,
                                      headers={"x-goog-api-key": settings.GEMINI_API_KEY})
    except httpx.HTTPError as exc:
        raise ExternalServiceError("Handwriting OCR could not be reached", detail=type(exc).__name__) from exc
    finally:
        if owned:
            await client.aclose()
    if response.status_code != 200:
        try:
            reason = response.json().get("error", {}).get("message")
        except ValueError:
            reason = None
        raise ExternalServiceError(f"Handwriting OCR returned HTTP {response.status_code}", detail=reason)
    candidates = response.json().get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    text = _FENCE.sub("", text).strip()
    if not text:
        raise ExternalServiceError("No text could be read from this image")
    return text


# The crime types a case may carry; "other" last so it reads as the fallback.
CRIME_TYPES = (*sorted(VALID_CRIME_TYPES - {"other"}), "other")
_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "crime_type": {"type": "STRING", "enum": list(CRIME_TYPES)},
        "confidence": {"type": "NUMBER"},
        "severity": {"type": "INTEGER"},
        "reasoning": _STR,
        "people": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": _STR,
                    "role": {"type": "STRING", "enum": ["complainant", "victim", "accused", "suspect", "witness", "other"]},
                    "detail": _STR,
                },
                "required": ["name", "role"],
            },
        },
        "timeline": {"type": "ARRAY", "items": _STR},
        "motive": _STR,
        "relationship": _STR,
        "premeditated": {"type": "BOOLEAN"},
        "violence": {"type": "BOOLEAN"},
        "injury": {"type": "BOOLEAN"},
        "weapons": {"type": "ARRAY", "items": _STR},
        "red_flags": {"type": "ARRAY", "items": _STR},
    },
    "required": ["crime_type", "confidence", "severity", "reasoning", "people", "timeline", "motive",
                 "relationship", "premeditated", "violence", "injury", "weapons", "red_flags"],
}
_CONTEXT_INSTRUCTION = (
    "You read Indian police FIRs in any Indian language and explain what happened, in English. Personal "
    "identifiers are masked as [PHONE], [AADHAAR], [UPI], [BANK_ACCOUNT] and similar: keep the masks and never "
    "guess them. Use only what the FIR states; add no facts and do not profile anyone. Pick the one crime_type "
    "that best fits the whole narrative, reading negation, sequence and intent rather than keywords (online or "
    "UPI cheating is cybercrime, rash driving is traffic_violation); confidence is 0 to 1. severity is 1 (minor) to 5 (grave, life threatening). "
    "reasoning is one sentence under 30 words. people lists each named or described person with their role and "
    "a short detail. timeline is up to six short events in order. motive and relationship (between accused and "
    "victim) are short phrases, or an empty string when the FIR is silent. red_flags are up to four investigative "
    "signals the text actually supports, such as an organised gang, a repeat pattern, an insider or a threat to a "
    "witness."
)


async def understand(text: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Contextual reading of an FIR: who did what to whom, in what order, why, and how serious it is."""
    if not enabled():
        raise ExternalServiceError("Gemini is not configured: set GEMINI_API_KEY")
    redaction = redact(normalise_digits(text or ""))
    payload = {
        "systemInstruction": {"parts": [{"text": _CONTEXT_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": f"FIR text:\n{redaction.text[:_MAX_CHARS]}"}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": _CONTEXT_SCHEMA,
        },
    }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=settings.GEMINI_TIMEOUT_SECONDS)
    try:
        response = await client.post(_ENDPOINT.format(model=settings.GEMINI_MODEL), json=payload,
                                      headers={"x-goog-api-key": settings.GEMINI_API_KEY})
    except httpx.HTTPError as exc:
        raise ExternalServiceError("Context model could not be reached", detail=type(exc).__name__) from exc
    finally:
        if owned:
            await client.aclose()
    if response.status_code != 200:
        raise ExternalServiceError(f"Context model returned HTTP {response.status_code}")
    candidates = response.json().get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    data = _parse("".join(p.get("text", "") for p in parts if not p.get("thought")))

    people = [
        {"name": str(p.get("name", "")).strip(), "role": str(p.get("role") or "other"),
         "detail": str(p.get("detail") or "").strip()}
        for p in (data.get("people") or []) if isinstance(p, dict) and str(p.get("name", "")).strip()
    ][:8]
    return {
        "crime_type": data.get("crime_type") if data.get("crime_type") in CRIME_TYPES else "other",
        "confidence": round(min(max(float(data.get("confidence") or 0), 0.0), 1.0), 2),
        "severity": min(max(int(data.get("severity") or 1), 1), 5),
        "reasoning": str(data.get("reasoning", "")).strip(),
        "people": people,
        "timeline": _strings(data.get("timeline"), 6),
        "motive": str(data.get("motive") or "").strip(),
        "relationship": str(data.get("relationship") or "").strip(),
        "premeditated": bool(data.get("premeditated")),
        "violence": bool(data.get("violence")),
        "injury": bool(data.get("injury")),
        "weapons": _strings(data.get("weapons"), 5),
        "red_flags": _strings(data.get("red_flags"), 4),
    }
