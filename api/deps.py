"""
FastAPI dependency functions — injected into route handlers via Depends().
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
