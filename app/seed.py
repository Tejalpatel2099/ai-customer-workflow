"""Seed data: ten realistic support tickets across all five categories.

These are sample tickets written for this demo. ``seed_if_empty`` inserts
them only when the tickets table is empty, so restarts never duplicate
them and real tickets are never overwritten.
"""

from __future__ import annotations

from . import models

SAMPLE_TICKETS: list[dict] = [
    {
        "customer_name": "Priya Nair",
        "customer_email": "priya.nair@example.com",
        "subject": "Charged twice for my subscription",
        "body": ("I was charged twice for my monthly subscription today and I need "
                 "this fixed immediately. The same amount appears two times on my "
                 "card statement. Please refund one of the charges as soon as possible."),
        "category": "Billing",
    },
    {
        "customer_name": "Marcus Lee",
        "customer_email": "marcus.lee@example.com",
        "subject": "App crashes when I export a report",
        "body": ("Since the last update, the app crashes every time I try to export "
                 "a report to PDF. I get an error and the app closes. This is blocking "
                 "my weekly reporting, so a workaround would help."),
        "category": "Technical",
    },
    {
        "customer_name": "Sofia Ramirez",
        "customer_email": "sofia.ramirez@example.com",
        "subject": "Locked out of my account",
        "body": ("I forgot my password and after a few attempts I am now locked out "
                 "of my account. I cannot access my orders or my saved payment method. "
                 "Please help me get back in urgently."),
        "category": "Account",
    },
    {
        "customer_name": "Daniel Kim",
        "customer_email": "daniel.kim@example.com",
        "subject": "Package has not arrived after 10 days",
        "body": ("My order was supposed to arrive last week and the tracking has not "
                 "moved in five days. I have been waiting and I am starting to worry "
                 "the package is lost. Can you check where it is?"),
        "category": "Shipping",
    },
    {
        "customer_name": "Amara Osei",
        "customer_email": "amara.osei@example.com",
        "subject": "Question about team plans",
        "body": ("We are a small team of six and I would love to know whether the "
                 "team plan includes shared workspaces. Thank you for the great "
                 "product, we are happy users so far."),
        "category": "General",
    },
    {
        "customer_name": "Jonas Weber",
        "customer_email": "jonas.weber@example.com",
        "subject": "Invoice shows the wrong amount",
        "body": ("My latest invoice shows a higher amount than the price on your "
                 "website. The discount from my coupon does not seem to be applied. "
                 "Could you check the invoice and correct it?"),
        "category": "Billing",
    },
    {
        "customer_name": "Lena Fischer",
        "customer_email": "lena.fischer@example.com",
        "subject": "Dashboard is very slow to load",
        "body": ("The dashboard takes almost a minute to load since yesterday and "
                 "sometimes shows a 500 error. Everything else on my network is "
                 "fine. It is frustrating because I use it all day for work."),
        "category": "Technical",
    },
    {
        "customer_name": "Omar Haddad",
        "customer_email": "omar.haddad@example.com",
        "subject": "Change the email on my account",
        "body": ("I need to change the email address on my account to my new work "
                 "email. I could not find the option in my profile settings. "
                 "Could you point me to the right place?"),
        "category": "Account",
    },
    {
        "customer_name": "Grace Mbeki",
        "customer_email": "grace.mbeki@example.com",
        "subject": "Delivery arrived damaged",
        "body": ("My delivery arrived today but the box was crushed and the item "
                 "inside is damaged. This is unacceptable for a gift order. "
                 "I would like a replacement sent as soon as possible."),
        "category": "Shipping",
    },
    {
        "customer_name": "Tom Becker",
        "customer_email": "tom.becker@example.com",
        "subject": "Do you offer an API?",
        "body": ("We are evaluating tools for our internal workflow and I am "
                 "wondering whether you offer an API for creating tickets "
                 "programmatically. Any documentation link would be appreciated."),
        "category": "General",
    },
]


def seed_if_empty() -> int:
    """Insert the sample tickets when the table is empty.

    Returns how many tickets were inserted (0 when data already exists).
    Events are logged directly (not via create_ticket) so seeding does not
    spam the activity feed with ten 'created' entries.
    """
    if models.count_tickets() > 0:
        return 0
    now = models._now()
    with models.connect() as conn:
        for t in SAMPLE_TICKETS:
            conn.execute(
                """INSERT INTO tickets (customer_name, customer_email, subject, body,
                                        category, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'New', ?, ?)""",
                (t["customer_name"], t["customer_email"], t["subject"], t["body"],
                 t["category"], now, now),
            )
    models.log_event(None, "seeded", f"Seeded {len(SAMPLE_TICKETS)} sample tickets")
    return len(SAMPLE_TICKETS)
