"""Safe, allow-listed fault injection for the versioned flagd configuration."""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oncall_contracts.faults import FaultAction, FaultSpec


class FaultInjectionError(RuntimeError):
    """A fault could not be activated or safely cleaned up."""


@dataclass(frozen=True)
class ActiveFault:
    """The small piece of runtime state an evaluation may expose in logs."""

    fault_id: str
    flag: str
    disabled_variant: str
    enabled_variant: str


class FlagdFaultInjector:
    """Temporarily change one existing flagd default variant.

    The injector deliberately supports only ``set_flag``. It never creates a
    flag or variant, refuses to start over a non-baseline value, and restores
    the disabled value in ``finally``. If another process changes unrelated
    flags during the run, those changes are retained during cleanup.
    """

    def __init__(self, config_path: Path):
        self.config_path = config_path

    @contextmanager
    def injected(self, fault: FaultSpec) -> Iterator[ActiveFault]:
        """Enable ``fault`` for the body and disable it on every exit path."""
        original_bytes, original = self._read()
        enable_flag, enable_variant = self._resolve_action(fault["enable"], original)
        disable_flag, disable_variant = self._resolve_action(fault["disable"], original)
        if enable_flag != disable_flag:
            raise FaultInjectionError("enable and disable actions must target the same flag")
        if enable_variant == disable_variant:
            raise FaultInjectionError("enable and disable variants must differ")

        current = original["flags"][enable_flag]["defaultVariant"]
        if current != disable_variant:
            raise FaultInjectionError(
                f"refusing to enable {enable_flag!r}: expected baseline "
                f"{disable_variant!r}, found {current!r}"
            )

        active = copy.deepcopy(original)
        active["flags"][enable_flag]["defaultVariant"] = enable_variant
        self._write_document(active)
        state = ActiveFault(
            fault_id=fault["fault_id"],
            flag=enable_flag,
            disabled_variant=disable_variant,
            enabled_variant=enable_variant,
        )
        try:
            yield state
        finally:
            self._restore(
                flag=enable_flag,
                disabled_variant=disable_variant,
                original_bytes=original_bytes,
                active=active,
            )

    def current_variant(self, flag: str) -> str:
        """Return the configured default variant for one existing flag."""
        _, document = self._read()
        flags = document["flags"]
        if flag not in flags:
            raise FaultInjectionError(f"unknown flag {flag!r}")
        variant = flags[flag].get("defaultVariant")
        if not isinstance(variant, str):
            raise FaultInjectionError(f"flag {flag!r} has no string defaultVariant")
        return variant

    def _restore(
        self,
        *,
        flag: str,
        disabled_variant: str,
        original_bytes: bytes,
        active: dict[str, Any],
    ) -> None:
        try:
            _, current = self._read()
        except FaultInjectionError:
            # A corrupt runtime file is more dangerous than losing a concurrent
            # invalid edit. Restore the last known-good baseline exactly.
            self._write_bytes(original_bytes)
            return

        if current == active:
            self._write_bytes(original_bytes)
            return

        flags = current["flags"]
        if flag not in flags or not isinstance(flags[flag], dict):
            raise FaultInjectionError(
                f"cannot clean up {flag!r}: the flag was removed during evaluation"
            )
        variants = flags[flag].get("variants")
        if not isinstance(variants, Mapping) or disabled_variant not in variants:
            raise FaultInjectionError(
                f"cannot clean up {flag!r}: variant {disabled_variant!r} was removed"
            )
        flags[flag]["defaultVariant"] = disabled_variant
        self._write_document(current)

    def _resolve_action(self, action: FaultAction, document: dict[str, Any]) -> tuple[str, str]:
        if action.get("action") != "set_flag":
            raise FaultInjectionError(f"unsupported fault action {action.get('action')!r}")
        args = action.get("args")
        if not isinstance(args, Mapping) or set(args) != {"flag", "variant"}:
            raise FaultInjectionError("set_flag args must contain exactly 'flag' and 'variant'")
        flag = args.get("flag")
        variant = args.get("variant")
        if not isinstance(flag, str) or not flag:
            raise FaultInjectionError("set_flag flag must be a non-empty string")
        if not isinstance(variant, str) or not variant:
            raise FaultInjectionError("set_flag variant must be a non-empty string")

        flags = document["flags"]
        definition = flags.get(flag)
        if not isinstance(definition, Mapping):
            raise FaultInjectionError(f"unknown flag {flag!r}")
        variants = definition.get("variants")
        if not isinstance(variants, Mapping) or variant not in variants:
            raise FaultInjectionError(f"unknown variant {variant!r} for flag {flag!r}")
        if not isinstance(definition.get("defaultVariant"), str):
            raise FaultInjectionError(f"flag {flag!r} has no string defaultVariant")
        return flag, variant

    def _read(self) -> tuple[bytes, dict[str, Any]]:
        try:
            raw = self.config_path.read_bytes()
        except OSError as exc:
            raise FaultInjectionError(
                f"cannot read flagd config {self.config_path}: {exc}"
            ) from exc
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FaultInjectionError(f"invalid flagd JSON in {self.config_path}: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("flags"), dict):
            raise FaultInjectionError("flagd config must contain a 'flags' object")
        return raw, document

    def _write_document(self, document: dict[str, Any]) -> None:
        raw = (json.dumps(document, indent=2) + "\n").encode()
        self._write_bytes(raw)

    def _write_bytes(self, raw: bytes) -> None:
        """Atomically replace the config so flagd never observes partial JSON."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = stat.S_IMODE(self.config_path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
        except OSError as exc:
            raise FaultInjectionError(
                f"cannot stat flagd config {self.config_path}: {exc}"
            ) from exc
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.config_path.parent,
                prefix=f".{self.config_path.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(mode)
            os.replace(temporary, self.config_path)
        except OSError as exc:
            raise FaultInjectionError(
                f"cannot write flagd config {self.config_path}: {exc}"
            ) from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
