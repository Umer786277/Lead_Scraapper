"""Durable background processing for organization and Auth-user deletion jobs."""

import logging
import os
import threading
from uuid import uuid4

import db
import requests

log = logging.getLogger(__name__)


class PermanentDeletionError(Exception):
    """A deletion cannot succeed without an administrator changing state."""


class RetryableDeletionError(Exception):
    """A safe-to-display deletion failure that should be retried."""


def _delete_user(job):
    with db.global_user_deletion(
        job["target_id"], job["requested_by"], allow_missing_auth=True
    ) as deletion:
        reason = deletion["reason"]
        if reason == "forbidden":
            raise PermanentDeletionError("The requesting account no longer has Super Admin access.")
        if reason == "last_super_admin":
            raise PermanentDeletionError("The last platform Super Admin cannot be deleted.")
        if reason == "last_org_admin":
            raise PermanentDeletionError(
                "The user is the only admin of an active organization. Assign another admin first."
            )

        if deletion["auth_user_exists"]:
            supabase_url = (
                os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or ""
            ).rstrip("/")
            service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            if not supabase_url or not service_key:
                raise PermanentDeletionError(
                    "User deletion requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the backend environment."
                )

            try:
                response = requests.delete(
                    f"{supabase_url}/auth/v1/admin/users/{job['target_id']}",
                    headers={
                        "apikey": service_key,
                        "Authorization": f"Bearer {service_key}",
                    },
                    timeout=15,
                )
            except requests.RequestException as exc:
                raise RetryableDeletionError(
                    "Supabase Auth could not be reached; the job will retry."
                ) from exc

            if response.status_code == 401:
                raise PermanentDeletionError(
                    "Supabase Auth rejected the backend service credentials (HTTP 401). "
                    "Verify SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
                )
            if response.status_code == 403:
                raise PermanentDeletionError(
                    "Supabase Auth denied user deletion (HTTP 403). Verify the backend service-role credentials."
                )
            if response.status_code != 404 and not response.ok:
                message = (
                    f"Supabase Auth returned HTTP {response.status_code}; the job will retry."
                    if response.status_code >= 500 or response.status_code in (408, 425, 429)
                    else f"Supabase Auth rejected the deletion request (HTTP {response.status_code})."
                )
                if response.status_code >= 500 or response.status_code in (408, 425, 429):
                    raise RetryableDeletionError(message)
                raise PermanentDeletionError(message)


def _process_job(job, worker_id):
    job_id = job["id"]
    attempts = job["attempts"]
    stop_heartbeat = threading.Event()

    def heartbeat():
        while not stop_heartbeat.wait(60):
            try:
                if not db.renew_deletion_job(job_id, worker_id):
                    log.error("Deletion job %s lost its worker lease", job_id)
                    return
            except Exception:
                log.exception("Could not renew deletion job lease %s", job_id)

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"deletion-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        if job["kind"] == "organization":
            result = db.delete_organization(job["target_id"])
            if result == "protected_default":
                raise PermanentDeletionError("The configured default organization cannot be deleted.")
            if result == "last_active_org":
                raise PermanentDeletionError("The last active organization cannot be deleted.")
        elif job["kind"] == "user":
            _delete_user(job)
        else:
            raise PermanentDeletionError("Unsupported deletion job type.")

        if not db.complete_deletion_job(job_id, worker_id):
            log.error("Deletion job %s finished but its lease was no longer owned", job_id)
    except PermanentDeletionError as exc:
        log.warning("Deletion job %s cannot proceed: %s", job_id, exc)
        if not db.fail_deletion_job(job_id, worker_id, str(exc), 5):
            log.error("Could not mark deletion job %s failed because its lease was lost", job_id)
    except RetryableDeletionError as exc:
        log.warning("Deletion job %s failed on attempt %s: %s", job_id, attempts, exc)
        if not db.fail_deletion_job(job_id, worker_id, str(exc), attempts):
            log.error("Could not record failure for deletion job %s because its lease was lost", job_id)
    except Exception:
        log.exception("Deletion job %s failed on attempt %s", job_id, attempts)
        if not db.fail_deletion_job(
            job_id,
            worker_id,
            "Deletion failed due to a backend error; it will retry automatically.",
            attempts,
        ):
            log.error("Could not record failure for deletion job %s because its lease was lost", job_id)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)


def process_deletion_jobs(batch_size=3):
    """Claim and process a bounded batch; PostgreSQL claims coordinate replicas."""
    for _ in range(batch_size):
        worker_id = str(uuid4())
        job = db.claim_deletion_job(worker_id)
        if not job:
            return
        _process_job(job, worker_id)


def process_deletion_job_now(job_id):
    """Process a newly queued job inline when the durable queue was idle."""
    worker_id = str(uuid4())
    job = db.claim_deletion_job(worker_id, job_id=job_id)
    if job:
        _process_job(job, worker_id)
    return db.get_deletion_job_status(job_id)
