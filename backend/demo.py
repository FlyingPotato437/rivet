"""Local development demo configuration. Normal Clerk verification still applies."""

import os

DEMO_EMAIL = "rivet-demo+clerk_test@example.com"


def demo_config():
    # Clerk's reserved test email works only in test mode. Never advertise it
    # from a shared deployment or a production instance.
    if not (
        os.getenv("RIVET_DEMO_ENABLED") == "true"
        and os.getenv("RIVET_LOCAL_ONLY", "true") == "true"
        and os.getenv("CLERK_SECRET_KEY", "").startswith("sk_test_")
        and os.getenv("VITE_CLERK_PUBLISHABLE_KEY", "").startswith("pk_test_")
        and os.getenv("RIVET_DEMO_USER_ID")
        and os.getenv("RIVET_DEMO_ORG_ID")
    ):
        return None
    return {
        "email": DEMO_EMAIL,
        "user_id": os.environ["RIVET_DEMO_USER_ID"],
        "organization_id": os.environ["RIVET_DEMO_ORG_ID"],
    }
