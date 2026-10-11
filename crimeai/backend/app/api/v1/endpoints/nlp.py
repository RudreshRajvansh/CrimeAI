"""Inline NLP utilities: entity extraction, classification, semantic search, legal section mapping."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.v1.dependencies.auth import Admin, Analyst, Officer, Viewer
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.redis import get_redis
from app.db.session import get_db
from app.nlp import gemini
from app.nlp.legal.bns_crosswalk import analyse_sections
from app.nlp.parsers.crime_classifier import classify_crime_type
from app.repositories.fir_repository import FIRRepository
from app.services.audit_service import record_audit
from app.services.ml_service import MLService
from app.services.nlp_service import NLPService
from app.workers.dispatch import enqueue

router = APIRouter()
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


class ExtractRequest(BaseModel):
    text: str = Field(..., min_length=20, max_length=10000)


class GeminiBriefRequest(BaseModel):
    text: str = Field(..., min_length=20, max_length=10000)
    incident_date: date | None = None


class SimilarityRequest(BaseModel):
    text: str = Field(..., min_length=10, max_length=5000)
    top_k: int = Field(default=5, ge=1, le=20)
    crime_type: str | None = None
    min_similarity: float = Field(default=0.5, ge=0.0, le=1.0)


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=10, max_length=5000)


class LegalSectionsRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=10000)
    incident_date: date | None = None


@router.post("/extract", summary="Extract entities, legal sections and PII-redacted text")
@limiter.limit(settings.RATE_LIMIT_ML)
async def extract_entities(request: Request, body: ExtractRequest, user: Officer, db: DbDep, redis: RedisDep) -> dict:
    return await NLPService(db, redis).extract_inline(body.text)


@router.get("/gemini", summary="Whether Gemini case briefs are switched on, and with which model")
async def gemini_status(user: Viewer) -> dict:
    return {"enabled": gemini.enabled(), "model": settings.GEMINI_MODEL if gemini.enabled() else None}


@router.post("/gemini-brief", summary="Gemini brief of pasted FIR text, without saving it (redacted first, audited)")
@limiter.limit("10/minute")
async def gemini_preview(request: Request, body: GeminiBriefRequest, user: Officer, db: DbDep) -> dict:
    if not gemini.enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Gemini is not configured on this server")
    brief = await gemini.case_brief(body.text, body.incident_date)
    await record_audit(db, request, user, action="nlp.gemini_brief", resource_type="fir_text",
                       metadata={"model": brief["model"], "chars_sent": brief["chars_sent"],
                                 "redactions": brief["redactions"]})
    return brief


@router.post("/similarity", summary="Semantic search over crime narratives (pgvector)")
@limiter.limit(settings.RATE_LIMIT_ML)
async def find_similar(request: Request, body: SimilarityRequest, user: Analyst, db: DbDep, redis: RedisDep) -> dict:
    results = await MLService(db, redis).find_similar(
        body.text, top_k=body.top_k, crime_type_filter=body.crime_type, min_similarity=body.min_similarity
    )
    return {"top_k": body.top_k, "min_similarity": body.min_similarity, "results": results, "total_found": len(results)}


@router.post("/classify", summary="Classify crime type from free text")
async def classify(body: ClassifyRequest, user: Officer) -> dict:
    result = await run_in_threadpool(classify_crime_type, body.text)
    return {
        "crime_type": result["crime_type"],
        "confidence": result["confidence"],
        "method": result["method"],
        "top_predictions": result["keyword_predictions"],
    }


@router.post("/legal-sections", summary="Map cited IPC/BNS sections and flag code mismatches")
async def legal_sections(body: LegalSectionsRequest, user: Viewer) -> dict:
    return analyse_sections(body.text, body.incident_date)


@router.get("/pending", summary="FIRs awaiting NLP processing")
async def pending(admin: Admin, db: DbDep) -> dict:
    ids = await FIRRepository(db).claimable_ids(limit=200)
    return {"total": len(ids), "fir_ids": [str(i) for i in ids]}


@router.post("/process-pending", status_code=status.HTTP_202_ACCEPTED)
async def process_pending(admin: Admin) -> dict:
    return {"task_id": enqueue("app.workers.tasks.nlp_tasks.process_pending_firs")}
