"""Создаёт переносимый SHA256SUMS с LF независимо от ОС."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    artifacts = sorted(dist.glob("hermes_context_optimizer-*.whl")) + sorted(
        dist.glob("hermes_context_optimizer-*.tar.gz")
    )
    if len(artifacts) != 2:
        raise AssertionError(f"Ожидалось два release artifacts, получено: {artifacts}")
    content = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in artifacts
    ).encode("ascii")
    if b"\r" in content:
        raise AssertionError("SHA256SUMS содержит CR")
    output = dist / "SHA256SUMS"
    output.write_bytes(content)
    print(f"SHA256SUMS=PASS path={output} bytes={len(content)}")


if __name__ == "__main__":
    main()
