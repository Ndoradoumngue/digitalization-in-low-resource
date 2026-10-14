"""fr/en completeness (GED Phase 9): what the backend can say or record has
a French and an English text, here and in the frontend catalogues."""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import i18n
import notifications

API = Path(__file__).resolve().parent.parent
LOCALES = API.parent / "frontend" / "src" / "i18n" / "locales"


def _sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in [*API.glob("*.py"), *(API / "sync").glob("*.py")]
    )


def _frontend(lang: str) -> dict:
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def test_every_backend_message_has_both_languages():
    for key, table in i18n.MESSAGES.items():
        assert set(table) == {"en", "fr"}, key
        en = set(re.findall(r"\{(\w+)\}", table["en"]))
        assert en == set(re.findall(r"\{(\w+)\}", table["fr"])), key


def test_every_static_t_key_exists():
    used = set(re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']""", _sources()))
    assert used - set(i18n.MESSAGES) == set()


def test_frontend_catalogues_have_the_same_keys():
    assert set(_flat(_frontend("en"))) == set(_flat(_frontend("fr")))


def test_every_audit_action_has_a_label():
    actions = set(re.findall(r'\baction\s*=\s*"([a-z_]+)"', _sources()))
    actions -= {"store_true", "store_false"}  # argparse
    for lang in ("en", "fr"):
        assert actions - set(_frontend(lang)["audit"]["actions"]) == set(), lang


def test_every_notification_kind_and_sync_alert_has_a_text():
    alerts = set(
        re.findall(r'"kind":\s*"([a-z_]+)"', (API / "sync" / "admin.py").read_text())
    )
    for lang in ("en", "fr"):
        texts = _frontend(lang)["notifications"]
        plain = {re.sub(r"_(one|other)$", "", k) for k in texts["text"]}
        assert set(notifications.KINDS) - set(texts["kinds"]) == set(), lang
        assert set(notifications.KINDS) - plain == set(), lang
        assert alerts - set(_frontend(lang)["sync"]["alerts"]) == set(), lang
    for kind in notifications.KINDS:
        assert f"email.subject.{kind}" in i18n.MESSAGES, kind
        assert f"email.body.{kind}" in i18n.MESSAGES, kind


def test_dates_are_day_first_in_french():
    ts = datetime(2026, 10, 4, 13, 5, tzinfo=timezone.utc)
    assert i18n.format_date(ts, "fr") == "04/10/2026"
    assert i18n.format_date(date(2026, 10, 4), "en") == "2026-10-04"
    assert i18n.format_date("2026-10-04", "fr") == "04/10/2026"
    assert i18n.format_date(None, "fr") == "-"
    assert i18n.format_datetime(ts, "fr") == "04/10/2026 13:05 UTC"
    assert i18n.format_datetime(ts.isoformat(), "en") == "2026-10-04 13:05 UTC"
