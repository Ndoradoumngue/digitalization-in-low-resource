"""Plan de classement helpers that need no database (GED Phase 3)."""

from classification_router import build_tree, compute_paths


def test_paths_follow_parents():
    paths, cut = compute_paths([("a", None), ("b", "a"), ("c", "b"), ("d", None)])
    assert cut == []
    assert paths == {"a": "/a/", "b": "/a/b/", "c": "/a/b/c/", "d": "/d/"}


def test_missing_parent_makes_a_root():
    paths, cut = compute_paths([("b", "gone"), ("c", "b")])
    assert cut == [] and paths == {"b": "/b/", "c": "/b/c/"}


def test_cycle_is_cut_at_its_smallest_id_whatever_the_input_order():
    rows = [("x", "y"), ("y", "x"), ("z", "x"), ("r", None)]
    for ordering in (rows, list(reversed(rows))):
        paths, cut = compute_paths(ordering)
        assert cut == ["x"]
        assert paths == {"x": "/x/", "y": "/x/y/", "z": "/x/z/", "r": "/r/"}


def test_self_parent_is_a_cycle():
    paths, cut = compute_paths([("a", "a")])
    assert cut == ["a"] and paths == {"a": "/a/"}


def test_build_tree_nests_and_keeps_orphans_visible():
    rows = [
        {"id": "a", "parent_id": None},
        {"id": "b", "parent_id": "a"},
        {"id": "c", "parent_id": "not-synced-yet"},
    ]
    tree = build_tree(rows)
    assert [n["id"] for n in tree] == ["a", "c"]
    assert [n["id"] for n in tree[0]["children"]] == ["b"]
