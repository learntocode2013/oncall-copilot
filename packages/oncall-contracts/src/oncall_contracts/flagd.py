"""Change one flagd default variant and leave the rest of the document alone.

flagd reads ``deploy/flagd/demo.flagd.json`` and reloads it. It does not create
flags.

IN CONTRACTS, NOT IN EITHER CALLER. Two packages write this document with
opposite intent: `oncall-lab` sets a fault variant to INJECT a fault, and
`oncall_tools.servers.ops_actions` sets a flag to `off` to FIX one. The
dependency rules say nothing may import the lab -- it holds the fault
manifest's root cause -- so a shared writer cannot live there, and a second
copy in oncall-tools would mean a flagd schema change needs fixing twice.
Breaking exactly this kind of sharing cycle is why oncall-contracts exists.

Moved here from `oncall_lab.flags` on 2026-11-06. The API is unchanged; only
the import path moved. P4/P5 own the lab and should confirm the move.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


class FlagEditError(ValueError):
    """The requested flag or variant is not in the live document."""


class FlagFileError(RuntimeError):
    """The flag file could not be read or replaced."""


def read_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FlagFileError(f"cannot read flagd config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FlagFileError(f"invalid flagd JSON in {path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("flags"), dict):
        raise FlagFileError("flagd config must contain a flags object")
    return document


def current_variant(document: dict[str, Any], flag: str) -> str:
    definition = _definition(document, flag)
    variant = definition.get("defaultVariant")
    if not isinstance(variant, str):
        raise FlagEditError(f"flag {flag!r} has no string defaultVariant")
    return variant


def set_variant(document: dict[str, Any], flag: str, variant: str) -> tuple[dict[str, Any], bool]:
    """Return a new document and whether ``defaultVariant`` changed.

    An unchanged document is returned as-is so the caller can skip the write.
    """
    definition = _definition(document, flag)
    variants = definition.get("variants")
    if not isinstance(variants, dict) or variant not in variants:
        raise FlagEditError(f"unknown variant {variant!r} for flag {flag!r}")
    if definition.get("defaultVariant") == variant:
        return document, False
    updated = copy.deepcopy(document)
    updated["flags"][flag]["defaultVariant"] = variant
    return updated, True


def write_document(path: Path, document: dict[str, Any]) -> None:
    """Atomically replace the file so flagd never reads a partial document."""
    raw = (json.dumps(document, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    except OSError as exc:
        raise FlagFileError(f"cannot stat flagd config {path}: {exc}") from exc
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    except OSError as exc:
        raise FlagFileError(f"cannot write flagd config {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _definition(document: dict[str, Any], flag: str) -> dict[str, Any]:
    flags = document.get("flags")
    if not isinstance(flags, dict):
        raise FlagEditError("flagd config must contain a flags object")
    definition = flags.get(flag)
    if not isinstance(definition, dict):
        raise FlagEditError(f"unknown flag {flag!r}")
    return definition
