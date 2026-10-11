"""Link analysis: identifiers (vehicles, phones, UPI IDs) that connect separate cases."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import Analyst, Officer
from app.core.exceptions import ValidationError
from app.db.session import get_db
from app.services import entity_graph
from app.services.audit_service import record_audit
from app.services.freeze_packet import build_packet

router = APIRouter()
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/overview", summary="Every identifier linking two or more cases, with those cases and active series")
async def graph_overview(user: Analyst, db: DbDep, min_cases: int = Query(2, ge=2, le=20)) -> dict:
    return await entity_graph.overview(db, min_cases=min_cases)


@router.get("/neighbourhood", summary="Expand around a case, entity or series node")
async def graph_neighbourhood(
    user: Analyst,
    db: DbDep,
    node: str = Query(..., pattern=r"^(crime|entity):[0-9a-f-]{36}$|^series:SER-[0-9A-F]{10}$"),
    depth: int = Query(2, ge=1, le=4),
) -> dict:
    try:
        return await entity_graph.neighbourhood(db, node, depth)
    except ValueError as exc:
        raise ValidationError("Invalid node id") from exc


@router.get("/search", summary="Find a vehicle number, phone number or UPI ID in the link graph")
async def graph_search(user: Analyst, db: DbDep, q: str = Query(..., min_length=2, max_length=64)) -> list[dict]:
    return await entity_graph.search(db, q)


@router.get(
    "/entities/{entity_id}/freeze-packet",
    summary="Draft one freeze/lien request citing every complaint linked to a UPI ID, account or phone (audited)",
)
async def freeze_packet(entity_id: uuid.UUID, request: Request, user: Officer, db: DbDep) -> dict:
    packet = await build_packet(db, entity_id)
    # The packet reveals the full identifier recovered from the FIRs, so every read is recorded.
    await record_audit(db, request, user, action="entity.freeze_packet", resource_type="entity", resource_id=str(entity_id))
    return packet
