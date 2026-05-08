"""Configuration and state persistence."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from antline.core.models import DataSource, Project, Requirement

CONFIG_FILE = "antline.yml"
SOURCES_DIR = "sources"
REQUIREMENTS_DIR = "requirements"
PROJECTS_DIR = "projects"
REPORTS_DIR = "reports"


def _ensure_dirs(root: Path) -> None:
    for d in (SOURCES_DIR, REQUIREMENTS_DIR, PROJECTS_DIR, REPORTS_DIR):
        (root / d).mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def get_project_root() -> Path:
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / CONFIG_FILE).exists():
            return p
    raise RuntimeError(
        f"Not inside an Antline project (no {CONFIG_FILE} found). Run `antline init` first."
    )


def require_initialized() -> Path:
    root = get_project_root()
    _ensure_dirs(root)
    return root


class ProjectState:
    """Git-native state manager."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or require_initialized()
        _ensure_dirs(self.root)

    # --- DataSource ---

    def _source_path(self, source_id: str) -> Path:
        return self.root / SOURCES_DIR / source_id / "source.yml"

    def list_sources(self) -> list[DataSource]:
        src_dir = self.root / SOURCES_DIR
        sources: list[DataSource] = []
        for subdir in sorted(src_dir.iterdir()):
            if subdir.is_dir():
                f = subdir / "source.yml"
                data = load_yaml(f)
                if data:
                    sources.append(DataSource.model_validate(data))
        return sources

    def get_source(self, source_id: str) -> DataSource | None:
        path = self._source_path(source_id)
        data = load_yaml(path)
        return DataSource.model_validate(data) if data else None

    def save_source(self, source: DataSource) -> None:
        path = self._source_path(source.id)
        save_yaml(path, source.model_dump(mode="json"))

    def delete_source(self, source_id: str) -> None:
        path = self.root / SOURCES_DIR / source_id
        if path.exists():
            import shutil

            shutil.rmtree(path)

    # --- Requirement ---

    def _requirement_path(self, req_id: str) -> Path:
        return self.root / REQUIREMENTS_DIR / req_id / "requirement.yml"

    def list_requirements(self) -> list[Requirement]:
        req_dir = self.root / REQUIREMENTS_DIR
        reqs: list[Requirement] = []
        for subdir in sorted(req_dir.iterdir()):
            if subdir.is_dir():
                f = subdir / "requirement.yml"
                data = load_yaml(f)
                if data:
                    reqs.append(Requirement.model_validate(data))
        return reqs

    def get_requirement(self, req_id: str) -> Requirement | None:
        path = self._requirement_path(req_id)
        data = load_yaml(path)
        return Requirement.model_validate(data) if data else None

    def save_requirement(self, req: Requirement) -> None:
        path = self._requirement_path(req.id)
        save_yaml(path, req.model_dump(mode="json"))

    def delete_requirement(self, req_id: str) -> None:
        path = self.root / REQUIREMENTS_DIR / req_id
        if path.exists():
            import shutil

            shutil.rmtree(path)

    # --- Project ---

    def _project_path(self, prj_id: str) -> Path:
        return self.root / PROJECTS_DIR / prj_id / "project.yml"

    def list_projects(self) -> list[Project]:
        prj_dir = self.root / PROJECTS_DIR
        prjs: list[Project] = []
        for subdir in sorted(prj_dir.iterdir()):
            if subdir.is_dir():
                f = subdir / "project.yml"
                data = load_yaml(f)
                if data:
                    prjs.append(Project.model_validate(data))
        return prjs

    def get_project(self, prj_id: str) -> Project | None:
        path = self._project_path(prj_id)
        data = load_yaml(path)
        return Project.model_validate(data) if data else None

    def save_project(self, prj: Project) -> None:
        path = self._project_path(prj.id)
        save_yaml(path, prj.model_dump(mode="json"))

    def delete_project(self, prj_id: str) -> None:
        path = self.root / PROJECTS_DIR / prj_id
        if path.exists():
            import shutil

            shutil.rmtree(path)

    # --- Workspace Platform ---

    def workspace_platform(self) -> dict[str, Any] | None:
        """Read workspace platform config from antline.yml."""
        config_path = self.root / CONFIG_FILE
        config = load_yaml(config_path)
        if config and isinstance(config, dict):
            return config.get("platform")
        return None

    # --- Helpers ---

    def next_id(self, prefix: str, existing: list[str]) -> str:
        today = date.today().strftime("%Y%m%d")
        pattern = f"{prefix}-{today}-"
        nums = [
            int(x.replace(pattern, ""))
            for x in existing
            if x.startswith(pattern) and x.replace(pattern, "").isdigit()
        ]
        next_num = max(nums, default=0) + 1
        return f"{prefix}-{today}-{next_num:03d}"

    def next_source_id(self) -> str:
        return self.next_id("SRC", [s.id for s in self.list_sources()])

    def next_requirement_id(self) -> str:
        return self.next_id("REQ", [r.id for r in self.list_requirements()])

    def next_project_id(self) -> str:
        return self.next_id("PRJ", [p.id for p in self.list_projects()])
