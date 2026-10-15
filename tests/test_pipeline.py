"""Tests for the AI pipeline's demo engine.

The demo engine is deterministic, so these tests can assert exact
behaviour. They also pin the honesty contract: with no API key set,
results must say mode == "demo".
"""

from types import SimpleNamespace

import pytest

from app import pipeline


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def make_ticket(subject, body, name="Test User"):
    return SimpleNamespace(subject=subject, body=body, customer_name=name,
                           customer_email="test@example.com")


def test_billing_double_charge_is_high_urgency():
    ticket = make_ticket(
        "Charged twice for my subscription",
        "I was charged twice today and need a refund immediately.",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.mode == "demo"
    assert result.category == "Billing"
    assert result.urgency == "high"
    assert result.sentiment in ("neutral", "negative")


def test_account_lockout_classification():
    ticket = make_ticket(
        "Locked out of my account",
        "I forgot my password and now I am locked out and cannot access anything.",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.category == "Account"
    assert result.urgency == "high"


def test_shipping_question_classification():
    ticket = make_ticket(
        "Where is my package?",
        "My delivery has not arrived and tracking has not moved in days.",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.category == "Shipping"
    assert result.urgency == "medium"


def test_general_question_is_low_urgency_and_positive():
    ticket = make_ticket(
        "Question about team plans",
        "Thank you for the great product, we love it. Do you have team plans?",
        name="Amara Osei",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.category == "General"
    assert result.urgency == "low"
    assert result.sentiment == "positive"


def test_angry_technical_ticket_is_negative():
    ticket = make_ticket(
        "App crashes constantly",
        "This is terrible, the app crashes every time and I am furious. Worst update ever.",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.category == "Technical"
    assert result.sentiment == "negative"


def test_draft_and_summary_are_personalised_and_nonempty():
    ticket = make_ticket(
        "Invoice question",
        "My invoice shows the wrong amount. Please check it.",
        name="Jonas Weber",
    )
    result = pipeline.run_pipeline(ticket)
    assert result.draft_reply.startswith("Hi Jonas,")
    assert "Jonas Weber" in result.summary
    assert result.next_action  # every category/urgency pair has one


def test_engine_mode_reports_demo_without_key():
    assert pipeline.engine_mode() == "demo"
