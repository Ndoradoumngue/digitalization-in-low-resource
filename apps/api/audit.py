"""
Audit logging helper.

Every write action in the system calls log_action() to record who did what
and when.  The helper opens its own transaction so audit failures never
propagate to the caller — a logging hiccup must not reject a document review.

Actions logged:
  user_login         user_logout
  document_ingested  document_approved  document_rejected  document_flagged
  document_linked    document_link_removed
  document_access_granted  document_access_revoked
  series_created     series_assigned
  integrity_check_failed
  document_fields_edited
  archive_exported
  schema_created     schema_altered
"""

import json
import sys
import uuid as _uuid
from typing import Any, Optional

from fastapi import Request


# ── IP extraction ─────────────────────────────────────────────────────────────

def client_ip(request: Request) -> Optional[str]:
    """Return the real client IP from X-Forwarded-For.

    nginx appends the true client IP as the *last* entry in X-Forwarded-For,
    so we read from the right.  This prevents a client from spoofing their IP
    by sending a fabricated X-Forwarded-For header.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else None


# ── Core helper ───────────────────────────────────────────────────────────────

async def log_action(
    *,
    action:      str,
    user_id:     Optional[str] = None,
    user_email:  Optional[str] = None,
    table_name:  Optional[str] = None,
    document_id: Optional[str] = None,
    details:     Optional[dict[str, Any]] = None,
    ip_address:  Optional[str] = None,
) -> None:
    """
    Insert one audit log entry in its own transaction.

    Never raises — a logging failure is printed to stderr and swallowed so
    that the user-facing action that triggered it is never affected.
    """
    # Lazy import avoids the circular dependency:
    # ingest_router → audit → ingest_router
    from ingest_router import _engine  # noqa: PLC0415
    from sqlalchemy import text        # noqa: PLC0415

    def _to_uuid(s: Optional[str]):
        if s is None:
            return None
        try:
            return _uuid.UUID(s)
        except (ValueError, AttributeError):
            return None

    try:
        async with _engine().begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO sdai_audit_log
                        (user_id, user_email, action, table_name,
                         document_id, details, ip_address)
                    VALUES
                        (:uid, :email, :action, :tname,
                         :did, CAST(:details AS jsonb), :ip)
                """),
                {
                    "uid":     _to_uuid(user_id),
                    "email":   user_email,
                    "action":  action,
                    "tname":   table_name,
                    "did":     _to_uuid(document_id),
                    "details": json.dumps(details) if details is not None else None,
                    "ip":      ip_address,
                },
            )
    except Exception as exc:
        print(f"[audit] WARNING: could not log '{action}': {exc}", file=sys.stderr)
