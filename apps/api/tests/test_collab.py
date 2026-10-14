"""Collaboration helpers (GED Phase 7) without a database: e-mail content,
backoff, notification links, lock SQL, signature file types."""

from datetime import timedelta

import pytest


def test_render_email_has_only_a_link():
    import notifications

    subject, body = notifications.render_email(
        "mention", "fr", "/documents/t_default_x/abc"
    )
    assert subject.startswith("[SDAI] ") and "mentionné" in subject
    assert "/documents/t_default_x/abc" in body and body.startswith("Bonjour,")
    subject_en, body_en = notifications.render_email("signature_requested", "en", "/x")
    assert "signature" in subject_en.lower() and body_en.startswith("Hello,")


def test_every_kind_has_email_texts_in_both_languages():
    import i18n
    import notifications

    for kind in notifications.KINDS:
        for key in (f"email.subject.{kind}", f"email.body.{kind}"):
            assert set(i18n.MESSAGES[key]) == {"en", "fr"}, key


def test_retry_delay_backs_off_and_caps():
    import notifications

    assert notifications.retry_delay(1) == timedelta(minutes=1)
    assert notifications.retry_delay(4) == timedelta(minutes=8)
    assert notifications.retry_delay(30) == timedelta(hours=6)


def test_link_for():
    import notifications

    assert notifications.link_for("sync_down", {}, None, None) == "/ops/sync"
    assert notifications.link_for("review_pending", {}, None, None) == "/review"
    assert notifications.link_for("mention", {}, "t_a", "d1") == "/documents/t_a/d1"
    assert (
        notifications.link_for("destruction_proposed", {}, None, None)
        == "/lifecycle/destructions"
    )


def test_notify_rejects_unknown_kind():
    import asyncio

    import notifications

    with pytest.raises(ValueError):
        asyncio.run(notifications.notify(None, "t", ["u"], "nope", {}))


def test_lock_sql_only_takes_validated_literals():
    import locks

    uid = "00000000-0000-0000-0000-0000000000aa"
    sql = locks.unlocked_sql("t_default_invoice", uid)
    assert sql.startswith("NOT (") and f"CAST('{uid}' AS uuid)" in sql
    with pytest.raises(ValueError):
        locks.unlocked_sql("t_x", "1' OR '1'='1")
    with pytest.raises(ValueError):
        locks.unlocked_sql('t_x"; DROP TABLE x; --', uid)


def test_lock_is_active():
    from datetime import datetime, timezone

    import locks

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    assert not locks.is_active(None, None, now)
    assert locks.is_active("u", None, now)
    assert locks.is_active("u", now + timedelta(minutes=1), now)
    assert not locks.is_active("u", now - timedelta(minutes=1), now)


def test_edit_clause_includes_the_lock_for_everyone(admin_client):
    from auth import CurrentUser
    from documents_router import _access_where_clause

    def user(role):
        return CurrentUser(
            id="00000000-0000-0000-0000-0000000000bb",
            email="x",
            full_name=None,
            role=role,
            tenant_id="00000000-0000-0000-0000-000000000099",
            tenant_slug="default",
            tenant_name="D",
            can_manage_access=False,
            can_edit_extraction=True,
            group_ids=[],
            locale="en",
        )

    for role in ("admin", "reviewer"):
        assert "locked_by" in _access_where_clause(
            "t_default_invoice", user(role), "edit"
        )
        assert "locked_by" not in _access_where_clause(
            "t_default_invoice", user(role), "read"
        )


def test_signature_file_types():
    import signatures

    assert signatures.is_pdf("/a/b.PDF", None) and signatures.is_pdf(
        "/a/b", "application/pdf"
    )
    assert signatures.is_image("/a/b.jpg", None) and signatures.is_image(
        "/a/b", "image/tiff"
    )
    assert not signatures.is_image("/a/b.docx", None)


def test_signing_unavailable_without_certificate(monkeypatch):
    import signatures

    monkeypatch.setattr(signatures, "SIGNING_P12_PATH", "")
    assert signatures.status() == {"configured": False, "reason": None}
    with pytest.raises(signatures.SigningUnavailable):
        signatures.load_signer()
    monkeypatch.setattr(signatures, "SIGNING_P12_PATH", "/nonexistent/org.p12")
    st = signatures.status()
    assert st["configured"] is False and "not found" in st["reason"]
