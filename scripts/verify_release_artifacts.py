"""Проверяет, что release-архивы не содержат локальное окружение и state."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path


FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    ".verify-venv",
    ".release-venv",
    ".pytest_cache",
    "__pycache__",
}
ALLOWED_SDIST_ROOT_ITEMS = {
    ".gitignore",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "PKG-INFO",
    "plugin",
    "scripts",
    "src",
    "tests",
}


def reject_forbidden(name: str) -> None:
    parts = set(Path(name).parts)
    lowered = name.lower()
    if parts & FORBIDDEN_PARTS:
        raise AssertionError(f"Запрещённый каталог в release artifact: {name}")
    if any(
        marker in lowered
        for marker in (".sqlite3", ".env", "auth.json", "credentials")
    ):
        raise AssertionError(f"Запрещённый state/credential path: {name}")


def verify_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
    if not members:
        raise AssertionError("Пустой sdist")
    roots = {member.name.split("/", 1)[0] for member in members}
    if len(roots) != 1:
        raise AssertionError(f"Неожиданные корни sdist: {sorted(roots)}")
    root = next(iter(roots))
    for member in members:
        reject_forbidden(member.name)
        relative = member.name.removeprefix(root + "/")
        if not relative:
            continue
        item = relative.split("/", 1)[0]
        if item not in ALLOWED_SDIST_ROOT_ITEMS:
            raise AssertionError(f"Файл вне sdist allowlist: {member.name}")


def verify_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        bad_crc = archive.testzip()
        names = archive.namelist()
    if bad_crc is not None:
        raise AssertionError(f"Wheel CRC failure: {bad_crc}")
    for name in names:
        reject_forbidden(name)


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(dist.glob("hermes_context_optimizer-*.whl"))
    sdists = sorted(dist.glob("hermes_context_optimizer-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AssertionError(
            f"Ожидался один wheel и один sdist, получено {wheels=} {sdists=}"
        )
    verify_wheel(wheels[0])
    verify_sdist(sdists[0])
    print(f"RELEASE_ARTIFACTS=PASS wheel={wheels[0].name} sdist={sdists[0].name}")


if __name__ == "__main__":
    main()
