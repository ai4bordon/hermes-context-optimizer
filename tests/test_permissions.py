"""Windows user-only ACL hardening for HCO state."""

from __future__ import annotations

import getpass
import os

from hco.permissions import acl_readback, harden_private_path, harden_state_files


def test_harden_state_files_covers_sqlite_sidecars(tmp_path) -> None:
    home = tmp_path / "hco"
    home.mkdir()
    names = (
        "store.sqlite3",
        "store.sqlite3-wal",
        "store.sqlite3-shm",
        "telemetry.sqlite3",
        "telemetry.sqlite3-wal",
        "telemetry.sqlite3-shm",
    )
    for name in names:
        (home / name).write_bytes(b"state")

    hardened = harden_state_files(home)

    assert {path.name for path in hardened} == set(names)
    if os.name != "nt":
        assert all((path.stat().st_mode & 0o077) == 0 for path in hardened)


def test_harden_state_files_ignores_sidecar_that_vanishes_during_hardening(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "hco"
    home.mkdir()
    first = home / "store.sqlite3"
    vanished = home / "telemetry.sqlite3-shm"
    first.write_bytes(b"state")
    vanished.write_bytes(b"state")

    from hco import permissions

    original_harden = permissions.harden_private_path

    def delete_later_file(path):
        if path == first:
            vanished.unlink()
        original_harden(path)

    monkeypatch.setattr(permissions, "harden_private_path", delete_later_file)

    hardened = harden_state_files(home)

    assert hardened == [first]
