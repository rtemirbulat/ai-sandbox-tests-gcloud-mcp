# Sandbox MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sandbox MCP server (tools `assess_application`, `validate_and_deploy`, `status`) with a job worker that checks a git commit (Gitleaks, Trivy) and deploys it to Coolify, exposed to Claude Code through LiteLLM MCP Gateway on a GCP VM.

**Architecture:** One Python package `sandbox_mcp` with four layers: `db` (SQLAlchemy models + repository), `services` (detect, rules, coolify client, pipeline stages), `mcp` (MCPServer tools mounted in a Starlette app behind a bearer-token middleware), `worker` (lease loop). LiteLLM proxies `/sandbox_mcp/mcp` to the internal service. Everything ships as one Docker image with two entrypoints (`serve`, `worker`) in a compose file alongside LiteLLM, PostgreSQL and Caddy.

**Tech Stack:** Python 3.13, `mcp` 2.3 (`MCPServer`, in-memory `Client` for tests), Starlette/uvicorn, SQLAlchemy 2, pydantic-settings, httpx, pytest + anyio, ruff; Docker Compose; LiteLLM proxy; Caddy; gcloud.

Spec: `docs/superpowers/specs/2026-10-19-sandbox-mcp-design.md`.

Note on SDK version: `mcp` 2.x renamed `FastMCP` → `MCPServer` (`from mcp.server.mcpserver import MCPServer, Context`). HTTP headers of the current request are available as `ctx.headers`. In-process tests use `Client(mcp_server)`.

---

## File structure

```
ai-sandbox-tests-gcloud-mcp/
├── pyproject.toml                 # package, deps, pytest, ruff
├── Makefile                       # install / test / lint / run / compose targets
├── .gitignore, .env.example
├── Dockerfile                     # python:3.13-slim + git + gitleaks + trivy; entry `sandbox-mcp serve|worker`
├── src/sandbox_mcp/
│   ├── __init__.py
│   ├── settings.py                # Settings(SANDBOX_*)
│   ├── ids.py                     # new_id(prefix) -> "app_…" / "job_…", slugify
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py              # Application, Job (SQLAlchemy 2 declarative)
│   │   ├── session.py             # engine/session factory from settings
│   │   └── repo.py                # Repository: create/get app, create/get job (idempotent), claim_job, update_job
│   ├── services/
│   │   ├── __init__.py
│   │   ├── git.py                 # validate_github_url, clone_at(url, ref, dest), head_sha
│   │   ├── detect.py              # detect_kind(path) -> Kind
│   │   ├── rules.py               # assess(kind, data_classes) -> Decision, requirements R1
│   │   ├── scanners.py            # run_gitleaks(path), run_trivy(path) -> CheckResult (subprocess, list args)
│   │   ├── coolify.py             # CoolifyClient protocol, HttpCoolifyClient, FakeCoolifyClient
│   │   └── pipeline.py            # Stage classes + run_job(job, repo, settings, coolify)
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── server.py              # build_mcp(repo, settings) -> MCPServer with 3 tools
│   │   └── app.py                 # build_app(settings) -> Starlette: bearer middleware, /healthz, Mount("/", mcp)
│   ├── worker.py                  # run_worker(settings): loop claim -> run_job; hourly TTL sweep
│   └── cli.py                     # `sandbox-mcp serve|worker|migrate`
├── tests/
│   ├── conftest.py                # sqlite repo fixture, tmp git repo fixture
│   ├── test_detect.py, test_rules.py, test_git.py, test_repo.py
│   ├── test_scanners.py, test_coolify_fake.py, test_pipeline.py
│   ├── test_mcp_tools.py          # in-memory Client against MCPServer
│   └── test_app_auth.py           # httpx against Starlette app: 401 without token, 200 /healthz
├── deploy/
│   ├── compose.yaml               # caddy, litellm, postgres, sandbox-mcp, sandbox-worker
│   ├── Caddyfile
│   ├── litellm/config.yaml        # mcp_servers.sandbox_mcp
│   ├── postgres/init.sql          # create database litellm; sandbox
│   └── .env.example
└── infra/
    ├── create-vm.sh               # gcloud VM + firewall + IAP
    ├── deploy.sh                  # scp deploy/ + docker compose up -d
    └── litellm-key.sh             # /key/generate with mcp_servers
```

---

### Task 1: Project skeleton

**Files:** Create `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`, `src/sandbox_mcp/__init__.py`, `src/sandbox_mcp/settings.py`, `tests/conftest.py`, `tests/test_settings.py`.

- [x] **Step 1: pyproject.toml**

```toml
[project]
name = "sandbox-mcp"
version = "0.1.0"
description = "BI Sandbox MCP: assess, validate and deploy vibe-coded apps to TEST via Coolify"
requires-python = ">=3.13"
dependencies = [
    "mcp>=2.3,<3",
    "starlette>=0.46",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.6",
    "httpx>=0.27",
    "psycopg[binary]>=3.2",
]

[project.scripts]
sandbox-mcp = "sandbox_mcp.cli:main"

[dependency-groups]
dev = ["pytest>=8.3", "anyio>=4", "ruff>=0.8"]

[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

- [x] **Step 2: Makefile, .gitignore, .env.example**

```make
PY=.venv/bin/python
install:
	python3.13 -m venv .venv && .venv/bin/pip install -q -e . --group dev
test:
	$(PY) -m pytest
lint:
	.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
serve:
	$(PY) -m sandbox_mcp.cli serve
worker:
	$(PY) -m sandbox_mcp.cli worker
compose-up:
	docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
compose-down:
	docker compose -f deploy/compose.yaml --env-file deploy/.env down
```

`.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `deploy/.env`, `.secrets/`, `*.sqlite3`, `.pytest_cache/`.

`.env.example`:
```
SANDBOX_DATABASE_URL=sqlite:///./sandbox.sqlite3
SANDBOX_GATEWAY_TOKEN=change-me
SANDBOX_COOLIFY_FAKE=true
SANDBOX_COOLIFY_URL=https://coolify.example
SANDBOX_COOLIFY_TOKEN=
SANDBOX_COOLIFY_PROJECT_UUID=
SANDBOX_COOLIFY_SERVER_UUID=
SANDBOX_COOLIFY_ENVIRONMENT_NAME=production
SANDBOX_APPS_BASE_DOMAIN=apps.example
SANDBOX_TTL_DAYS=14
SANDBOX_WORKDIR=/tmp/sandbox-mcp
```

- [x] **Step 3: failing test `tests/test_settings.py`**

```python
from sandbox_mcp.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("SANDBOX_GATEWAY_TOKEN", "t")
    s = Settings(_env_file=None)
    assert s.coolify_fake is True
    assert s.ttl_days == 14
    assert s.database_url.startswith("sqlite")
```

- [x] **Step 4: `src/sandbox_mcp/settings.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sandbox.sqlite3"
    gateway_token: str
    coolify_fake: bool = True
    coolify_url: str = "https://coolify.example"
    coolify_token: str = ""
    coolify_project_uuid: str = ""
    coolify_server_uuid: str = ""
    coolify_environment_name: str = "production"
    apps_base_domain: str = "apps.example"
    ttl_days: int = 14
    workdir: str = "/tmp/sandbox-mcp"
    host: str = "0.0.0.0"
    port: int = 8080
    lease_seconds: int = 600
    deploy_timeout_seconds: int = 900
```

- [x] **Step 5: install, run, commit**

Run: `make install && make test` → 1 passed.
```bash
git add -A && git commit -m "chore: project skeleton with settings"
```

---

### Task 2: IDs, slug and DB models + repository

**Files:** Create `src/sandbox_mcp/ids.py`, `src/sandbox_mcp/db/__init__.py`, `models.py`, `session.py`, `repo.py`; tests `tests/test_repo.py`, `tests/conftest.py`.

- [x] **Step 1: failing tests**

`tests/conftest.py`:
```python
import pytest

from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.session import make_engine, make_session_factory
from sandbox_mcp.db.models import Base


@pytest.fixture
def repo(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.sqlite3")
    Base.metadata.create_all(engine)
    return Repository(make_session_factory(engine))
```

