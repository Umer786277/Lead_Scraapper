"""
Create a Supabase auth user from the command line.
Reads credentials from frontend/.env.local automatically.

Usage:
    python create_user.py

Needs SUPABASE_SERVICE_ROLE_KEY in frontend/.env.local to bypass
email validation. Get it from:
  Supabase dashboard -> Project Settings -> API Keys -> service_role secret
"""

import json
import sys
from pathlib import Path

import requests

# ── Load frontend/.env.local ─────────────────────────────────
env_file = Path(__file__).parent / "frontend" / ".env.local"
env: dict[str, str] = {}
for line in env_file.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

SUPABASE_URL      = env.get("NEXT_PUBLIC_SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
SERVICE_ROLE_KEY  = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("[ERR] NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY missing.")
    sys.exit(1)

# ── User to create ───────────────────────────────────────────
EMAIL    = "admin@gmail.com"
PASSWORD = "Brw@12345"

# ── Call Supabase Auth API ───────────────────────────────────
if SERVICE_ROLE_KEY:
    # Admin endpoint: bypasses email validation, auto-confirms the user
    print("Using service role key (admin endpoint)...")
    res = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={
            "apikey":        SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
            "Content-Type":  "application/json",
        },
        json={"email": EMAIL, "password": PASSWORD, "email_confirm": True},
        timeout=15,
    )
else:
    # Fallback: public signup (subject to Supabase email validation)
    print("No service role key found — using public signup...")
    res = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers={
            "apikey":       SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        },
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )

data = res.json()

if res.status_code in (200, 201) and data.get("id"):
    print(f"[OK] User created successfully")
    print(f"     Email : {data['email']}")
    print(f"     ID    : {data['id']}")
    if not data.get("confirmed_at") and not data.get("email_confirmed_at"):
        print("[WARN] Email not yet confirmed.")
        print("       Go to Supabase dashboard -> Authentication -> Users")
        print("       and click Confirm next to the user.")
    else:
        print("[OK] Email confirmed. You can sign in now.")

elif "already registered" in json.dumps(data).lower():
    print(f"[WARN] User already exists: {EMAIL}")
    print("       You can sign in directly on the login page.")

else:
    print(f"[ERR] Failed ({res.status_code}): {data}")
    print()
    print("Quick fix: create the user manually in Supabase dashboard")
    print("  -> Authentication -> Users -> Add user -> Create new user")
    print("  -> Check 'Auto Confirm User'")
    sys.exit(1)
