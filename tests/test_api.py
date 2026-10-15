"""API happy-path tests using FastAPI's TestClient.

The app is pointed at a throwaway SQLite file (via WORKFLOW_DB_PATH)
before import, so tests never touch real data. Startup seeds the ten
sample tickets into that fresh database.
"""

import os
import tempfile

os.environ["WORKFLOW_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="workflow-test-"), "test.db")
os.environ.pop("OPENAI_API_KEY", None)  # force demo mode

from fastapi.testclient import TestClient  # noqa: E402

from app import models, seed  # noqa: E402
from app.main import app  # noqa: E402

# TestClient only runs startup handlers inside a `with` block, so run the
# same two startup steps explicitly against the throwaway database.
models.init_db()
seed.seed_if_empty()

client = TestClient(app)


def _first_ticket_id() -> int:
    tickets = client.get("/api/tickets").json()
    assert tickets, "seed data should exist"
    return tickets[0]["id"]


def test_seeded_tickets_are_listed():
    res = client.get("/api/tickets")
    assert res.status_code == 200
    tickets = res.json()
    assert len(tickets) == 10
    assert all(t["status"] == "New" for t in tickets)


def test_get_single_ticket_and_404():
    tid = _first_ticket_id()
    assert client.get(f"/api/tickets/{tid}").status_code == 200
    assert client.get("/api/tickets/99999").status_code == 404


def test_create_ticket():
    res = client.post("/api/tickets", json={
        "customer_name": "New Person",
        "customer_email": "new@example.com",
        "subject": "Refund please",
        "body": "I would like a refund for last month.",
        "category": "Billing",
    })
    assert res.status_code == 201
    assert res.json()["status"] == "New"
    assert client.get("/api/stats").json()["total"] == 11


def test_full_approval_flow():
    tid = _first_ticket_id()
    processed = client.post(f"/api/tickets/{tid}/process")
    assert processed.status_code == 200
    body = processed.json()
    assert body["status"] == "Pending approval"
    assert body["mode"] == "demo"          # honesty: labelled demo mode
    assert body["ai_category"] in ("Billing", "Technical", "Account",
                                   "Shipping", "General")
    assert body["draft_reply"]

    approved = client.post(f"/api/tickets/{tid}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "Resolved"


def test_edit_keeps_ticket_pending_then_reject():
    created = client.post("/api/tickets", json={
        "customer_name": "Edit Case", "customer_email": "edit@example.com",
        "subject": "Package missing",
        "body": "My delivery has not arrived and tracking is stuck.",
        "category": "Shipping",
    })
    tid = created.json()["id"]
    client.post(f"/api/tickets/{tid}/process")
    edited = client.post(f"/api/tickets/{tid}/edit",
                         json={"draft_reply": "Edited by a human agent."})
    assert edited.status_code == 200
    assert edited.json()["draft_reply"] == "Edited by a human agent."
    assert edited.json()["status"] == "Pending approval"

    rejected = client.post(f"/api/tickets/{tid}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "Needs attention"


def test_approve_without_processing_is_rejected():
    res = client.post("/api/tickets", json={
        "customer_name": "Skip Step", "customer_email": "skip@example.com",
        "subject": "Hi", "body": "No processing yet.",
    })
    tid = res.json()["id"]
    assert client.post(f"/api/tickets/{tid}/approve").status_code == 400


def test_stats_shape_and_approval_rate():
    stats = client.get("/api/stats").json()
    assert stats["mode"] == "demo"
    assert set(stats["by_status"]) == {"New", "Pending approval",
                                       "Resolved", "Needs attention"}
    assert stats["by_status"]["Resolved"] >= 1
    assert stats["by_status"]["Needs attention"] >= 1
    # One approval and one rejection happened in earlier tests.
    assert stats["approval_rate"] == 0.5
    assert stats["recent_activity"], "activity log should not be empty"
