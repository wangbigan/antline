"""Git helpers for Antline projects."""

from __future__ import annotations

import subprocess
from pathlib import Path


def is_git_repo(path: Path | None = None) -> bool:
    cwd = path or Path.cwd()
    return (cwd / ".git").exists()


def _git_run(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True)
    except FileNotFoundError:
        return None


def git_init(path: Path | None = None) -> bool:
    cwd = path or Path.cwd()
    result = _git_run(["git", "init"], cwd)
    return result is not None and result.returncode == 0


def git_add_all(path: Path | None = None) -> None:
    cwd = path or Path.cwd()
    _git_run(["git", "add", "."], cwd)


def git_commit(message: str, path: Path | None = None) -> bool:
    cwd = path or Path.cwd()
    result = _git_run(["git", "commit", "-m", message], cwd)
    return result is not None and result.returncode == 0


def ensure_gitignore(path: Path | None = None) -> None:
    """Ensure .gitignore exists with sensible defaults."""
    cwd = path or Path.cwd()
    gitignore = cwd / ".gitignore"
    content = """# Antline — never commit sensitive connection passwords
sources/*/source.yml
requirements/*/requirement.yml
projects/*/.env

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/

# Reports (generated)
reports/*.html
reports/*.csv

# dbt
projects/*/dbt/target/
projects/*/dbt/dbt_packages/
projects/*/dbt/logs/
"""
    if not gitignore.exists():
        gitignore.write_text(content)