`tests/test_repo.py`:
```python
from sandbox_mcp.ids import new_id, slugify


def test_ids_and_slug():
    assert new_id("app").startswith("app_") and len(new_id("app")) > 10
    assert slugify("Моё Приложение v2!") == "moe-prilozhenie-v2"
    assert slugify("  Hello  World ") == "hello-world"


def test_create_and_get_application(repo):
    app = repo.create_application(owner="u1", repo_url="https://github.com/o/r", app_name="Demo",
                                  purpose="p", data_classes=["none"], kind="static",
                                  decision="allowed", requirements_version="R1")
    assert app.app_id.startswith("app_") and app.slug == "demo"
    assert repo.get_application(app.app_id).owner == "u1"


def test_slug_collision_gets_suffix(repo):
    kw = dict(owner="u", repo_url="https://github.com/o/r", app_name="Demo", purpose="p",
              data_classes=["none"], kind="static", decision="allowed", requirements_version="R1")
    a = repo.create_application(**kw)
    b = repo.create_application(**kw)
    assert a.slug == "demo" and b.slug.startswith("demo-") and a.slug != b.slug


def test_job_idempotent_and_claim(repo):
    app = repo.create_application(owner="u", repo_url="https://github.com/o/r", app_name="D",
                                  purpose="p", data_classes=["none"], kind="static",
                                  decision="allowed", requirements_version="R1")
    j1, created1 = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k1")
    j2, created2 = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k1")
    assert created1 and not created2 and j1.job_id == j2.job_id
    claimed = repo.claim_job(lease_seconds=60)
    assert claimed.job_id == j1.job_id and claimed.state == "running"
    assert repo.claim_job(lease_seconds=60) is None
    repo.update_job(j1.job_id, state="succeeded", url="https://x", stage="done",
                    checks=[{"name": "clone", "result": "passed", "summary": "ok"}])
    assert repo.get_job(j1.job_id).checks[0]["name"] == "clone"
```

- [x] **Step 2: `ids.py`**

```python
import re
import secrets
import time
import unicodedata

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}{secrets.token_hex(4)}"


def slugify(name: str) -> str:
    s = name.lower().translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "app"
```

- [x] **Step 3: `db/models.py`**

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Application(Base):
    __tablename__ = "applications"
    app_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    repo_url: Mapped[str] = mapped_column(Text)
    app_name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text)
    data_classes: Mapped[list] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(30))
    requirements_version: Mapped[str] = mapped_column(String(10))
    coolify_uuid: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.app_id"))
    commit_sha: Mapped[str] = mapped_column(String(40))
    env: Mapped[str] = mapped_column(String(10))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    checks: Mapped[list] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)
```

- [x] **Step 4: `db/session.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, **kw)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
```

- [x] **Step 5: `db/repo.py`**

```python
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from sandbox_mcp.db.models import Application, Job
from sandbox_mcp.ids import new_id, slugify


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    # applications
    def create_application(self, *, owner, repo_url, app_name, purpose, data_classes, kind,
                           decision, requirements_version) -> Application:
        with self._sf() as s, s.begin():
            base = slugify(app_name)
            slug = base
            n = 2
            while s.scalar(select(Application).where(Application.slug == slug)) is not None:
                slug = f"{base}-{n}"
                n += 1
            app = Application(app_id=new_id("app"), owner=owner, slug=slug, repo_url=repo_url,
                              app_name=app_name, purpose=purpose, data_classes=list(data_classes),
                              kind=kind, decision=decision,
                              requirements_version=requirements_version)
            s.add(app)
            return app

    def get_application(self, app_id: str) -> Application | None:
        with self._sf() as s:
            return s.get(Application, app_id)

    def set_coolify_uuid(self, app_id: str, uuid: str) -> None:
        with self._sf() as s, s.begin():
            s.execute(update(Application).where(Application.app_id == app_id)
                      .values(coolify_uuid=uuid))

    # jobs
    def get_or_create_job(self, app_id, commit_sha, env, idempotency_key) -> tuple[Job, bool]:
        with self._sf() as s, s.begin():
            job = s.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            if job is not None:
                return job, False
            job = Job(job_id=new_id("job"), app_id=app_id, commit_sha=commit_sha, env=env,
                      idempotency_key=idempotency_key)
            s.add(job)
            return job, True

    def get_job(self, job_id: str) -> Job | None:
        with self._sf() as s:
            return s.get(Job, job_id)

    def latest_job_for_app(self, app_id: str) -> Job | None:
        with self._sf() as s:
            return s.scalar(select(Job).where(Job.app_id == app_id)
                            .order_by(Job.created_at.desc()).limit(1))

    def claim_job(self, lease_seconds: int) -> Job | None:
        now = datetime.utcnow()
        with self._sf() as s, s.begin():
            q = (select(Job).where(
                    (Job.state == "queued") |
                    ((Job.state == "running") & (Job.lease_until < now)))
                 .order_by(Job.created_at).limit(1))
            if s.bind.dialect.name == "postgresql":
                q = q.with_for_update(skip_locked=True)
            job = s.scalar(q)
            if job is None:
                return None
            job.state = "running"
            job.lease_until = now + timedelta(seconds=lease_seconds)
            return job

    def update_job(self, job_id: str, **fields) -> None:
        with self._sf() as s, s.begin():
            s.execute(update(Job).where(Job.job_id == job_id).values(**fields))

    def expired_jobs(self) -> list[Job]:
        with self._sf() as s:
            return list(s.scalars(select(Job).where(
                (Job.state == "succeeded") & (Job.expires_at < datetime.utcnow()))))
```

- [x] **Step 6: run + commit**

Run: `make test` → 5 passed. `git commit -am "feat: ids, models, repository"` (after `git add -A`).

---

### Task 3: git helpers and project-kind detection

**Files:** Create `src/sandbox_mcp/services/__init__.py`, `git.py`, `detect.py`; tests `tests/test_git.py`, `tests/test_detect.py`.

- [x] **Step 1: failing tests**

`tests/test_detect.py`:
```python
from sandbox_mcp.services.detect import detect_kind


