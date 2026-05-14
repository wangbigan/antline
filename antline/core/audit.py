"""Audit logging for database operations.

All database operations are logged for compliance and traceability.
Passwords are never logged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _log_dir(root: Path) -> Path:
    path = root / ".antline" / "audit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_operation(
    root: Path,
    operation: str,
    user: str,
    target: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Log a database operation for audit purposes.

    Args:
        root: Workspace root directory.
        operation: Operation name (e.g. 'init', 'source_explore', 'scaffold_db_create').
        user: Database user performing the operation.
        target: Target host:port/database or description.
        details: Additional non-sensitive details.
    """
    log_path = _log_dir(root) / "audit.log"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "user": user,
        "target": target,
        "details": details or {},
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
