"""GED Phase 9 security review: guards that need no database."""

from pathlib import Path

import api_server
import ingest_router


def test_drive_destination_keeps_a_safe_basename(tmp_path):
    root = tmp_path.resolve()
    dest = ingest_router._drive_destination(root, "../../etc/cron.d/x.pdf", "abc-123")
    assert dest == root / "abc-123_x.pdf"
    assert (
        ingest_router._drive_destination(root, "..\\..\\evil.png", "id1")
        == root / "id1_evil.png"
    )
    assert (
        ingest_router._drive_destination(root, "/abs/scan.jpeg", "i/d")
        == root / "id_scan.jpeg"
    )
    # not an allowed type, or nothing left of the name
    assert ingest_router._drive_destination(root, "run.sh", "id") is None
    assert ingest_router._drive_destination(root, "..", "id") is None
    # never overwrites
    (root / "id_a.pdf").write_bytes(b"%PDF-")
    assert ingest_router._drive_destination(root, "a.pdf", "id") is None


def test_drive_folder_id_is_validated(admin_client, monkeypatch):
    calls = []

    async def _create_batch(*args, **kwargs):
        calls.append(args)
        return "b"

    monkeypatch.setattr(ingest_router, "_create_batch", _create_batch)
    resp = admin_client.post(
        "/api/ingest/path", json={"google_drive_folder_id": "x' or name contains '"}
    )
    assert resp.status_code == 422
    assert calls == []


