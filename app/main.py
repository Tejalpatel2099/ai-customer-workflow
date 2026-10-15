"""FastAPI application: ticket inbox, AI processing, human approval.

The workflow in one paragraph: a ticket arrives with status ``New``.
``POST /process`` runs the AI pipeline and moves it to
``Pending approval`` — nothing is ever sent automatically. A human then
approves (``Resolved``), edits the draft (stays ``Pending approval``), or
rejects it (``Needs attention``). Every step is written to the activity
log that powers the dashboard.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import models, pipeline, seed

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="AI Customer Workflow Automation Platform", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    models.init_db()
    seed.seed_if_empty()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class TicketCreate(BaseModel):
    customer_name: str = Field(min_length=1)
    customer_email: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    category: str = "General"


class DraftEdit(BaseModel):
    draft_reply: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(ticket_id: int) -> models.Ticket:
    ticket = models.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _require_status(ticket: models.Ticket, *allowed: str) -> None:
    if ticket.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Ticket is '{ticket.status}'; this action needs one of {allowed}",
        )


# ---------------------------------------------------------------------------
# Ticket routes
# ---------------------------------------------------------------------------

@app.get("/api/tickets")
def list_tickets(status: str | None = Query(default=None),
                 category: str | None = Query(default=None)) -> list[dict]:
    return [t.to_dict() for t in models.list_tickets(status=status, category=category)]


@app.post("/api/tickets", status_code=201)
def create_ticket(payload: TicketCreate) -> dict:
    if payload.category not in models.CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"category must be one of {models.CATEGORIES}")
    ticket = models.create_ticket(payload.customer_name, payload.customer_email,
                                  payload.subject, payload.body, payload.category)
    return ticket.to_dict()


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> dict:
    return _get_or_404(ticket_id).to_dict()


@app.post("/api/tickets/{ticket_id}/process")
def process_ticket(ticket_id: int) -> dict:
    """Run the AI pipeline and park the ticket in the approval queue."""
    ticket = _get_or_404(ticket_id)
    _require_status(ticket, "New", "Needs attention")
    result = pipeline.run_pipeline(ticket)
    updated = models.update_ticket(
        ticket_id,
        status="Pending approval",
        ai_category=result.category,
        urgency=result.urgency,
        sentiment=result.sentiment,
        summary=result.summary,
        draft_reply=result.draft_reply,
        next_action=result.next_action,
        mode=result.mode,
        processed_at=models._now(),
    )
    models.log_event(ticket_id, "processed",
                     f"AI draft ready ({result.mode} mode, {result.category}, "
                     f"{result.urgency} urgency)")
    return updated.to_dict()


@app.post("/api/tickets/{ticket_id}/approve")
def approve_ticket(ticket_id: int) -> dict:
    """Human approves the draft: in a real system this is where the reply
    would be sent; here the ticket is marked Resolved and logged."""
    ticket = _get_or_404(ticket_id)
    _require_status(ticket, "Pending approval")
    updated = models.update_ticket(ticket_id, status="Resolved",
                                   decided_at=models._now())
    models.log_event(ticket_id, "approved", "Draft approved by agent; reply sent")
    return updated.to_dict()


@app.post("/api/tickets/{ticket_id}/reject")
def reject_ticket(ticket_id: int) -> dict:
    ticket = _get_or_404(ticket_id)
    _require_status(ticket, "Pending approval")
    updated = models.update_ticket(ticket_id, status="Needs attention",
                                   decided_at=models._now())
    models.log_event(ticket_id, "rejected", "Draft rejected by agent; needs attention")
    return updated.to_dict()


@app.post("/api/tickets/{ticket_id}/edit")
def edit_ticket(ticket_id: int, payload: DraftEdit) -> dict:
    """Edit the AI draft. The ticket stays in the approval queue — editing
    is not approving."""
    ticket = _get_or_404(ticket_id)
    _require_status(ticket, "Pending approval")
    updated = models.update_ticket(ticket_id, draft_reply=payload.draft_reply)
    models.log_event(ticket_id, "edited", "Draft edited by agent")
    return updated.to_dict()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats() -> dict:
    data = models.stats()
    # The UI uses this to show the demo-mode banner honestly.
    data["mode"] = pipeline.engine_mode()
    return data


# ---------------------------------------------------------------------------
# Frontend (single page, served by FastAPI)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
