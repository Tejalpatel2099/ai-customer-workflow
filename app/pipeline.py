"""The AI pipeline: turn a raw ticket into a triaged, drafted reply.

Two engines, one interface:

- ``llm`` mode — used when ``OPENAI_API_KEY`` is set. Calls the OpenAI
  chat completions API and asks for strict JSON back.
- ``demo`` mode — a deterministic, rule-based engine used when no key is
  configured (and as a fallback if the API call fails). It is *always*
  labelled demo mode in its results, in the API responses, and in the UI.
  Demo output is never passed off as model output.

Both engines return the same :class:`PipelineResult`, so the workflow
code does not care which one ran — only the ``mode`` field differs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class PipelineResult:
    category: str        # Billing | Technical | Account | Shipping | General
    urgency: str         # low | medium | high
    sentiment: str       # positive | neutral | negative
    summary: str         # one line
    draft_reply: str     # ready for a human to approve / edit / reject
    next_action: str     # what the agent should do next
    mode: str            # "llm" or "demo"


# --------------------------------------------------------------------------
# Demo engine (rule-based, deterministic)
# --------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Billing": ["charged", "charge", "refund", "invoice", "bill", "payment",
                "subscription", "price", "discount", "coupon", "receipt"],
    "Technical": ["error", "bug", "crash", "not working", "broken", "app",
                  "login page", "freeze", "slow", "update failed", "500"],
    "Account": ["password", "account", "sign in", "sign-in", "locked out",
                "two-factor", "2fa", "email change", "profile", "delete my account"],
    "Shipping": ["delivery", "package", "shipping", "tracking", "arrived",
                 "order", "shipment", "courier", "address"],
}

HIGH_URGENCY_WORDS = ["urgent", "immediately", "asap", "outage", "down",
                      "charged twice", "fraud", "locked out", "cannot access",
                      "can't access", "emergency", "lawsuit", "cancel everything"]
MEDIUM_URGENCY_WORDS = ["soon", "waiting", "days", "problem", "issue",
                        "wrong", "missing", "damaged"]

NEGATIVE_WORDS = ["angry", "frustrated", "terrible", "awful", "unacceptable",
                  "disappointed", "furious", "worst", "ridiculous", "upset"]
POSITIVE_WORDS = ["thank", "great", "love", "awesome", "appreciate", "happy"]

NEXT_ACTIONS: dict[str, dict[str, str]] = {
    "Billing": {
        "high": "Escalate to the billing team today and review the charge before replying further.",
        "medium": "Review the invoice in the billing console, then send the approved reply.",
        "low": "Send the approved reply; no console action needed.",
    },
    "Technical": {
        "high": "Page the on-call engineer and send the approved reply with a workaround if one exists.",
        "medium": "Reproduce the issue from the ticket details and attach findings to the ticket.",
        "low": "Send the approved reply and link the relevant help article.",
    },
    "Account": {
        "high": "Verify identity, unlock the account from the admin console, then send the approved reply.",
        "medium": "Verify identity in the admin console, then send the approved reply.",
        "low": "Send the approved reply with the self-service steps.",
    },
    "Shipping": {
        "high": "Contact the courier today for a trace and send the approved reply with the case number.",
        "medium": "Check the tracking scan history, then send the approved reply.",
        "low": "Send the approved reply with the tracking link.",
    },
    "General": {
        "high": "Reply within the hour using the approved draft and flag the ticket for a lead review.",
        "medium": "Send the approved reply.",
        "low": "Send the approved reply.",
    },
}

DRAFT_OPENERS: dict[str, str] = {
    "Billing": "I have reviewed the billing details on your account",
    "Technical": "I am sorry the product is not behaving as expected — I have logged the details you shared with our engineering team",
    "Account": "I can help you regain access to your account safely",
    "Shipping": "I have checked the latest status of your shipment",
    "General": "Thank you for reaching out and for the details you shared",
}

DRAFT_NEXT_STEPS: dict[str, str] = {
    "Billing": "If a charge was incorrect, we will correct it and confirm the updated amount by email — you do not need to do anything else.",
    "Technical": "We will update you as soon as we have findings, and I will keep this ticket open until the issue is confirmed fixed.",
    "Account": "For your security, please follow the verification step in the next email from us; after that your access will be restored.",
    "Shipping": "I will keep monitoring the shipment and update you if the status does not change within one business day.",
    "General": "If anything else comes up, just reply to this email and it will come straight back to me.",
}


def _score_category(text: str) -> str:
    scores = {cat: sum(1 for kw in kws if kw in text)
              for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


def _urgency(text: str, category: str) -> str:
    if any(w in text for w in HIGH_URGENCY_WORDS):
        return "high"
    if any(w in text for w in MEDIUM_URGENCY_WORDS):
        return "medium"
    # Account lockouts and billing disputes are rarely low-stakes.
    return "medium" if category in ("Billing", "Account") else "low"


def _sentiment(text: str) -> str:
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def _summarise(name: str, subject: str, body: str) -> str:
    first = re.split(r"(?<=[.!?])\s+", body.strip())[0]
    if len(first) > 140:
        first = first[:137].rstrip() + "…"
    return f"{name} — {subject}. {first}"


def demo_pipeline(ticket) -> PipelineResult:
    """Deterministic rule-based triage. Clearly demo mode, by design."""
    text = f"{ticket.subject}\n{ticket.body}".lower()
    category = _score_category(text)
    urgency = _urgency(text, category)
    sentiment = _sentiment(text)
    summary = _summarise(ticket.customer_name, ticket.subject, ticket.body)
    first_name = ticket.customer_name.split()[0]
    draft = (
        f"Hi {first_name},\n\n"
        f"Thank you for contacting us. {DRAFT_OPENERS[category]}. "
        f"{DRAFT_NEXT_STEPS[category]}\n\n"
        f"Thank you for your patience,\nSupport Team"
    )
    return PipelineResult(
        category=category,
        urgency=urgency,
        sentiment=sentiment,
        summary=summary,
        draft_reply=draft,
        next_action=NEXT_ACTIONS[category][urgency],
        mode="demo",
    )


# --------------------------------------------------------------------------
# LLM engine (OpenAI chat completions)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You triage customer-support tickets. Reply with strict JSON only, "
    "with keys: category (Billing|Technical|Account|Shipping|General), "
    "urgency (low|medium|high), sentiment (positive|neutral|negative), "
    "summary (one line), draft_reply (a polite, ready-to-send reply), "
    "next_action (one concrete instruction for the support agent)."
)


def llm_pipeline(ticket) -> PipelineResult:
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Customer: {ticket.customer_name}\n"
                f"Subject: {ticket.subject}\n\n{ticket.body}"},
        ],
    }
    response = httpx.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    data = json.loads(response.json()["choices"][0]["message"]["content"])
    return PipelineResult(
        category=data["category"],
        urgency=data["urgency"],
        sentiment=data["sentiment"],
        summary=data["summary"],
        draft_reply=data["draft_reply"],
        next_action=data["next_action"],
        mode="llm",
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def engine_mode() -> str:
    """Which engine a run would use right now (for /api/stats)."""
    return "llm" if os.environ.get("OPENAI_API_KEY") else "demo"


def run_pipeline(ticket) -> PipelineResult:
    """Run the best available engine.

    If the LLM call fails for any reason (network, quota, malformed
    response), fall back to the demo engine — and say so via mode="demo",
    so the UI never claims a rule-based draft came from a model.
    """
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return llm_pipeline(ticket)
        except Exception:
            return demo_pipeline(ticket)
    return demo_pipeline(ticket)
