import subprocess

import pytest

from sandbox_mcp.db.models import Base
from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.session import make_engine, make_session_factory

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


@pytest.fixture
def repo(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.sqlite3")
    Base.metadata.create_all(engine)
    return Repository(make_session_factory(engine))


@pytest.fixture
def local_repo(tmp_path):
    """A local git repo with one commit containing index.html. Returns (path_str, sha)."""
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "index.html").write_text("<h1>hi</h1>")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(src), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        check=True,
        env={**GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    sha = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(src), sha


@pytest.fixture
def anyio_backend():
    return "asyncio"
