"""Prompt routing (page_prompts / classify_prompt + page_types directives)
and the automatic post-extraction checks (_quality_flags) - all configured
from prompt files, nothing corpus-specific in code."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import ingest_router
from ingest_router import QualityChecks, TenantConfig, _load_vlm_prompt, _page_prompt, _quality_flags

LIST_FIELDS = frozenset({"entries"})


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _cfg(loaded):
    return TenantConfig(loaded.text, LIST_FIELDS, 60.0, False, None, loaded)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fake_source_page(monkeypatch, source_page_number):
    """Stand in for the DB lookup of a page's source page number."""

    class _Result:
        def scalar_one_or_none(self):
            return source_page_number

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *args, **kwargs):
            return _Result()

    class _Engine:
        def connect(self):
            return _Conn()

    monkeypatch.setattr(ingest_router, "_engine", lambda: _Engine())


# ── page_prompts ─────────────────────────────────────────────────────────────


def test_page_prompts_route_source_pages(tmp_path):
    _write(tmp_path / "front.txt", "Front prompt")
    _write(tmp_path / "annex.txt", "# min_items: entries=8\n\nAnnex prompt")
    main = _write(
        tmp_path / "main.txt",
        "# page_prompts: 1-11=front.txt; 93-105=annex.txt\n# min_items: entries=6\n\nMain prompt",
    )
    cfg = _cfg(_load_vlm_prompt(str(main)))
    assert cfg.main.text == "Main prompt"
    assert cfg.main.checks.min_items == {"entries": 6}

    assert cfg.prompt_for_range(1).text == "Front prompt"
    assert cfg.prompt_for_range(11).text == "Front prompt"
    assert cfg.prompt_for_range(12) is None
    annex = cfg.prompt_for_range(100)
    assert annex.text == "Annex prompt" and annex.checks.min_items == {"entries": 8}
    assert cfg.prompt_for_range(None) is None


def test_page_prompts_single_page_and_absolute_path(tmp_path):
    (tmp_path / "sub").mkdir()
    other = _write(tmp_path / "sub" / "cover.txt", "Cover")
    main = _write(tmp_path / "main.txt", f"# page_prompts: 1={other}\n\nMain")
    loaded = _load_vlm_prompt(str(main))
    assert [(lo, hi, p.text) for lo, hi, p in loaded.page_prompts] == [(1, 1, "Cover")]


# ── classify_prompt + page_types ─────────────────────────────────────────────


def _classified(tmp_path, extra=""):
    _write(tmp_path / "classify.txt", 'Return {"page_type": "cover" or "register"}')
    _write(tmp_path / "cover.txt", "# quality_checks: off\n\nCover prompt")
    _write(tmp_path / "register.txt", "# min_items: parcels=3\n\nRegister prompt")
    return _write(
        tmp_path / "main.txt",
        "# classify_prompt: classify.txt\n# page_types: cover=cover.txt; Register=register.txt\n"
        + extra
        + "\nMain prompt",
    )


def test_page_types_load(tmp_path):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path))))
    assert cfg.main.classify_prompt.startswith("Return")
    assert cfg.prompt_for_type("cover").text == "Cover prompt"
    assert cfg.prompt_for_type(" REGISTER ").text == "Register prompt"  # case-insensitive
    assert cfg.prompt_for_type("invoice") is None
    assert cfg.prompt_for_type(None) is None
    assert cfg.prompt_for_type("cover").checks.enabled is False


@pytest.mark.parametrize(
    "vlm_result, expected",
    [
        ({"page_type": "register"}, "Register prompt"),
        ({"page_type": "invoice"}, "Main prompt"),  # unknown type -> main
        ({"_parse_error": True, "_raw": "not json"}, "Main prompt"),  # bad output -> main
    ],
)
def test_classifier_picks_prompt(tmp_path, monkeypatch, vlm_result, expected):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path))))
    vlm = AsyncMock(return_value=vlm_result)
    monkeypatch.setattr(ingest_router, "_run_vlm_tracked", vlm)
    chosen = _run(_page_prompt(cfg, "page-1", Path("/img.png")))
    assert chosen.text == expected
    assert vlm.await_args.args[2] == cfg.main.classify_prompt