def test_static_root(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert detect_kind(tmp_path) == "static"


def test_static_in_dist(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x")
    assert detect_kind(tmp_path) == "static"


def test_node_build(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    assert detect_kind(tmp_path) == "node_build"


def test_dockerfile(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch")
    assert detect_kind(tmp_path) == "dockerfile"


def test_unsupported(tmp_path):
    (tmp_path / "main.py").write_text("print(1)")
    assert detect_kind(tmp_path) == "unsupported"
```

`tests/test_git.py`:
```python
import subprocess

import pytest

from sandbox_mcp.services.git import GitError, clone_at, validate_github_url


def test_validate_github_url():
    assert validate_github_url("https://github.com/acme/site") == "https://github.com/acme/site"
    assert validate_github_url("https://github.com/acme/site.git/") == "https://github.com/acme/site"
    for bad in ["http://github.com/a/b", "https://gitlab.com/a/b", "git@github.com:a/b.git",
                "https://github.com/a", "https://github.com/a/b/c"]:
        with pytest.raises(GitError):
            validate_github_url(bad)


@pytest.fixture
def local_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "index.html").write_text("hi")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "i"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    return src, sha


def test_clone_at_ref_and_sha(local_repo, tmp_path):
    src, sha = local_repo
    dest = tmp_path / "d1"
    assert clone_at(str(src), "main", dest) == sha
    assert (dest / "index.html").exists()
    dest2 = tmp_path / "d2"
    assert clone_at(str(src), sha, dest2) == sha


def test_clone_wrong_sha(local_repo, tmp_path):
    src, _ = local_repo
    with pytest.raises(GitError):
        clone_at(str(src), "b" * 40, tmp_path / "d3")
```

- [x] **Step 2: `services/git.py`**

```python
import re
import subprocess
from pathlib import Path

_GH = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitError(Exception):
    pass


def validate_github_url(url: str) -> str:
    m = _GH.match(url.strip())
    if not m:
        raise GitError("Разрешены только публичные репозитории вида https://github.com/<owner>/<repo>")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                           timeout=300, env={"GIT_TERMINAL_PROMPT": "0"})
    except subprocess.CalledProcessError as e:
        raise GitError(e.stderr.strip()[-500:] or "git failed") from e
    except subprocess.TimeoutExpired as e:
        raise GitError("git timeout") from e
    return p.stdout.strip()


def clone_at(url: str, ref: str, dest: Path) -> str:
    """Clone `url` and check out `ref` (branch, tag or 40-hex sha). Returns the HEAD sha."""
    dest.mkdir(parents=True, exist_ok=True)
    if _SHA.match(ref):
        _git("init", "-q", str(dest))
        _git("remote", "add", "origin", url, cwd=dest)
        _git("fetch", "-q", "--depth", "1", "origin", ref, cwd=dest)
        _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
    else:
        _git("clone", "-q", "--depth", "1", "--branch", ref, url, str(dest))
    sha = _git("rev-parse", "HEAD", cwd=dest)
    if _SHA.match(ref) and sha != ref:
        raise GitError("Полученный commit не совпадает с запрошенным")
    return sha
```

- [x] **Step 3: `services/detect.py`**

```python
import json
from pathlib import Path
from typing import Literal

Kind = Literal["static", "node_build", "dockerfile", "unsupported"]
STATIC_DIRS = ("dist", "build", "public", "out")


def detect_kind(path: Path) -> Kind:
    if (path / "Dockerfile").is_file():
        return "dockerfile"
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        if "build" in scripts:
            return "node_build"
    if (path / "index.html").is_file():
        return "static"
    for d in STATIC_DIRS:
        if (path / d / "index.html").is_file():
            return "static"
    return "unsupported"


def publish_directory(path: Path) -> str:
    if (path / "index.html").is_file():
        return "/"
    for d in STATIC_DIRS:
        if (path / d / "index.html").is_file():
            return f"/{d}"
    return "/"
```

- [x] **Step 4: run + commit** → `make test` all pass; `git add -A && git commit -m "feat: git clone-at and project kind detection"`.

---

### Task 4: Admission rules

**Files:** Create `src/sandbox_mcp/services/rules.py`; test `tests/test_rules.py`.

- [x] **Step 1: failing test**

```python
from sandbox_mcp.services.rules import REQUIREMENTS_VERSION, assess


def test_allowed_static():
    r = assess("static", ["none"])
    assert r.decision == "allowed" and r.requirements_version == REQUIREMENTS_VERSION
    assert any("TEST" in x for x in r.requirements)


def test_personal_data_needs_clarification():
    r = assess("static", ["personal"])
    assert r.decision == "needs_clarification" and "ПДн" in r.reason


def test_unsupported_rejected():
    r = assess("unsupported", ["none"])
    assert r.decision == "rejected"


def test_unknown_class_rejected():
    r = assess("static", ["weird"])
    assert r.decision == "rejected"
```

- [x] **Step 2: `services/rules.py`**

```python
from dataclasses import dataclass, field

REQUIREMENTS_VERSION = "R1"
ALLOWED_CLASSES = {"none", "internal", "personal", "financial"}
SENSITIVE = {"personal", "financial"}

TEST_REQUIREMENTS = [
    "Код хранится в публичном GitHub-репозитории; деплоится только проверенный commit.",
    "Секреты в репозитории запрещены (проверка Gitleaks блокирует выпуск).",
    "Уязвимости HIGH/CRITICAL в зависимостях и конфигурации блокируют выпуск (Trivy).",
    "Окружение TEST: TTL 14 дней, один контейнер, без доступа к корпоративным данным.",
    "Пользовательский Dockerfile допускается, но образ работает без root и без docker.sock.",
]


@dataclass
class Assessment:
    decision: str  # allowed | needs_clarification | rejected
    reason: str = ""
    requirements_version: str = REQUIREMENTS_VERSION
    requirements: list[str] = field(default_factory=list)


def assess(kind: str, data_classes: list[str]) -> Assessment:
    classes = set(data_classes or ["none"])
    if not classes <= ALLOWED_CLASSES:
        return Assessment("rejected",
                          f"Неизвестные классы данных: {sorted(classes - ALLOWED_CLASSES)}")
    if kind == "unsupported":
        return Assessment("rejected", "Не распознан тип проекта: нужен index.html, "
                                      "package.json со скриптом build или Dockerfile")
    if classes & SENSITIVE:
        return Assessment("needs_clarification",
                          "Заявлены ПДн или финансовые данные: профиль TEST без ПДн не "
                          "присваивается автоматически, нужно согласование владельца данных",
                          requirements=TEST_REQUIREMENTS)
    return Assessment("allowed", requirements=TEST_REQUIREMENTS)
```

- [x] **Step 3: run + commit** → `git add -A && git commit -m "feat: admission rules R1"`.

---

### Task 5: Scanners (Gitleaks, Trivy)

**Files:** Create `src/sandbox_mcp/services/scanners.py`; test `tests/test_scanners.py`.

- [x] **Step 1: failing test** (fakes the binaries with shell scripts on PATH)

```python
import os
import stat

import pytest

from sandbox_mcp.services.scanners import run_gitleaks, run_trivy


def _fake_bin(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return d


@pytest.fixture
def path_with(tmp_path, monkeypatch):
    def _set(name, body):
        d = _fake_bin(tmp_path, name, body)
        monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return _set


def test_gitleaks_clean(path_with, tmp_path):
    path_with("gitleaks", "exit 0")
    r = run_gitleaks(tmp_path)
    assert r.name == "gitleaks" and r.result == "passed"


def test_gitleaks_findings(path_with, tmp_path):
    path_with("gitleaks", 'echo "[{\\"RuleID\\":\\"aws\\",\\"File\\":\\"a.env\\"}]" > "$REPORT"; exit 1')
    r = run_gitleaks(tmp_path)
    assert r.result == "failed" and "1" in r.summary


def test_trivy_findings(path_with, tmp_path):
    path_with("trivy", "echo found; exit 1")
    r = run_trivy(tmp_path)
    assert r.result == "failed"


def test_missing_binary_is_error(path_with, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert run_trivy(tmp_path).result == "error"
```

Note: in the gitleaks fake the report path is passed via `--report-path`; the implementation must export it as `REPORT` env var for the fake to see it. Simpler: the implementation always passes `--report-path <workdir>/gitleaks.json` **and** sets `env={"REPORT": that_path, **os.environ}`. (Harmless in production.)

- [x] **Step 2: `services/scanners.py`**

```python
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    result: str  # passed | failed | error | pending | skipped
    summary: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "result": self.result, "summary": self.summary}


def _run(name: str, args: list[str], env: dict | None = None) -> tuple[int, str] | None:
    if shutil.which(args[0]) is None:
        return None
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=600,
                           env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return p.returncode, (p.stdout + p.stderr)[-2000:]


def run_gitleaks(path: Path) -> CheckResult:
    report = path.parent / f"{path.name}.gitleaks.json"
    out = _run("gitleaks", ["gitleaks", "detect", "--no-git", "--source", str(path),
                            "--report-format", "json", "--report-path", str(report),
                            "--exit-code", "1", "--no-banner"],
               env={"REPORT": str(report)})
    if out is None:
        return CheckResult("gitleaks", "error", "gitleaks не установлен")
    code, text = out
    if code == 0:
        return CheckResult("gitleaks", "passed", "Секретов не найдено")
    if code == 1:
        n = 0
        if report.exists():
            try:
                n = len(json.loads(report.read_text() or "[]"))
            except json.JSONDecodeError:
                n = -1
        return CheckResult("gitleaks", "failed", f"Найдено секретов: {n}. Удалите их из репозитория")
    return CheckResult("gitleaks", "error", f"gitleaks завершился с кодом {code}: {text[-300:]}")


def run_trivy(path: Path) -> CheckResult:
    out = _run("trivy", ["trivy", "fs", "--scanners", "vuln,misconfig", "--severity",
                         "HIGH,CRITICAL", "--exit-code", "1", "--quiet", "--format", "table",
                         str(path)])
    if out is None:
        return CheckResult("trivy", "error", "trivy не установлен")
    code, text = out
    if code == 0:
        return CheckResult("trivy", "passed", "Уязвимостей HIGH/CRITICAL не найдено")
    if code == 1:
        return CheckResult("trivy", "failed", "Найдены уязвимости HIGH/CRITICAL:\n" + text[-1200:])
    return CheckResult("trivy", "error", f"trivy завершился с кодом {code}: {text[-300:]}")
```

- [x] **Step 3: run + commit** → `git add -A && git commit -m "feat: gitleaks and trivy scanners"`.

---

### Task 6: Coolify client (HTTP + fake)

**Files:** Create `src/sandbox_mcp/services/coolify.py`; test `tests/test_coolify.py`.

- [x] **Step 1: failing test**

```python
import httpx
import pytest

from sandbox_mcp.services.coolify import (AppSpec, FakeCoolifyClient, HttpCoolifyClient)


def spec():
    return AppSpec(name="demo", repo_url="https://github.com/o/r", branch="main",
                   commit_sha="a" * 40, build_pack="static", publish_directory="/",
                   domain="https://demo.apps.example", ports_exposes="80")


def test_fake_lifecycle():
    c = FakeCoolifyClient()
    uuid = c.create_application(spec())
    dep = c.deploy(uuid)
    assert c.deployment_status(dep) == "finished"
    assert c.application_url(uuid) == "https://demo.apps.example"
    c.delete_application(uuid)
    assert uuid not in c.apps


def test_http_client_calls(monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.url.path == "/api/v1/applications/public":
            return httpx.Response(201, json={"uuid": "app-1"})
        if req.url.path == "/api/v1/deploy":
            return httpx.Response(200, json={"deployments": [{"deployment_uuid": "dep-1"}]})
        if req.url.path == "/api/v1/deployments/dep-1":
            return httpx.Response(200, json={"status": "finished"})
        if req.url.path == "/api/v1/applications/app-1":
            return httpx.Response(200, json={"fqdn": "https://demo.apps.example"})
        return httpx.Response(404)

    c = HttpCoolifyClient("https://coolify.test", "tok", project_uuid="p", server_uuid="s",
                          environment_name="production",
                          transport=httpx.MockTransport(handler))
    uuid = c.create_application(spec())
    dep = c.deploy(uuid)
    assert uuid == "app-1" and dep == "dep-1"
    assert c.deployment_status(dep) == "finished"
    assert c.application_url(uuid) == "https://demo.apps.example"
    assert all(a == "Bearer tok" for _, _, a in calls)
```

- [x] **Step 2: `services/coolify.py`**

```python
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class AppSpec:
    name: str
    repo_url: str
    branch: str
    commit_sha: str
    build_pack: str  # static | nixpacks | dockerfile
    publish_directory: str
    domain: str
    ports_exposes: str = "80"


class CoolifyError(Exception):
    pass


class CoolifyClient(Protocol):
    def create_application(self, spec: AppSpec) -> str: ...
    def update_source(self, uuid: str, spec: AppSpec) -> None: ...
    def deploy(self, uuid: str) -> str: ...
    def deployment_status(self, deployment_uuid: str) -> str: ...
    def application_url(self, uuid: str) -> str: ...
    def delete_application(self, uuid: str) -> None: ...


class FakeCoolifyClient:
    def __init__(self):
        self.apps: dict[str, AppSpec] = {}
        self.deployments: dict[str, str] = {}
        self._n = 0

    def create_application(self, spec: AppSpec) -> str:
        self._n += 1
        uuid = f"fake-app-{self._n}"
        self.apps[uuid] = spec
        return uuid

    def update_source(self, uuid: str, spec: AppSpec) -> None:
        self.apps[uuid] = spec

    def deploy(self, uuid: str) -> str:
        if uuid not in self.apps:
            raise CoolifyError("unknown app")
        dep = f"fake-dep-{uuid}"
        self.deployments[dep] = "finished"
        return dep

    def deployment_status(self, deployment_uuid: str) -> str:
        return self.deployments.get(deployment_uuid, "failed")

    def application_url(self, uuid: str) -> str:
        return self.apps[uuid].domain

    def delete_application(self, uuid: str) -> None:
        self.apps.pop(uuid, None)


class HttpCoolifyClient:
    def __init__(self, base_url: str, token: str, *, project_uuid: str, server_uuid: str,
                 environment_name: str, transport: httpx.BaseTransport | None = None):
        self._c = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1",
                               headers={"Authorization": f"Bearer {token}",
                                        "Accept": "application/json"},
                               timeout=60, transport=transport)
        self.project_uuid = project_uuid
        self.server_uuid = server_uuid
        self.environment_name = environment_name

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self._c.request(method, path, **kw)
        if r.status_code >= 400:
            raise CoolifyError(f"Coolify {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def create_application(self, spec: AppSpec) -> str:
        body = {
            "project_uuid": self.project_uuid, "server_uuid": self.server_uuid,
            "environment_name": self.environment_name,
            "git_repository": spec.repo_url, "git_branch": spec.branch,
            "git_commit_sha": spec.commit_sha, "build_pack": spec.build_pack,
            "name": spec.name, "domains": spec.domain, "ports_exposes": spec.ports_exposes,
            "instant_deploy": False,
        }
        if spec.build_pack == "static":
            body["publish_directory"] = spec.publish_directory
        if spec.build_pack == "nixpacks":
            body["is_static"] = True
            body["publish_directory"] = spec.publish_directory
        return self._req("POST", "/applications/public", json=body)["uuid"]

    def update_source(self, uuid: str, spec: AppSpec) -> None:
        self._req("PATCH", f"/applications/{uuid}",
                  json={"git_branch": spec.branch, "git_commit_sha": spec.commit_sha})

    def deploy(self, uuid: str) -> str:
        data = self._req("POST", "/deploy", params={"uuid": uuid, "force": "true"})
        deps = data.get("deployments") or []
        if not deps:
            raise CoolifyError("Coolify не вернул deployment_uuid")
        return deps[0]["deployment_uuid"]

    def deployment_status(self, deployment_uuid: str) -> str:
        return self._req("GET", f"/deployments/{deployment_uuid}").get("status", "unknown")

    def application_url(self, uuid: str) -> str:
        fqdn = self._req("GET", f"/applications/{uuid}").get("fqdn") or ""
        return fqdn.split(",")[0]

    def delete_application(self, uuid: str) -> None:
        self._req("DELETE", f"/applications/{uuid}",
                  params={"delete_configurations": "true", "delete_volumes": "true"})


def make_coolify_client(settings) -> CoolifyClient:
    if settings.coolify_fake:
        return FakeCoolifyClient()
    return HttpCoolifyClient(settings.coolify_url, settings.coolify_token,
                             project_uuid=settings.coolify_project_uuid,
                             server_uuid=settings.coolify_server_uuid,
                             environment_name=settings.coolify_environment_name)
```

- [x] **Step 3: run + commit** → `git add -A && git commit -m "feat: coolify client with fake"`.

---

### Task 7: Pipeline

**Files:** Create `src/sandbox_mcp/services/pipeline.py`; test `tests/test_pipeline.py`.

- [x] **Step 1: failing test** (uses local git repo fixture; scanners monkeypatched)

```python
import subprocess

import pytest

from sandbox_mcp.services import pipeline
from sandbox_mcp.services.coolify import FakeCoolifyClient
from sandbox_mcp.services.scanners import CheckResult
from sandbox_mcp.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, gateway_token="t", workdir=str(tmp_path / "work"),
                    apps_base_domain="apps.test", deploy_timeout_seconds=5)


@pytest.fixture
def repo_with_commit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "index.html").write_text("hi")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "i"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    return str(src), sha


def _app(repo, url):
    return repo.create_application(owner="u", repo_url=url, app_name="Demo", purpose="p",
                                   data_classes=["none"], kind="static", decision="allowed",
                                   requirements_version="R1")


def test_happy_path(repo, settings, repo_with_commit, monkeypatch):
    url, sha = repo_with_commit
    monkeypatch.setattr(pipeline, "run_gitleaks", lambda p: CheckResult("gitleaks", "passed"))
    monkeypatch.setattr(pipeline, "run_trivy", lambda p: CheckResult("trivy", "passed"))
    monkeypatch.setattr(pipeline, "smoke_get", lambda url, timeout: CheckResult("smoke", "passed"))
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    coolify = FakeCoolifyClient()
    pipeline.run_job(job.job_id, repo, settings, coolify)
    j = repo.get_job(job.job_id)
    assert j.state == "succeeded" and j.url == "https://demo.apps.test" and j.expires_at
    assert [c["name"] for c in j.checks] == ["clone", "detect", "gitleaks", "trivy",
                                             "coolify_create", "coolify_deploy", "smoke"]
    assert repo.get_application(app.app_id).coolify_uuid == "fake-app-1"


def test_gitleaks_blocks(repo, settings, repo_with_commit, monkeypatch):
    url, sha = repo_with_commit
    monkeypatch.setattr(pipeline, "run_gitleaks",
                        lambda p: CheckResult("gitleaks", "failed", "Найдено секретов: 1"))
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    j = repo.get_job(job.job_id)
    assert j.state == "blocked" and j.stage == "gitleaks"
    assert j.checks[-1]["result"] == "failed" and len(j.checks) == 3


def test_scanner_error_fails(repo, settings, repo_with_commit, monkeypatch):
    url, sha = repo_with_commit
    monkeypatch.setattr(pipeline, "run_gitleaks", lambda p: CheckResult("gitleaks", "error", "x"))
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    assert repo.get_job(job.job_id).state == "failed"


def test_bad_sha_fails_at_clone(repo, settings, repo_with_commit):
    url, _ = repo_with_commit
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, "b" * 40, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    j = repo.get_job(job.job_id)
    assert j.state == "failed" and j.stage == "clone" and j.error
```

- [x] **Step 2: `services/pipeline.py`**

```python
import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from sandbox_mcp.db.repo import Repository
from sandbox_mcp.services.coolify import AppSpec, CoolifyClient, CoolifyError
from sandbox_mcp.services.detect import detect_kind, publish_directory
from sandbox_mcp.services.git import GitError, clone_at
from sandbox_mcp.services.scanners import CheckResult, run_gitleaks, run_trivy
from sandbox_mcp.settings import Settings

log = logging.getLogger(__name__)
BUILD_PACK = {"static": "static", "node_build": "nixpacks", "dockerfile": "dockerfile"}


class Blocked(Exception):
    """Проверка не пройдена: нужно исправить код (state=blocked)."""


class Failed(Exception):
    """Ошибка платформы или деплоя (state=failed)."""


def smoke_get(url: str, timeout: int = 60) -> CheckResult:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                return CheckResult("smoke", "passed", f"GET {url} → 200")
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = type(e).__name__
        time.sleep(3)
    return CheckResult("smoke", "failed", f"GET {url}: {last or 'нет ответа'} за {timeout} с")


def run_job(job_id: str, repo: Repository, settings: Settings, coolify: CoolifyClient) -> None:
    job = repo.get_job(job_id)
    app = repo.get_application(job.app_id)
    checks: list[dict] = []
    workdir = Path(settings.workdir) / job_id
    shutil.rmtree(workdir, ignore_errors=True)

    def record(check: CheckResult, stage: str) -> None:
        checks.append(check.as_dict())
        repo.update_job(job_id, stage=stage, checks=list(checks))
        if check.result == "failed":
            raise Blocked(check.summary)
        if check.result == "error":
            raise Failed(check.summary)

    try:
        # clone
        repo.update_job(job_id, stage="clone")
        try:
            sha = clone_at(app.repo_url, job.commit_sha, workdir / "src")
        except GitError as e:
            record(CheckResult("clone", "error", str(e)), "clone")
        record(CheckResult("clone", "passed", f"commit {sha[:12]}"), "clone")

        # detect
        kind = detect_kind(workdir / "src")
        if kind == "unsupported":
            record(CheckResult("detect", "failed", "Тип проекта не распознан"), "detect")
        record(CheckResult("detect", "passed", kind), "detect")

        # scanners
        record(run_gitleaks(workdir / "src"), "gitleaks")
        record(run_trivy(workdir / "src"), "trivy")

        # coolify
        domain = f"https://{app.slug}.{settings.apps_base_domain}"
        spec = AppSpec(name=f"{app.slug}-{app.app_id[-6:]}", repo_url=app.repo_url,
                       branch="main", commit_sha=sha, build_pack=BUILD_PACK[kind],
                       publish_directory=publish_directory(workdir / "src"), domain=domain)
        try:
            uuid = app.coolify_uuid
            if uuid:
                coolify.update_source(uuid, spec)
            else:
                uuid = coolify.create_application(spec)
                repo.set_coolify_uuid(app.app_id, uuid)
            record(CheckResult("coolify_create", "passed", f"application {uuid}"), "coolify_create")

            dep = coolify.deploy(uuid)
            deadline = time.time() + settings.deploy_timeout_seconds
            status = "queued"
            while time.time() < deadline:
                status = coolify.deployment_status(dep)
                if status in ("finished", "failed", "cancelled-by-user"):
                    break
                time.sleep(5)
            if status != "finished":
                record(CheckResult("coolify_deploy", "error",
                                   f"deployment {dep}: {status}"), "coolify_deploy")
            record(CheckResult("coolify_deploy", "passed", f"deployment {dep}"), "coolify_deploy")
            url = coolify.application_url(uuid) or domain
        except CoolifyError as e:
            record(CheckResult("coolify_deploy", "error", str(e)), "coolify_deploy")

        # smoke
        smoke = smoke_get(url, 60)
        if smoke.result == "failed":
            smoke.result = "error"  # not the author's code problem: platform-level failure
        record(smoke, "smoke")

        repo.update_job(job_id, state="succeeded", stage="done", url=url, error=None,
                        expires_at=datetime.utcnow() + timedelta(days=settings.ttl_days))
    except Blocked as e:
        repo.update_job(job_id, state="blocked", error=str(e))
    except Failed as e:
        repo.update_job(job_id, state="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("job %s crashed", job_id)
        repo.update_job(job_id, state="failed", error=f"internal: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [x] **Step 3: run + commit** → `git add -A && git commit -m "feat: job pipeline clone→scan→coolify→smoke"`.

---

### Task 8: MCP tools

**Files:** Create `src/sandbox_mcp/mcp/__init__.py`, `src/sandbox_mcp/mcp/server.py`; test `tests/test_mcp_tools.py`.

- [x] **Step 1: failing test**

```python
import subprocess

import pytest
from mcp import Client

from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def local_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "index.html").write_text("hi")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "i"], check=True, env=env)
    return str(src)


@pytest.fixture
def mcp(repo, tmp_path, monkeypatch):
    s = Settings(_env_file=None, gateway_token="t", workdir=str(tmp_path / "w"))
    # allow file:// style local paths in tests
    monkeypatch.setattr("sandbox_mcp.mcp.server.validate_github_url", lambda u: u)
    return build_mcp(repo, s)


async def test_list_tools(mcp):
    async with Client(mcp) as c:
        names = sorted(t.name for t in (await c.list_tools()).tools)
    assert names == ["assess_application", "status", "validate_and_deploy"]


async def test_assess_then_deploy_then_status(mcp, local_repo):
    async with Client(mcp, raise_exceptions=True) as c:
        a = (await c.call_tool("assess_application", {
            "repo_url": local_repo, "git_ref": "main", "app_name": "Demo",
            "purpose": "тест", "data_classes": ["none"]})).structured_content
        assert a["decision"] == "allowed" and a["kind"] == "static"
        sha = a["commit_sha"]
        d = (await c.call_tool("validate_and_deploy", {
            "app_id": a["app_id"], "commit_sha": sha, "env": "TEST",
            "idempotency_key": "k1"})).structured_content
        assert d["created"] is True and d["state"] == "queued"
        d2 = (await c.call_tool("validate_and_deploy", {
            "app_id": a["app_id"], "commit_sha": sha, "env": "TEST",
            "idempotency_key": "k1"})).structured_content
        assert d2["created"] is False and d2["job_id"] == d["job_id"]
        s = (await c.call_tool("status", {"job_id": d["job_id"]})).structured_content
        assert s["state"] == "queued" and "next_action" in s


async def test_prod_rejected(mcp, local_repo):
    async with Client(mcp, raise_exceptions=True) as c:
        a = (await c.call_tool("assess_application", {
            "repo_url": local_repo, "git_ref": "main", "app_name": "Demo",
            "purpose": "p", "data_classes": ["none"]})).structured_content
        d = (await c.call_tool("validate_and_deploy", {
            "app_id": a["app_id"], "commit_sha": a["commit_sha"], "env": "PROD",
            "idempotency_key": "k"})).structured_content
        assert d["error"] == "env_not_allowed"


async def test_personal_data_blocks_deploy(mcp, local_repo):
    async with Client(mcp, raise_exceptions=True) as c:
        a = (await c.call_tool("assess_application", {
            "repo_url": local_repo, "git_ref": "main", "app_name": "PD",
            "purpose": "p", "data_classes": ["personal"]})).structured_content
        assert a["decision"] == "needs_clarification"
        d = (await c.call_tool("validate_and_deploy", {
            "app_id": a["app_id"], "commit_sha": a["commit_sha"], "env": "TEST",
            "idempotency_key": "k"})).structured_content
        assert d["error"] == "not_allowed"
```

- [x] **Step 2: `mcp/server.py`**

```python
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from sandbox_mcp.db.repo import Repository
from sandbox_mcp.services.detect import detect_kind
from sandbox_mcp.services.git import GitError, clone_at, validate_github_url
from sandbox_mcp.services.rules import assess
from sandbox_mcp.settings import Settings

_SHA = re.compile(r"^[0-9a-f]{40}$")
ACTOR_HEADERS = ("x-litellm-user-email", "x-litellm-user-id", "x-litellm-key-alias",
                 "x-litellm-api-key-alias", "x-forwarded-user")

NEXT_ACTION = {
    "queued": "Задание в очереди. Вызовите status через 30–60 секунд.",
    "running": "Идут проверки и деплой. Вызовите status через 30–60 секунд.",
    "succeeded": "Приложение доступно по url до expires_at. Проверьте его и соберите обратную связь.",
    "blocked": "Проверки не пройдены: исправьте код по замечаниям в checks, сделайте новый commit "
               "и вызовите validate_and_deploy с новым commit_sha.",
    "failed": "Ошибка платформы или деплоя. Повторите validate_and_deploy с новым idempotency_key; "
              "если повторяется, передайте job_id в поддержку песочницы.",
    "expired": "Срок жизни TEST истёк, приложение удалено. Для нового выпуска вызовите "
               "validate_and_deploy.",
}


def actor_from(ctx: Context) -> str:
    headers = ctx.headers or {}
    for h in ACTOR_HEADERS:
        if headers.get(h):
            return headers[h]
    return "unknown"


def build_mcp(repo: Repository, settings: Settings) -> MCPServer:
    mcp = MCPServer(
        "sandbox", version="0.1.0",
        instructions="Песочница BI: оцените приложение (assess_application), запросите выпуск в "
                     "TEST (validate_and_deploy) и следите за ним (status). PROD не поддерживается.",
    )

    @mcp.tool()
    def assess_application(
        repo_url: Annotated[str, Field(description="Публичный репозиторий https://github.com/<owner>/<repo>")],
        git_ref: Annotated[str, Field(description="Ветка, тег или 40-символьный SHA")],
        app_name: Annotated[str, Field(description="Название приложения (станет частью URL)")],
        purpose: Annotated[str, Field(description="Назначение и бизнес-польза, 1–3 предложения")],
        data_classes: Annotated[list[str], Field(description="Классы данных: none, internal, personal, financial")],
        ctx: Context,
    ) -> dict:
        """Оценить приложение: тип проекта, допуск к TEST и требования версии R1."""
        try:
            url = validate_github_url(repo_url)
        except GitError as e:
            return {"decision": "rejected", "reason": str(e)}
        tmp = Path(tempfile.mkdtemp(prefix="assess-", dir=_ensure(settings.workdir)))
        try:
            try:
                sha = clone_at(url, git_ref, tmp / "src")
            except GitError as e:
                return {"decision": "rejected", "reason": f"Не удалось получить код: {e}"}
            kind = detect_kind(tmp / "src")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        a = assess(kind, data_classes)
        app = repo.create_application(owner=actor_from(ctx), repo_url=url, app_name=app_name,
                                      purpose=purpose, data_classes=data_classes, kind=kind,
                                      decision=a.decision,
                                      requirements_version=a.requirements_version)
        return {"app_id": app.app_id, "slug": app.slug, "kind": kind, "commit_sha": sha,
                "decision": a.decision, "reason": a.reason,
                "requirements_version": a.requirements_version, "requirements": a.requirements}

    @mcp.tool()
    def validate_and_deploy(
        app_id: Annotated[str, Field(description="app_id из assess_application")],
        commit_sha: Annotated[str, Field(description="40-символьный SHA коммита для выпуска")],
        env: Annotated[str, Field(description="Окружение; поддерживается только TEST")],
        idempotency_key: Annotated[str, Field(description="Уникальный ключ запроса; повтор с тем же ключом вернёт то же задание")],
        ctx: Context,
    ) -> dict:
        """Поставить задание: проверить commit (Gitleaks, Trivy) и развернуть его в TEST."""
        if env != "TEST":
            return {"error": "env_not_allowed",
                    "message": "Выпуск возможен только в TEST; PROD требует отдельного согласования"}
        if not _SHA.match(commit_sha):
            return {"error": "bad_commit_sha", "message": "Нужен полный 40-символьный SHA"}
        app = repo.get_application(app_id)
        if app is None or not _owns(actor_from(ctx), app.owner):
            return {"error": "not_found", "message": "Приложение не найдено или недоступно"}
        if app.decision != "allowed":
            return {"error": "not_allowed",
                    "message": f"Решение оценки: {app.decision}. Повторите assess_application "
                               "после уточнения данных"}
        job, created = repo.get_or_create_job(app_id, commit_sha, env, idempotency_key)
        return {"job_id": job.job_id, "state": job.state, "created": created}

    @mcp.tool()
    def status(
        ctx: Context,
        job_id: Annotated[str | None, Field(description="job_id из validate_and_deploy")] = None,
        app_id: Annotated[str | None, Field(description="app_id: вернуть последнее задание приложения")] = None,
    ) -> dict:
        """Статус задания: стадия, результаты проверок, URL, срок жизни и следующий шаг."""
        job = repo.get_job(job_id) if job_id else (repo.latest_job_for_app(app_id) if app_id else None)
        if job is None:
            return {"error": "not_found", "message": "Задание не найдено"}
        app = repo.get_application(job.app_id)
        if not _owns(actor_from(ctx), app.owner):
            return {"error": "not_found", "message": "Задание не найдено"}
        return {"job_id": job.job_id, "app_id": job.app_id, "commit_sha": job.commit_sha,
                "env": job.env, "state": job.state, "stage": job.stage, "checks": job.checks,
                "url": job.url,
                "expires_at": job.expires_at.isoformat() if job.expires_at else None,
                "error": job.error, "next_action": NEXT_ACTION.get(job.state, "")}

    return mcp


def _owns(actor: str, owner: str) -> bool:
    return actor == "unknown" or owner == "unknown" or actor == owner


def _ensure(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return path
```

- [x] **Step 3: run + commit** → `make test`; `git add -A && git commit -m "feat: MCP tools assess/validate_and_deploy/status"`.

---

### Task 9: Starlette app with gateway-token middleware, worker loop, CLI

**Files:** Create `src/sandbox_mcp/mcp/app.py`, `src/sandbox_mcp/worker.py`, `src/sandbox_mcp/cli.py`; test `tests/test_app_auth.py`, `tests/test_worker.py`.

- [x] **Step 1: failing tests**

`tests/test_app_auth.py`:
```python
import httpx
import pytest

from sandbox_mcp.mcp.app import build_app
from sandbox_mcp.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_healthz_open_and_mcp_requires_token(tmp_path):
    s = Settings(_env_file=None, gateway_token="secret",
                 database_url=f"sqlite:///{tmp_path}/a.sqlite3", workdir=str(tmp_path))
    app = build_app(s)
    async with app.router.lifespan_context(app):
        tr = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://t") as c:
            assert (await c.get("/healthz")).status_code == 200
            r = await c.post("/mcp", json={})
            assert r.status_code == 401
            r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"Authorization": "Bearer secret",
                                      "Accept": "application/json, text/event-stream",
                                      "Content-Type": "application/json"})
            assert r.status_code != 401
```

`tests/test_worker.py`:
```python
from sandbox_mcp import worker


def test_tick_runs_claimed_job(repo, monkeypatch):
    app = repo.create_application(owner="u", repo_url="https://github.com/o/r", app_name="D",
                                  purpose="p", data_classes=["none"], kind="static",
                                  decision="allowed", requirements_version="R1")
    job, _ = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k")
    ran = []
    monkeypatch.setattr(worker, "run_job", lambda jid, r, s, c: ran.append(jid))
    assert worker.tick(repo, settings=None, coolify=None, lease_seconds=60) is True
    assert ran == [job.job_id]
    assert worker.tick(repo, settings=None, coolify=None, lease_seconds=60) is False
```

- [x] **Step 2: `mcp/app.py`**

```python
import contextlib
import hmac

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from sandbox_mcp.db.models import Base
from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.session import make_engine, make_session_factory
from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.settings import Settings


class GatewayTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        ok = auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], self._token)
        if not ok:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app(settings: Settings) -> Starlette:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    repo = Repository(make_session_factory(engine))
    mcp = build_mcp(repo, settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    async def healthz(_: Request):
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[Route("/healthz", healthz),
                Mount("/", app=mcp.streamable_http_app(json_response=True, stateless_http=True,
                                                       host="0.0.0.0"))],
        middleware=[Middleware(GatewayTokenMiddleware, token=settings.gateway_token)],
        lifespan=lifespan,
    )
```

Note: `host` in `streamable_http_app` configures DNS-rebinding protection (allowed Host header). If LiteLLM calls the service as `sandbox-mcp:8080`, pass `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)` instead (import from `mcp.server.transport_security`). Decide during Task 11 integration; start with protection disabled behind the token.

- [x] **Step 3: `worker.py`**

```python
import logging
import time

from sandbox_mcp.db.models import Base
from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.session import make_engine, make_session_factory
from sandbox_mcp.services.coolify import CoolifyError, make_coolify_client
from sandbox_mcp.services.pipeline import run_job
from sandbox_mcp.settings import Settings

log = logging.getLogger(__name__)


def tick(repo: Repository, settings, coolify, lease_seconds: int) -> bool:
    job = repo.claim_job(lease_seconds)
    if job is None:
        return False
    log.info("job %s claimed", job.job_id)
    run_job(job.job_id, repo, settings, coolify)
    return True


def sweep_expired(repo: Repository, coolify) -> int:
    n = 0
    for job in repo.expired_jobs():
        app = repo.get_application(job.app_id)
        try:
            if app and app.coolify_uuid:
                coolify.delete_application(app.coolify_uuid)
                repo.set_coolify_uuid(app.app_id, None)
            repo.update_job(job.job_id, state="expired", url=None)
            n += 1
        except CoolifyError as e:
            log.warning("expire %s failed: %s", job.job_id, e)
    return n


def run_worker(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    repo = Repository(make_session_factory(engine))
    coolify = make_coolify_client(settings)
    last_sweep = 0.0
    while True:
        try:
            if time.time() - last_sweep > 3600:
                sweep_expired(repo, coolify)
                last_sweep = time.time()
            if not tick(repo, settings, coolify, settings.lease_seconds):
                time.sleep(3)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            time.sleep(5)
```

- [x] **Step 4: `cli.py`**

```python
import argparse
import logging

import uvicorn

from sandbox_mcp.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser("sandbox-mcp")
    p.add_argument("command", choices=["serve", "worker"])
    args = p.parse_args()
    settings = Settings()
    if args.command == "serve":
        from sandbox_mcp.mcp.app import build_app
        uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")
    else:
        from sandbox_mcp.worker import run_worker
        run_worker(settings)


if __name__ == "__main__":
    main()
```

- [x] **Step 5: run + lint + commit** → `make test && make lint`; `git add -A && git commit -m "feat: starlette app with gateway token, worker loop, cli"`.

---

### Task 10: Dockerfile and compose (LiteLLM, Postgres, Caddy)

**Files:** Create `Dockerfile`, `deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/litellm/config.yaml`, `deploy/postgres/init.sql`, `deploy/.env.example`.

- [x] **Step 1: `Dockerfile`**

```dockerfile
FROM python:3.13-slim
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.58.1
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
 && curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz | tar -xz -C /usr/local/bin gitleaks \
 && curl -sSL https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz | tar -xz -C /usr/local/bin trivy \
 && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -m -u 10001 sandbox && mkdir -p /work && chown sandbox /work
USER sandbox
ENV SANDBOX_WORKDIR=/work
EXPOSE 8080
ENTRYPOINT ["sandbox-mcp"]
CMD ["serve"]
```

- [x] **Step 2: `deploy/compose.yaml`**

```yaml
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    environment: { MCP_DOMAIN: "${MCP_DOMAIN}" }
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [litellm]

  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: sandbox
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
      POSTGRES_DB: sandbox
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sandbox"]
      interval: 5s
      retries: 20

  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    restart: unless-stopped
    command: ["--config", "/config.yaml", "--port", "4000"]
    environment:
      LITELLM_MASTER_KEY: "${LITELLM_MASTER_KEY}"
      LITELLM_SALT_KEY: "${LITELLM_SALT_KEY}"
      DATABASE_URL: "postgresql://sandbox:${POSTGRES_PASSWORD}@postgres:5432/litellm"
      SANDBOX_GATEWAY_TOKEN: "${SANDBOX_GATEWAY_TOKEN}"
      STORE_MODEL_IN_DB: "True"
    volumes:
      - ./litellm/config.yaml:/config.yaml:ro
    depends_on:
      postgres: { condition: service_healthy }
    expose: ["4000"]

  sandbox-mcp:
    build: { context: .., dockerfile: Dockerfile }
    image: sandbox-mcp:local
    restart: unless-stopped
    command: ["serve"]
    env_file: .env
    environment:
      SANDBOX_DATABASE_URL: "postgresql+psycopg://sandbox:${POSTGRES_PASSWORD}@postgres:5432/sandbox"
    depends_on:
      postgres: { condition: service_healthy }
    expose: ["8080"]

  sandbox-worker:
    image: sandbox-mcp:local
    restart: unless-stopped
    command: ["worker"]
    env_file: .env
    environment:
      SANDBOX_DATABASE_URL: "postgresql+psycopg://sandbox:${POSTGRES_PASSWORD}@postgres:5432/sandbox"
    depends_on: [sandbox-mcp]

volumes:
  caddy_data: {}
  pg_data: {}
```

- [x] **Step 3: `deploy/Caddyfile`, `deploy/postgres/init.sql`, `deploy/litellm/config.yaml`, `deploy/.env.example`**

Caddyfile:
```
{$MCP_DOMAIN} {
    reverse_proxy litellm:4000
}
```

init.sql:
```sql
CREATE DATABASE litellm OWNER sandbox;
```

litellm/config.yaml:
```yaml
model_list: []
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
mcp_servers:
  sandbox_mcp:
    url: "http://sandbox-mcp:8080/mcp"
    transport: "http"
    description: "BI Sandbox: assess, validate_and_deploy, status"
    auth_type: "bearer_token"
    auth_value: os.environ/SANDBOX_GATEWAY_TOKEN
```

.env.example (deploy):
```
MCP_DOMAIN=mcp.1-2-3-4.sslip.io
POSTGRES_PASSWORD=change-me
LITELLM_MASTER_KEY=sk-change-me
LITELLM_SALT_KEY=sk-salt-change-me
SANDBOX_GATEWAY_TOKEN=change-me-long-random
SANDBOX_COOLIFY_FAKE=true
SANDBOX_COOLIFY_URL=https://coolify.example
SANDBOX_COOLIFY_TOKEN=
SANDBOX_COOLIFY_PROJECT_UUID=
SANDBOX_COOLIFY_SERVER_UUID=
SANDBOX_COOLIFY_ENVIRONMENT_NAME=production
SANDBOX_APPS_BASE_DOMAIN=apps.example
SANDBOX_TTL_DAYS=14
```

- [x] **Step 4: build and run locally**

```bash
cp deploy/.env.example deploy/.env   # then set real random values
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build postgres litellm sandbox-mcp sandbox-worker
curl -s localhost:4000/health/liveliness   # litellm alive (expose→ add temporary ports: ["4000:4000"] for local)
```
Expected: all four containers `Up`; `docker compose logs sandbox-mcp` shows uvicorn on 8080.

- [x] **Step 5: commit** → `git add -A && git commit -m "build: dockerfile and compose with litellm, postgres, caddy"`.

---

### Task 11: Local integration through LiteLLM + Claude Code

- [x] **Step 1: create a virtual key** (`infra/litellm-key.sh`)

```bash
#!/usr/bin/env bash
# usage: infra/litellm-key.sh <base_url> <master_key> <user_alias>
set -euo pipefail
curl -sS -X POST "$1/key/generate" -H "Authorization: Bearer $2" -H "Content-Type: application/json" \
  -d "{\"key_alias\": \"$3\", \"mcp_servers\": [\"sandbox_mcp\"], \"metadata\": {\"user\": \"$3\"}}"
```

- [x] **Step 2: verify tools over the gateway with the SDK client**

```bash
.venv/bin/python - <<'EOF'
import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
async def main():
    async with Client("http://localhost:4000/sandbox_mcp/mcp",
                      ) as c:  # if headers needed, see docs: Client(streamable_http_client(url, headers=...))
        print([t.name for t in (await c.list_tools()).tools])
anyio.run(main)
EOF
```
Expected: `['assess_application', 'validate_and_deploy', 'status']` (names may be prefixed `sandbox_mcp-` on the aggregate `/mcp` endpoint; on `/sandbox_mcp/mcp` they are plain).

- [x] **Step 3: connect Claude Code**

```bash
claude mcp add --transport http sandbox http://localhost:4000/sandbox_mcp/mcp \
  --header "x-litellm-api-key: Bearer sk-<virtual key>"
claude mcp list
```
In Claude Code: ask to assess `https://github.com/<public static repo>` → `validate_and_deploy` → `status` shows `succeeded` with fake URL.

- [x] **Step 4: record what LiteLLM forwards** — in `sandbox-mcp` logs print request headers once (temporary debug) to learn which identity headers arrive; update `ACTOR_HEADERS` in `mcp/server.py` accordingly; remove debug. Commit: `fix: actor headers as forwarded by LiteLLM`.

---

### Task 12: GCP VM and deploy scripts

**Files:** Create `infra/create-vm.sh`, `infra/deploy.sh`; update `README.md`.

- [x] **Step 1: `infra/create-vm.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT=project-c6b97dc5-607e-455a-910
ACCOUNT=temirbulat_r@bi.group
ZONE=europe-west3-a
NAME=sandbox-mcp
G="gcloud --project $PROJECT --account $ACCOUNT"
$G services enable compute.googleapis.com iap.googleapis.com
$G compute firewall-rules describe allow-https-mcp >/dev/null 2>&1 || \
  $G compute firewall-rules create allow-https-mcp --allow tcp:80,tcp:443 --target-tags mcp
$G compute firewall-rules describe allow-iap-ssh >/dev/null 2>&1 || \
  $G compute firewall-rules create allow-iap-ssh --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags mcp
$G compute instances describe $NAME --zone $ZONE >/dev/null 2>&1 || \
  $G compute instances create $NAME --zone $ZONE --machine-type e2-medium \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 30GB --tags mcp \
    --metadata startup-script='#!/bin/bash
set -e
if ! command -v docker >/dev/null; then
  apt-get update && apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi'
IP=$($G compute instances describe $NAME --zone $ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "VM $NAME ready. IP=$IP  MCP_DOMAIN=mcp.${IP//./-}.sslip.io"
```

- [x] **Step 2: `infra/deploy.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT=project-c6b97dc5-607e-455a-910
ACCOUNT=temirbulat_r@bi.group
ZONE=europe-west3-a
NAME=sandbox-mcp
G="gcloud --project $PROJECT --account $ACCOUNT"
cd "$(dirname "$0")/.."
tar czf /tmp/sandbox-mcp.tgz --exclude .venv --exclude .git --exclude '__pycache__' \
  Dockerfile pyproject.toml src deploy
$G compute scp /tmp/sandbox-mcp.tgz $NAME:/tmp/ --zone $ZONE --tunnel-through-iap
$G compute ssh $NAME --zone $ZONE --tunnel-through-iap --command \
  'sudo mkdir -p /opt/sandbox-mcp && sudo tar xzf /tmp/sandbox-mcp.tgz -C /opt/sandbox-mcp && cd /opt/sandbox-mcp && sudo docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build'
```
`deploy/.env` is created on the VM once by hand (or scp'd from `.secrets/`), never committed.

- [x] **Step 3: run create-vm, write `.secrets/deploy.env`, scp it to `/opt/sandbox-mcp/deploy/.env`, run deploy.sh.** Verify: `curl https://mcp.<ip>.sslip.io/health/liveliness` → LiteLLM alive; `claude mcp add` with the public URL; tools listed.

- [x] **Step 4: README.md** describing: purpose, architecture picture (text), how to run tests, local compose, VM deploy, Claude Code connection, env variables, and what is fake until Coolify exists. Commit: `docs: README and infra scripts`.

---

### Task 13: Real Coolify (when the Codex stand is available)

- [ ] Fill `SANDBOX_COOLIFY_URL/TOKEN/PROJECT_UUID/SERVER_UUID`, `SANDBOX_APPS_BASE_DOMAIN` (wildcard `*.<coolify-ip>.sslip.io` or a real domain), set `SANDBOX_COOLIFY_FAKE=false`, redeploy.
- [ ] E2E: public static repo → `assess_application` → `validate_and_deploy` → `status` reaches `succeeded` with a working URL; `blocked` path verified with a repo containing a fake AWS key in a file.
- [ ] Fix field names against the real Coolify API response (`deployments[0].deployment_uuid`, `fqdn`) if they differ; add a regression test in `tests/test_coolify.py`.

---

## Self-review

- Spec coverage: tools (T8), rules incl. BDD 04/10/11 (T4, T8), pipeline stages and blocked/failed distinction (T7), TTL sweep (T9), storage (T2), config (T1), infra/compose/LiteLLM/Caddy (T10, T12), Claude Code connection (T11), testing (every task + T11/T13). Identity headers: open point handled in T11 step 4.
- Types: `CheckResult.as_dict()` used in pipeline; `Repository` method names consistent (`get_or_create_job`, `claim_job`, `update_job`, `set_coolify_uuid`, `latest_job_for_app`, `expired_jobs`); `AppSpec` fields match between coolify.py and pipeline.py; `Settings` fields used (`workdir`, `apps_base_domain`, `deploy_timeout_seconds`, `lease_seconds`, `ttl_days`).
- Known risk: `streamable_http_app(host=...)` DNS-rebinding setting — resolved in T9 note / T11.


## Status 19.10.2026

Tasks 1–12 done and verified on VM `sandbox-mcp` (35.234.71.242): e2e via LiteLLM gateway reaches `succeeded` with fake Coolify; owner resolved via sandbox token. Deviation from plan: identity is a separate `x-sandbox-token` (LiteLLM scrubs its own keys from forwarded headers), not LiteLLM headers. Task 13 (real Coolify) pending the Codex stand.
