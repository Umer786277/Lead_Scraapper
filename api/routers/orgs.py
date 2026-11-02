"""Organization, membership, and invitation endpoints."""

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import db
import psycopg
import requests
from api.deps import get_current_user, get_org_id, get_user_id
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

router = APIRouter()


class OrgCreate(BaseModel):
    name: str
    slug: Optional[str] = None


class MemberInvite(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(default="user", pattern="^(admin|user)$")


class MemberRoleUpdate(BaseModel):
    role: str = Field(pattern="^(admin|user)$")


class InvitationAccept(BaseModel):
    token: Optional[str] = Field(default=None, min_length=20, max_length=200)


class InvitationLookup(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class InvitationSignup(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=128)


@router.get("")
def list_orgs(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing sub claim.")
    super_admin = db.is_super_admin(user_id)
    if super_admin:
        return {"items": db.list_all_orgs(), "is_super_admin": True}
    # Ensure a personal org exists so a brand-new user isn't org-less.
    db.ensure_user_org(user_id)
    return {"items": db.list_orgs_for_user(user_id), "is_super_admin": False}


@router.get("/current")
def current_org(org_id: str = Depends(get_org_id)):
    return {"org_id": org_id}


@router.post("")
def create_org(body: OrgCreate, user: dict = Depends(get_current_user)):
    if not db.is_super_admin(user.get("sub")):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a platform super admin can create organizations.",
        )
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Organization name is required.")
    org_id = db.create_org(name, body.slug)
    return {"id": org_id}


@router.get("/directory")
def platform_directory(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    if not db.is_super_admin(user.get("sub")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    return {
        "organizations": db.list_org_directory(),
        "users": db.list_all_users(limit=limit, offset=offset),
        "current_user_id": user["sub"],
    }


@router.get("/deletion-jobs")
def deletion_jobs(user: dict = Depends(get_current_user)):
    if not db.is_super_admin(user.get("sub")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    return {"items": db.list_deletion_jobs()}


def _process_immediately_if_idle(result: str, job_id: str | None) -> str:
    if result != "process_now":
        return result
    from api.deletion_worker import process_deletion_job_now

    job_status = process_deletion_job_now(job_id)
    if job_status is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The deletion job was created but its status could not be retrieved.",
        )
    return "deleted" if job_status == "completed" else job_status


@router.get("/members")
def list_members(
    org_id: str = Depends(get_org_id),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    role = db.get_membership_role(user_id, org_id)
    super_admin = db.is_super_admin(user_id)
    if not role and not super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this organization.")
    if role:
        db.update_membership_email(user_id, org_id, user.get("email"))
    return {
        "items": db.get_org_members(org_id),
        "can_manage": super_admin or role == "admin",
        "can_manage_roles": super_admin or role == "admin",
        "is_super_admin": super_admin,
    }


@router.post("/invitations/accept")
def accept_invitations(
    body: Optional[InvitationAccept] = None,
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    email = user.get("email")
    if not user_id or not email:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "The authenticated account must include a user ID and email.",
        )
    if body and body.token:
        result = db.accept_org_invitation_token(user_id, email, body.token)
        if "error" in result:
            errors = {
                "not_found": (status.HTTP_404_NOT_FOUND, "This invitation is invalid or its organization is no longer active."),
                "not_pending": (status.HTTP_409_CONFLICT, "This invitation has already been accepted or revoked."),
                "expired": (status.HTTP_410_GONE, "This invitation has expired. Ask an organization admin to send a new one."),
                "email_mismatch": (status.HTTP_403_FORBIDDEN, "Sign in or create an account using the email address this invitation was sent to."),
            }
            code, detail = errors[result["error"]]
            raise HTTPException(code, detail)
        return {"ok": True, "items": result["items"]}

    accepted = db.accept_org_invitations(user_id, email)
    if not accepted:
        if db.has_pending_org_invitation_for_email(email):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "A pending invitation exists for this email, but it does not match the authenticated account. Sign out and open the invitation link using the invited email address.",
            )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No pending organization invitation was found for the authenticated email address.",
        )
    return {"ok": True, "items": accepted}


@router.post("/invitations/lookup")
def lookup_invitation(body: InvitationLookup):
    invitation = db.lookup_org_invitation(body.token)
    if not invitation:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This invitation is invalid, expired, or has been revoked.",
        )
    return invitation


@router.post("/invitations/signup", status_code=status.HTTP_201_CREATED)
def signup_invitation_account(body: InvitationSignup):
    invitation = db.lookup_org_invitation(body.token)
    if not invitation:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This invitation is invalid, expired, or has been revoked.",
        )
    existing_user = db.find_auth_user_by_email(invitation["email"])
    if existing_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account already exists for this email. Sign in to accept the invitation.",
        )

    supabase_url = (
        os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or ""
    ).rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not service_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Invitation signup requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on the backend.",
        )

    try:
        response = requests.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
            json={
                "email": invitation["email"],
                "password": body.password,
                "email_confirm": True,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Supabase could not be reached to create the invited account.",
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Supabase rejected the backend credentials. Set SUPABASE_SERVICE_ROLE_KEY to the service_role key for the same project as SUPABASE_URL.",
        )
    if response.status_code in (409, 422):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account already exists for this email. Sign in to accept the invitation.",
        )
    if not response.ok:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Supabase could not create the invited account (HTTP {response.status_code}).",
        )
    try:
        created_user = response.json()
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Supabase returned an invalid account-creation response.",
        ) from exc
    if str(created_user.get("email", "")).lower() != invitation["email"].lower():
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Supabase returned an account that did not match the invited email.",
        )
    return {"ok": True, "email": invitation["email"]}