def test_classifier_failure_falls_back_to_main(tmp_path, monkeypatch):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path))))
    monkeypatch.setattr(ingest_router, "_run_vlm_tracked", AsyncMock(side_effect=TimeoutError()))
    assert _run(_page_prompt(cfg, "page-1", Path("/img.png"))).text == "Main prompt"


def test_classifier_user_cancel_propagates(tmp_path, monkeypatch):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path))))
    monkeypatch.setattr(
        ingest_router, "_run_vlm_tracked", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        _run(_page_prompt(cfg, "page-1", Path("/img.png")))


def test_page_range_wins_over_classifier(tmp_path, monkeypatch):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path, extra="# page_prompts: 1-2=cover.txt\n"))))
    _fake_source_page(monkeypatch, 1)
    vlm = AsyncMock(return_value={"page_type": "register"})
    monkeypatch.setattr(ingest_router, "_run_vlm_tracked", vlm)
    assert _run(_page_prompt(cfg, "page-1", Path("/img.png"))).text == "Cover prompt"
    vlm.assert_not_awaited()  # the range decided - no classification call


def test_outside_ranges_falls_through_to_classifier(tmp_path, monkeypatch):
    cfg = _cfg(_load_vlm_prompt(str(_classified(tmp_path, extra="# page_prompts: 1-2=cover.txt\n"))))
    _fake_source_page(monkeypatch, 50)
    monkeypatch.setattr(ingest_router, "_run_vlm_tracked", AsyncMock(return_value={"page_type": "register"}))
    assert _run(_page_prompt(cfg, "page-1", Path("/img.png"))).text == "Register prompt"


def test_plain_prompt_never_classifies(tmp_path, monkeypatch):
    cfg = _cfg(_load_vlm_prompt(str(_write(tmp_path / "main.txt", "Main"))))
    vlm = AsyncMock()
    monkeypatch.setattr(ingest_router, "_run_vlm_tracked", vlm)
    assert _run(_page_prompt(cfg, "page-1", Path("/img.png"))).text == "Main"
    vlm.assert_not_awaited()


# ── directive validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "directive",
    [
        "# page_prompts: 1-11\n",  # no target file
        "# page_prompts: a-b=x.txt\n",  # not numbers
        "# page_prompts: 10-5=x.txt\n",  # backwards
        "# page_prompts: 1-10=x.txt; 5-20=x.txt\n",  # overlap
        "# min_items: entries\n",  # no count
        "# max_value_length: zero\n",
        "# max_repeats: 0\n",
        "# classify_prompt: x.txt\n",  # without page_types
        "# page_types: a=x.txt\n",  # without classify_prompt
        "# classify_prompt: x.txt\n# page_types: a=x.txt; A=x.txt\n",  # duplicate type
        "# classify_prompt: x.txt\n# page_types: bad name=x.txt\n",
    ],
)
def test_bad_directives_fail_fast(tmp_path, directive):
    _write(tmp_path / "x.txt", "X")
    main = _write(tmp_path / "main.txt", directive + "\nMain")
    with pytest.raises(RuntimeError):
        _load_vlm_prompt(str(main))


def test_missing_routed_prompt_file_fails_fast(tmp_path):
    main = _write(tmp_path / "main.txt", "# page_prompts: 1-2=missing.txt\n\nMain")
    with pytest.raises(RuntimeError, match="does not exist"):
        _load_vlm_prompt(str(main))


