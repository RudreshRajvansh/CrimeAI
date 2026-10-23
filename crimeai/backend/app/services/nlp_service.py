"""FIR intake and NLP orchestration."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import AlreadyExistsError, NLPServiceError, ValidationError
from app.core.logging import get_logger
from app.db.redis import CacheManager
from app.models.fir import FIRReport
from app.nlp import gemini
from app.nlp.documents import SUPPORTED_TYPES, ExtractedText, extract_text, sniff_content_type
from app.nlp.parsers.fir_pipeline import process_fir_text
from app.realtime.broker import publish_alert
from app.repositories.fir_repository import FIRRepository
from app.services import entity_graph
from app.services.storage import load_document, store_document
from app.workers.dispatch import enqueue

logger = get_logger(__name__)

_PROCESS_TASK = "app.workers.tasks.nlp_tasks.process_fir"


class NLPService:
    def __init__(self, db: AsyncSession, redis: Redis | None = None) -> None:
        self.db = db
        self.firs = FIRRepository(db)
        self.redis = redis
        self.cache = CacheManager(redis, namespace="nlp") if redis is not None else None

    async def submit_text(
        self, fir_number: str, raw_text: str, submitted_by: uuid.UUID,
        crime_id: uuid.UUID | None = None, incident_date: datetime | None = None,
    ) -> FIRReport:
        await self._ensure_unique(fir_number)
        fir = await self.firs.create(
            fir_number=fir_number, raw_text=raw_text, crime_id=crime_id,
            submitted_by=submitted_by, incident_date=incident_date, nlp_status="pending",
        )
        await self.db.commit()
        enqueue(_PROCESS_TASK, str(fir.id))
        return fir

    async def submit_document(
        self, fir_number: str, filename: str | None, data: bytes, submitted_by: uuid.UUID,
        crime_id: uuid.UUID | None = None, incident_date: datetime | None = None,
    ) -> FIRReport:
        content_type = sniff_content_type(data)
        if content_type not in SUPPORTED_TYPES:
            raise ValidationError("Unsupported document. Upload a PDF, PNG, JPEG, WebP, TIFF or UTF-8 text file.")
        await self._ensure_unique(fir_number)
        stored = await store_document(fir_number, filename, data, content_type)
        fir = await self.firs.create(
            fir_number=fir_number,
            raw_text="",
            crime_id=crime_id,
            submitted_by=submitted_by,
            incident_date=incident_date,
            file_url=stored.url,
            file_type=content_type,
            file_sha256=stored.sha256,
            nlp_status="pending",
        )
        await self.db.commit()
        enqueue(_PROCESS_TASK, str(fir.id))
        return fir

    async def _ensure_unique(self, fir_number: str) -> None:
        if await self.firs.get_by_fir_number(fir_number):
            raise AlreadyExistsError(f"FIR number '{fir_number}' already registered")

    async def process(self, fir_id: uuid.UUID) -> dict[str, Any]:
        """Worker entry point. Idempotent: a FIR another worker holds, or one already done, is skipped."""
        fir = await self.firs.claim(fir_id)
        if fir is None:
            return {"status": "skipped", "fir_id": str(fir_id)}
        try:
            text = fir.raw_text
            if not text and fir.file_url:
                extracted = await run_in_threadpool(extract_text, await load_document(fir.file_url), fir.file_type)
                if extracted.needs_ocr and gemini.enabled():
                    try:
                        read = await gemini.read_document(await load_document(fir.file_url), fir.file_type)
                        extracted = ExtractedText(read, "vision_ocr")
                    except Exception as exc:
                        logger.warning("fir_ocr_failed", fir_id=str(fir_id), error=type(exc).__name__)
                if extracted.needs_ocr:
                    await self.firs.fail(fir_id, "Document has no text layer; queued for OCR", status="awaiting_ocr")
                    await self.db.commit()
                    return {"status": "awaiting_ocr", "fir_id": str(fir_id)}
                text = extracted.text
                fir.raw_text = text
            output = await run_in_threadpool(process_fir_text, text, fir.fir_number, fir.incident_date)
            entities = await _with_context(output["entities"], text)
            await self.firs.complete(fir_id, entities, entities["overall_confidence"], output["redacted_text"])
            fir.language = entities["language"]["code"]
            alerts = []
            if fir.crime_id:
                linked = await entity_graph.link_crime(self.db, fir.crime_id, text, fir_id, fir.incident_date)
                alerts = await entity_graph.link_alerts(self.db, linked)
            await self.db.commit()
            for alert in alerts if self.redis is not None else []:
                await publish_alert(self.redis, alert)
            return {"status": "completed", "fir_id": str(fir_id), "crime_type": entities["crime_type"],
                    "language": fir.language, "links": len(alerts)}
        except Exception as exc:
            await self.db.rollback()
            await self.firs.fail(fir_id, f"{type(exc).__name__}: {exc}")
            await self.db.commit()
            raise

    async def reprocess(self, fir_id: uuid.UUID) -> str | None:
        await self.firs.reset_for_reprocess(fir_id)
        await self.db.commit()
        return enqueue(_PROCESS_TASK, str(fir_id))

    async def extract_inline(self, text: str, incident_date: datetime | None = None) -> dict[str, Any]:
        # The date decides IPC vs BNS, so it is part of the cache key.
        stamp = incident_date.date().isoformat() if incident_date else ""
        key = f"extract:{hashlib.sha256(f'{stamp}|{text}'.encode()).hexdigest()}"
        if self.cache and (cached := await self.cache.get(key)):
            return {**cached, "from_cache": True}
        try:
            output = await run_in_threadpool(process_fir_text, text, None, incident_date)
        except NLPServiceError:
            raise
        result = {**await _with_context(output["entities"], text), "redacted_text": output["redacted_text"]}
        if self.cache:
            await self.cache.set(key, result, ttl=300)
        return result


async def _with_context(entities: dict[str, Any], text: str) -> dict[str, Any]:
    """
    Add the contextual reading (roles, sequence, intent, severity) to the rule-based entities. When the rules
    found no crime type, or are less sure than the contextual reading, the contextual reading decides it and the
    rule-based answer is kept beside it. A confident reading (0.75 and up) also wins, because keywords miss
    negation and sequence. Best effort: without the model the entities come back unchanged.
    """
    if not gemini.enabled() or len((text or "").strip()) < 20:
        return entities
    try:
        context = await gemini.understand(text)
    except Exception as exc:
        logger.warning("fir_context_failed", error=type(exc).__name__)
        return entities
    merged = {**entities, "context": context}
    rules_confidence = float(entities.get("crime_type_confidence") or 0)
    if context["crime_type"] != "other" and (
        not entities.get("crime_type") or context["confidence"] >= 0.75 or context["confidence"] > rules_confidence
    ):
        merged["rules_crime_type"] = entities.get("crime_type")
        merged["crime_type"] = context["crime_type"]
        merged["crime_type_confidence"] = context["confidence"]
    merged["inferred_severity"] = max(int(entities.get("inferred_severity") or 0), context["severity"])
    merged["has_weapon"] = bool(entities.get("has_weapon")) or bool(context["weapons"])
    merged["has_injury"] = bool(entities.get("has_injury")) or context["injury"]
    merged["weapons"] = list(dict.fromkeys([*(entities.get("weapons") or []), *context["weapons"]]))
    return merged