def test_inside_refuses_traversal(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    assert api_server._inside(tmp_path, "a.png") == (tmp_path / "a.png").resolve()
    assert api_server._inside(tmp_path, "../secret.png") is None
    assert api_server._inside(tmp_path, "..") is None
    assert api_server._inside(tmp_path, "/etc/passwd") is None


def test_legacy_images_are_private_and_confined(auth_client, monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(api_server, "DOCS_DIR", Path(docs))
    ok = auth_client.get("/images/raw/scan.png")
    assert ok.status_code == 200
    assert ok.headers["cache-control"].startswith("private")
    assert auth_client.get("/images/raw/..%2Foutside.png").status_code == 404
    assert auth_client.get("/images/raw/..").status_code == 404


def test_dockerfile_copies_every_imported_module():
    """A module the app imports but the image lacks only fails in Docker
    (GED Phase 9) - every top-level module loaded by the API and the sync
    worker (in a fresh interpreter) is in the Dockerfile's COPY list."""
    import os
    import re
    import subprocess
    import sys

    api_dir = Path(api_server.__file__).resolve().parent
    probe = (
        "import sys, pathlib, api_server, sync.worker, sync.join, sync.check\n"
        f"d = pathlib.Path({str(api_dir)!r})\n"
        "print('\\n'.join(sorted({pathlib.Path(m.__file__).name for m in list(sys.modules.values())"
        " if getattr(m, '__file__', None) and pathlib.Path(m.__file__).resolve().parent == d})))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=api_dir,
        env={**os.environ, "PYTHONPATH": str(api_dir)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    dockerfile = (api_dir / "Dockerfile").read_text()
    copied = set()
    for line in re.findall(r"^COPY (.+) \./$", dockerfile, re.M):
        copied.update(line.split())
    assert set(out.split()) - copied == set()
    assert "api_server.py" in out.split()


def test_compose_build_contexts_are_not_git_ignored():
    """A build context matched by .gitignore builds here but is missing from
    every clone (GED Phase 9: `backup/` also hid scripts/backup/) - each
    compose context and Dockerfile exists and is not ignored."""
    import re
    import shutil
    import subprocess

    import pytest

    root = Path(api_server.__file__).resolve().parents[2]
    if shutil.which("git") is None or not (root / ".git").exists():
        pytest.skip("needs a git checkout")
    compose = (root / "docker-compose.yml").read_text()
    paths = []
    for context, dockerfile in re.findall(
        r"^\s+context: (\S+)\n(?:\s+dockerfile: (\S+))?", compose, re.M
    ):
        paths += [root / context, root / context / (dockerfile or "Dockerfile")]
    assert len(paths) >= 6  # api, frontend, backup (sync reuses the api image)
    for path in paths:
        assert path.exists(), path
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(path)], cwd=root
        ).returncode
        assert ignored == 1, f"{path.relative_to(root)} is git-ignored"


def test_setup_pulls_every_image_for_the_target_platform():
    """`docker compose pull` keeps an image present for the build machine's
    platform - an arm64 postgres would ship in an amd64 bundle (GED Phase 9)."""
    root = Path(api_server.__file__).resolve().parents[2]
    setup = (root / "setup.sh").read_text()
    assert 'docker pull --quiet --platform "$TARGET_PLATFORM"' in setup
    assert "compose -f docker-compose.yml pull" not in setup
    assert "the bundle would not run on the server" in setup  # mismatch = error


OLLAMA_MODES = {
    # OLLAMA_MODE -> compose files added to docker-compose.yml
    "docker": "",
    "docker-nvidia": ":docker-compose.gpu-nvidia.yml",
    "docker-amd": ":docker-compose.gpu-amd.yml",
    "host": ":docker-compose.ollama-external.yml",
    "remote": ":docker-compose.ollama-external.yml",
}


def test_first_run_and_setup_agree_on_the_ollama_modes():
    root = Path(api_server.__file__).resolve().parents[2]
    first_run = (root / "scripts" / "first-run.sh").read_text()
    setup = (root / "setup.sh").read_text()
    for mode, files in OLLAMA_MODES.items():
        for name in filter(None, files.split(":")):
            assert (root / name).exists(), name
            assert name in first_run and mode in first_run
    assert "choose OLLAMA_MODE" in first_run
    # setup bundles the image each mode runs (none outside Docker)
    assert 'docker-amd) COMPOSE_FILES="docker-compose.yml:docker-compose.gpu-amd.yml"' in setup
    assert (
        'host|remote) COMPOSE_FILES="docker-compose.yml:docker-compose.ollama-external.yml"'
        in setup
    )
    # the model archive never carries that Ollama's private key
    assert '-C /root/.ollama models"' in setup


def test_ollama_modes_render(tmp_path):
    """Each OLLAMA_MODE's compose files render: GPU reservation / ROCm image,
    or no ollama container and the API calling OLLAMA_HOST without waiting."""
    import json
    import os
    import shutil
    import subprocess

    import pytest

    if shutil.which("docker") is None:
        pytest.skip("needs the docker CLI")
    root = Path(api_server.__file__).resolve().parents[2]

    def config(mode):
        env = {
            **os.environ,
            "COMPOSE_FILE": "docker-compose.yml" + OLLAMA_MODES[mode],
            "OLLAMA_HOST": "http://192.0.2.10:11434",
            "COMPOSE_PROJECT_NAME": "sdai_render_test",
        }
        out = subprocess.run(
            ["docker", "compose", "--env-file", os.devnull, "config", "--format", "json"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        if out.returncode != 0 and "unknown" in out.stderr.lower():
            pytest.skip(f"docker compose too old: {out.stderr.strip()}")
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)["services"]

    cpu = config("docker")
    assert cpu["api"]["environment"]["OLLAMA_HOST"] == "http://ollama:11434"
    assert "ollama" in cpu["api"]["depends_on"]
    gpu = config("docker-nvidia")["ollama"]["deploy"]["resources"]["reservations"]
    assert gpu["devices"][0]["driver"] == "nvidia"
    assert config("docker-amd")["ollama"]["image"] == "ollama/ollama:rocm"
    for mode in ("host", "remote"):
        services = config(mode)
        assert "ollama" not in services
        assert services["api"]["environment"]["OLLAMA_HOST"] == "http://192.0.2.10:11434"
        assert sorted(services["api"]["depends_on"]) == ["db", "redis"]