@pytest.mark.parametrize(
    "nested",
    ["# page_prompts: 1-2=leaf.txt", "# classify_prompt: leaf.txt\n# page_types: a=leaf.txt"],
)
def test_routed_prompts_cannot_route_further(tmp_path, nested):
    _write(tmp_path / "leaf.txt", "Leaf")
    _write(tmp_path / "inner.txt", nested + "\n\nInner")
    main = _write(tmp_path / "main.txt", "# page_prompts: 1-5=inner.txt\n\nMain")
    with pytest.raises(RuntimeError, match="top-level"):
        _load_vlm_prompt(str(main))


def test_prompt_without_new_directives_is_unchanged(tmp_path):
    loaded = _load_vlm_prompt(str(_write(tmp_path / "main.txt", "# split_from_page: 12\n\nMain")))
    assert loaded.page_prompts == () and loaded.page_types == () and loaded.classify_prompt is None
    assert loaded.checks == QualityChecks()
    # A config built without a loaded prompt behaves as a single plain prompt.
    assert TenantConfig("Main", LIST_FIELDS, 60.0, True, 12).main.text == "Main"


def test_tracked_routing_example_loads():
    path = Path(ingest_router.__file__).parent / "prompts" / "examples" / "routing" / "main.txt"
    cfg = _cfg(_load_vlm_prompt(str(path)))
    assert [(lo, hi) for lo, hi, _ in cfg.main.page_prompts] == [(1, 1)]
    assert cfg.main.classify_prompt is not None
    assert cfg.prompt_for_type("register").checks.min_items == {"parcels": 3}
    assert cfg.prompt_for_type("cover").checks.enabled is False


# ── _quality_flags ───────────────────────────────────────────────────────────


def _codes(flags):
    return {(f["code"], f["count"]) for f in flags}


def test_clean_page_has_no_flags():
    fields = {"entries": [{"kabalay": "bàdú", "french": "N. o.a. chat."}]}
    assert _quality_flags(fields, LIST_FIELDS, QualityChecks()) == []


def test_flags_empty_identical_overlong_and_repeated():
    entries = [
        {"kabalay": "'bèy bàb", "french": ""},
        {"kabalay": "Ami", "french": "ami"},
        {"kabalay": "pédi", "french": "x" * 500},
    ] + [{"kabalay": "gugu", "french": "pigeon"}] * 3
    flags = _quality_flags({"entries": entries}, LIST_FIELDS, QualityChecks())
    assert _codes(flags) == {
        ("empty_value", 1),
        ("identical_values", 1),
        ("overlong_value", 1),
        ("repeated_items", 1),
    }
    assert all(f["field"] == "entries" for f in flags)


def test_thresholds_come_from_the_prompt(tmp_path):
    loaded = _load_vlm_prompt(str(_write(tmp_path / "p.txt", "# max_value_length: 1000\n# max_repeats: 5\n\nP")))
    entries = [{"a": "x" * 500, "b": "y"}] + [{"a": "1", "b": "2"}] * 4
    assert _quality_flags({"entries": entries}, LIST_FIELDS, loaded.checks) == []
    assert _codes(_quality_flags({"entries": entries}, LIST_FIELDS, QualityChecks())) == {
        ("overlong_value", 1),
        ("repeated_items", 1),
    }


def test_checks_can_be_turned_off(tmp_path):
    loaded = _load_vlm_prompt(str(_write(tmp_path / "p.txt", "# quality_checks: off\n# min_items: entries=5\n\nP")))
    assert _quality_flags({"entries": [{"a": "", "b": ""}]}, LIST_FIELDS, loaded.checks) == []


def test_flags_too_few_items_including_missing_field():
    checks = QualityChecks(min_items={"entries": 6})
    assert _codes(_quality_flags({"entries": [{"a": "x", "b": "y"}]}, LIST_FIELDS, checks)) == {
        ("too_few_items", 1)
    }
    assert _codes(_quality_flags({}, LIST_FIELDS, checks)) == {("too_few_items", 0)}
    assert _quality_flags({}, LIST_FIELDS, QualityChecks()) == []
