"""
One-off manual test for email outreach — sends a single real email using the
"Step 1 - Intro" template, bypassing the scheduled_sends queue entirely so it
never touches real lead data.

Usage:
    python test_email.py you@example.com
"""

import sys
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

import db
from outreach import DEFAULT_TEMPLATES, render

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_email.py you@example.com")
        sys.exit(1)

    to_addr = sys.argv[1]

    test_lead = {
        "business_name": "Test Vet Clinic",
        "first_name": None,
        "contact_name": None,
        "email": to_addr,
        "opener": "Noticed you've been serving West London pet owners for over a decade.",
        "city": "London",
        "country": "UK",
    }

    template = DEFAULT_TEMPLATES[0]  # "Step 1 -- Intro"
    subject = render(template["subject"], test_lead)
    body = render(template["body"], test_lead)

    print(f"Subject: {subject}")
    print("--- Body ---")
    print(body)
    print("------------")

    queued = db.enqueue_outbound(
        org_id=db.DEFAULT_ORG_ID,
        source="manual_test",
        to_email=to_addr,
        subject=subject,
        body=body,
        idempotency_key=f"manual-test:{uuid4()}",
        note="manual outbound gateway test",
    )
    print(f"Queued outbound message #{queued['id']} ({queued['status']}).")
