"""Windows user-only ACL hardening for HCO state and SQLite sidecars."""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path


def harden_private_path(path: str | Path) -> None:
    target = Path(path)
    if os.name == "nt":
        principal = getpass.getuser()
        permission = f"{principal}:(OI)(CI)F" if target.is_dir() else f"{principal}:F"
        completed = subprocess.run(
            ["icacls", str(target), "/inheritance:r", "/grant:r", permission],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout).decode("oem", errors="replace")
            raise PermissionError(f"Could not harden HCO ACL for {target}: {diagnostic}")
        return
    os.chmod(target, 0o700 if target.is_dir() else 0o600)


def acl_readback(path: str | Path) -> str:
    if os.name != "nt":
        return oct(Path(path).stat().st_mode & 0o777)
    completed = subprocess.run(["icacls", str(Path(path))], capture_output=True, check=True)
    return completed.stdout.decode("oem", errors="replace")


def harden_state_files(home: str | Path) -> list[Path]:
    root = Path(home)
    if not root.exists():
        return []
    paths = sorted(
        path for path in root.iterdir()
        if path.is_file() and (
            path.name.startswith("store.sqlite3")
            or path.name.startswith("telemetry.sqlite3")
        )
    )
    hardened: list[Path] = []
    for path in paths:
        try:
            harden_private_path(path)
        except (PermissionError, FileNotFoundError):
            # SQLite may remove transient -wal/-shm files between listing and
            # OS permission hardening. Windows commonly surfaces PermissionError;
            # POSIX chmod surfaces FileNotFoundError. Ignore only a path that
            # genuinely vanished; preserve fail-closed behavior if it still exists.
            if path.exists():
                raise
            continue
        hardened.append(path)
    return hardened
