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


def _run(args: list[str], env: dict | None = None) -> tuple[int, str] | None:
    if shutil.which(args[0]) is None:
        return None
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return p.returncode, (p.stdout + p.stderr)[-2000:]


def run_gitleaks(path: Path) -> CheckResult:
    report = path.parent / f"{path.name}.gitleaks.json"
    out = _run(
        [
            "gitleaks", "detect", "--no-git", "--source", str(path),
            "--report-format", "json", "--report-path", str(report),
            "--exit-code", "1", "--no-banner",
        ],
        env={"REPORT": str(report)},
    )  # fmt: skip
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
        return CheckResult(
            "gitleaks", "failed", f"Найдено секретов: {n}. Удалите их из репозитория"
        )
    return CheckResult("gitleaks", "error", f"gitleaks завершился с кодом {code}: {text[-300:]}")


def run_trivy(path: Path) -> CheckResult:
    out = _run(
        [
            "trivy", "fs", "--scanners", "vuln,misconfig", "--severity", "HIGH,CRITICAL",
            "--exit-code", "1", "--quiet", "--format", "table", str(path),
        ]
    )  # fmt: skip
    if out is None:
        return CheckResult("trivy", "error", "trivy не установлен")
    code, text = out
    if code == 0:
        return CheckResult("trivy", "passed", "Уязвимостей HIGH/CRITICAL не найдено")
    if code == 1 and "FATAL" not in text:
        return CheckResult("trivy", "failed", "Найдены уязвимости HIGH/CRITICAL:\n" + text[-1200:])
    return CheckResult("trivy", "error", f"trivy завершился с кодом {code}: {text[-300:]}")
