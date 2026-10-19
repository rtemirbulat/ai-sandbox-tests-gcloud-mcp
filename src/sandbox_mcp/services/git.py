import os
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
        raise GitError(
            "Разрешены только публичные репозитории вида https://github.com/<owner>/<repo>"
        )
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
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
