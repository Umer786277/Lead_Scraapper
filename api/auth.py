"""
JWT verification for Supabase-issued tokens.

Supports both signing schemes:
  • HS256 (legacy "JWT Secret" mode)  — uses SUPABASE_JWT_SECRET
  • ES256 / RS256 (new asymmetric mode, default for new projects)
    — fetches the public JWKS from the project's /auth/v1/.well-known/jwks.json

The JWKS URL is derived from the token's `iss` claim, so no extra config is
needed beyond the user already being able to authenticate against Supabase.
"""

import os
import time

import requests
from fastapi import HTTPException, status
from jose import JWTError, jwt

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

_jwks_cache: dict[str, tuple[float, dict]] = {}
_JWKS_TTL = 3600  # seconds


def _fetch_jwks(issuer: str) -> dict:
    """Fetch (and cache) the JWKS document for a given Supabase issuer URL."""
    cached = _jwks_cache.get(issuer)
    if cached and time.time() - cached[0] < _JWKS_TTL:
        return cached[1]
    url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    jwks = r.json()
    _jwks_cache[issuer] = (time.time(), jwks)
    return jwks


def _verify_asymmetric(token: str, alg: str, kid: str | None) -> dict:
    """Verify an ES256/RS256 token against the project JWKS."""
    unverified = jwt.get_unverified_claims(token)
    issuer = unverified.get("iss")
    if not issuer:
        raise HTTPException(401, "Token missing iss claim.")
    try:
        jwks = _fetch_jwks(issuer)
    except requests.RequestException as e:
        raise HTTPException(503, f"Could not fetch JWKS: {e}")

    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        raise HTTPException(401, f"Token kid '{kid}' not found in JWKS.")

    return jwt.decode(
        token,
        key,
        algorithms=[alg],
        options={"verify_aud": False},
    )


def verify_token(token: str) -> dict:
    """Decode and verify a Supabase JWT. Returns the full payload."""
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        kid = header.get("kid")

        if alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SUPABASE_JWT_SECRET is not configured on the server.",
                )
            return jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )

        if alg in ("ES256", "RS256"):
            return _verify_asymmetric(token, alg, kid)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported token algorithm: {alg}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
