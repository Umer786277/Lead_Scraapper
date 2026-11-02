"""
FastAPI dependency functions — injected into route handlers via Depends().
"""

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db
from api.auth import verify_token

# auto_error=False so OPTIONS preflight (no Authorization header) gets a 200
# from CORS middleware instead of a 400/403 from this dependency.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Verify the Bearer token and return the decoded JWT payload."""
    if request.method == "OPTIONS":
        return {}
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials)


def get_user_id(user: dict = Depends(get_current_user)) -> str:
    """Extract the Supabase user UUID from the JWT sub claim."""
    uid = user.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim.",
        )
    return uid


def get_org_id(
    request: Request,
    user: dict = Depends(get_current_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> str:
    """Resolve the active organization for this request.

    Uses the `X-Org-Id` header when present (and validates membership),
    otherwise falls back to the user's first org, creating a personal org
    on first use.
    """
    if request.method == "OPTIONS":
        return ""
    uid = user.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim.",
        )
    requested = (x_org_id or "").strip()
    if requested:
        org = db.org_for_user(uid, requested)
        if not org and db.is_super_admin(uid) and db.organization_exists(requested):
            return requested
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization.",
            )
        return org
    return db.ensure_user_org(uid, user.get("email"))


def require_org_admin(
    org_id: str = Depends(get_org_id),
    user: dict = Depends(get_current_user),
) -> str:
    """Require organization-admin membership or platform-wide super-admin."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim.",
        )
    if db.is_super_admin(user_id):
        return org_id
    if db.get_membership_role(user_id, org_id) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin access is required.",
        )
    return org_id
