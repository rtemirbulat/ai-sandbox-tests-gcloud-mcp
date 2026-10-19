import pytest

from sandbox_mcp.ids import new_id, slugify
from sandbox_mcp.services.detect import detect_kind, publish_directory
from sandbox_mcp.services.git import GitError, clone_at, validate_github_url
from sandbox_mcp.services.rules import REQUIREMENTS_VERSION, assess
from sandbox_mcp.settings import Settings

APP_KW = dict(
    owner="u1",
    repo_url="https://github.com/o/r",
    app_name="Demo",
    purpose="p",
    data_classes=["none"],
    kind="static",
    decision="allowed",
    requirements_version="R1",
)


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("SANDBOX_GATEWAY_TOKEN", "t")
    s = Settings(_env_file=None)
    assert s.coolify_fake is True
    assert s.ttl_days == 14
    assert s.database_url.startswith("sqlite")


def test_ids_and_slug():
    assert new_id("app").startswith("app_") and len(new_id("app")) > 10
    assert slugify("Моё Приложение v2!") == "moe-prilozhenie-v2"
    assert slugify("  Hello  World ") == "hello-world"
    assert slugify("!!!") == "app"


def test_create_and_get_application(repo):
    app = repo.create_application(**APP_KW)
    assert app.app_id.startswith("app_") and app.slug == "demo"
    assert repo.get_application(app.app_id).owner == "u1"


def test_slug_collision_gets_suffix(repo):
    a = repo.create_application(**APP_KW)
    b = repo.create_application(**APP_KW)
    assert a.slug == "demo" and b.slug == "demo-2"


def test_job_idempotent_and_claim(repo):
    app = repo.create_application(**APP_KW)
    j1, created1 = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k1")
    j2, created2 = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k1")
    assert created1 and not created2 and j1.job_id == j2.job_id
    claimed = repo.claim_job(lease_seconds=60)
    assert claimed.job_id == j1.job_id and claimed.state == "running"
    assert repo.claim_job(lease_seconds=60) is None
    repo.update_job(
        j1.job_id,
        state="succeeded",
        url="https://x",
        stage="done",
        checks=[{"name": "clone", "result": "passed", "summary": "ok"}],
    )
    got = repo.get_job(j1.job_id)
    assert got.checks[0]["name"] == "clone" and got.state == "succeeded"
    assert repo.latest_job_for_app(app.app_id).job_id == j1.job_id


# detect


def test_detect_static_root(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert detect_kind(tmp_path) == "static" and publish_directory(tmp_path) == "/"


def test_detect_static_in_dist(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x")
    assert detect_kind(tmp_path) == "static" and publish_directory(tmp_path) == "/dist"


def test_detect_node_build(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    assert detect_kind(tmp_path) == "node_build"


def test_detect_dockerfile(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch")
    assert detect_kind(tmp_path) == "dockerfile"


def test_detect_unsupported(tmp_path):
    (tmp_path / "main.py").write_text("print(1)")
    assert detect_kind(tmp_path) == "unsupported"


# rules


def test_rules():
    r = assess("static", ["none"])
    assert r.decision == "allowed" and r.requirements_version == REQUIREMENTS_VERSION
    assert any("TEST" in x for x in r.requirements)
    assert assess("static", ["personal"]).decision == "needs_clarification"
    assert "ПДн" in assess("static", ["personal"]).reason
    assert assess("unsupported", ["none"]).decision == "rejected"
    assert assess("static", ["weird"]).decision == "rejected"


# git


def test_validate_github_url():
    assert validate_github_url("https://github.com/acme/site") == "https://github.com/acme/site"
    assert (
        validate_github_url("https://github.com/acme/site.git/") == "https://github.com/acme/site"
    )
    for bad in [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "git@github.com:a/b.git",
        "https://github.com/a",
        "https://github.com/a/b/c",
    ]:
        with pytest.raises(GitError):
            validate_github_url(bad)


def test_clone_at_ref_and_sha(local_repo, tmp_path):
    src, sha = local_repo
    dest = tmp_path / "d1"
    assert clone_at(src, "main", dest) == sha
    assert (dest / "index.html").exists()
    assert clone_at(src, sha, tmp_path / "d2") == sha


def test_clone_wrong_sha(local_repo, tmp_path):
    src, _ = local_repo
    with pytest.raises(GitError):
        clone_at(src, "b" * 40, tmp_path / "d3")


def test_ensure_schema_adds_missing_column(tmp_path):
    from sqlalchemy import inspect, text

    from sandbox_mcp.db.schema import ensure_schema
    from sandbox_mcp.db.session import make_engine

    engine = make_engine(f"sqlite:///{tmp_path}/old.sqlite3")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE applications (app_id VARCHAR(40) PRIMARY KEY)"))
    ensure_schema(engine)
    ensure_schema(engine)  # idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("applications")}
    assert "git_ref" in cols