@router.post("/members/invite", status_code=status.HTTP_201_CREATED)
def invite_member(
    body: MemberInvite,
    org_id: str = Depends(get_org_id),
    user_id: str = Depends(get_user_id),
):
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address.")
    role = db.get_membership_role(user_id, org_id)
    super_admin = db.is_super_admin(user_id)
    if role != "admin" and not super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization admin access is required.")
    if db.membership_email_exists(org_id, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already a member.")
    if db.pending_invitation_exists(org_id, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An invitation is already pending for this email.")

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    from_addr = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    org_accounts = db.list_email_accounts(org_id=org_id)
    has_active_account = any(account.get("status") == "active" for account in org_accounts)
    if not (smtp_host and from_addr) and not has_active_account:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Organization invitations require an active sender account or SMTP_HOST and SMTP_FROM (or SMTP_USER).",
        )
    app_url = (
        os.getenv("APP_BASE_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or os.getenv("SUPABASE_INVITE_REDIRECT_URL", "")
    ).strip()
    parsed_app_url = urlsplit(app_url)
    if (
        parsed_app_url.scheme not in ("http", "https")
        or not parsed_app_url.hostname
        or parsed_app_url.username
        or parsed_app_url.password
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Set APP_BASE_URL to the deployed frontend origin so invitation links point to the correct app.",
        )

    token = secrets.token_urlsafe(32)
    invitation_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    try:
        db.create_org_invitation(
            invitation_id,
            org_id,
            email,
            body.role,
            user_id,
            db.hash_invitation_token(token),
            expires_at,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An invitation is already pending for this email.",
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The invitation could not be recorded. Please try again.",
        ) from exc

    app_origin = f"{parsed_app_url.scheme}://{parsed_app_url.netloc}"
    invite_url = f"{app_origin}/login?{urlencode({'invite': token})}"
    try:
        db.enqueue_outbound(
            org_id=org_id,
            source="organization_invitation",
            to_email=email,
            subject="You're invited to join an organization on Missa",
            body=(
                f"You have been invited to join an organization on Missa with the {body.role} role.\n\n"
                f"Accept the invitation and create or sign in to your account here:\n{invite_url}\n\n"
                "This link expires in 7 days and can only be used by the invited email address."
            ),
            idempotency_key=f"organization-invitation:{invitation_id}",
            note=f"organization invitation ({body.role})",
        )
    except psycopg.Error as exc:
        try:
            db.discard_org_invitation(invitation_id)
        except psycopg.Error as cleanup_error:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "The invitation email failed and the pending invitation could not be revoked. Revoke it from the organization members page.",
            ) from cleanup_error
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The invitation was recorded but its email could not be queued. Please try again.",
        ) from exc
    return {
        "ok": True,
        "email": email,
        "user_id": None,
        "role": body.role,
        "invitation_required": True,
        "email_queued": True,
    }

@router.delete("/invitations/{member_id}")
def revoke_invitation(
    member_id: UUID,
    org_id: str = Depends(get_org_id),
    user_id: str = Depends(get_user_id),
):
    result = db.revoke_org_invitation(user_id, org_id, member_id)
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization admin access is required.")
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pending organization invitation not found.")
    return {"ok": True}


@router.patch("/members/{member_id}")
def update_member_role(
    member_id: UUID,
    body: MemberRoleUpdate,
    org_id: str = Depends(get_org_id),
    user_id: str = Depends(get_user_id),
):
    result = db.update_org_member_role(user_id, org_id, member_id, body.role)
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization admin access is required.")
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization member not found.")
    if result == "last_admin":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the organization's only admin. Promote another member to Admin before changing this member's role.",
        )
    return {"ok": True}


@router.delete("/members/{member_id}")
def remove_member(
    member_id: UUID,
    org_id: str = Depends(get_org_id),
    user_id: str = Depends(get_user_id),
):
    result = db.remove_org_member(user_id, org_id, member_id)
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot remove this organization member.")
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization member not found.")
    if result == "last_admin":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the organization's only admin. Promote another member to Admin before removing this member.",
        )
    return {"ok": True}


@router.delete("/users/{target_user_id}")
def delete_user(
    target_user_id: UUID,
    user: dict = Depends(get_current_user),
):
    actor_id = user.get("sub")
    if not actor_id or not db.is_super_admin(actor_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    result, job_id = db.enqueue_user_deletion(target_user_id, actor_id)
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    if result == "self_delete":
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account.")
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User account not found.")
    if result == "last_super_admin":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The last platform super admin cannot be deleted.",
        )
    if result == "last_org_admin":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This user is the only admin of an active organization. Assign another admin before deleting the account.",
        )
    result = _process_immediately_if_idle(result, job_id)
    return {"ok": True, "job_id": job_id, "status": result}


@router.delete("/{org_id}")
def delete_organization(
    org_id: UUID,
    user: dict = Depends(get_current_user),
):
    if not db.is_super_admin(user.get("sub")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    result, job_id = db.enqueue_organization_deletion(org_id, user.get("sub"))
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin access is required.")
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
    if result == "protected_default":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The configured default organization cannot be deleted because it is the system fallback.",
        )
    if result == "last_active_org":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The last active organization cannot be deleted.",
        )
    result = _process_immediately_if_idle(result, job_id)
    return {"ok": True, "job_id": job_id, "status": result}
