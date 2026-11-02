"""
One-off manual test for the CallMissa voice agent — dials a single number
directly via voice_caller.dispatch_call(), bypassing the DB/queue entirely.

Only works with a US number while VAPI_PHONE_NUMBER_ID is a free Vapi number
(no international calling on free numbers).

Usage:
    python -m scripts.send_test_call +15551234567
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from voice_caller import dispatch_call

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.send_test_call +1XXXXXXXXXX")
        sys.exit(1)

    test_lead = {
        "id": 0,
        "business_name": "Test Clinic",
        "phone": sys.argv[1],
        "city": "Austin",
        "country": "USA",
        "website": "https://example.com",
        "last_review_days": 90,   # exercises the background-signal fallback
        "rating": None,
        "reviews": None,
    }

    result = dispatch_call(test_lead)
    print("Call dispatched:", result)
