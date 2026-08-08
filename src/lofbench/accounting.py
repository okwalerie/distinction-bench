"""Atomic release-wide spend accounting independent of any model provider."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from lofbench.records import LedgerEvent

DEFAULT_GLOBAL_CAP_USD = 30.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SpendLedger:
    """One authoritative ledger shared by every run in a release cohort."""

    def __init__(
        self,
        path: Path,
        *,
        global_cap: float,
        cohort_caps: dict[str, float],
    ) -> None:
        self.path = path
        self.global_cap = global_cap
        self.cohort_caps = dict(cohort_caps)
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[IO[str]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                with self.path.open("a+") as ledger:
                    ledger.seek(0)
                    yield ledger
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _events(handle: IO[str]) -> list[LedgerEvent]:
        handle.seek(0)
        return [
            LedgerEvent.from_dict(json.loads(line))
            for line in handle.read().splitlines()
            if line.strip()
        ]

    @staticmethod
    def _append(handle: IO[str], event: LedgerEvent) -> None:
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        directory = os.open(Path(handle.name).parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _totals(events: list[LedgerEvent], cohort: str | None = None) -> tuple[float, float]:
        reservations: dict[str, float] = {}
        observed = 0.0
        for event in events:
            if cohort is not None and event.cohort != cohort:
                continue
            if event.event_type == "reserved":
                reservations[event.call_id] = event.amount_usd
            else:
                reservations.pop(event.call_id, None)
                observed += event.amount_usd
        return observed, sum(reservations.values())

    def totals(self, cohort: str | None = None) -> tuple[float, float]:
        with self._locked(exclusive=False) as handle:
            return self._totals(self._events(handle), cohort)

    def events_for_run(self, run_id: str) -> list[LedgerEvent]:
        with self._locked(exclusive=False) as handle:
            return [event for event in self._events(handle) if event.run_id == run_id]

    def reserve(
        self,
        *,
        call_id: str,
        trial_id: str,
        run_id: str,
        cohort: str,
        amount: float,
    ) -> None:
        if amount < 0:
            raise RuntimeError("spend reservation cannot be negative")
        if cohort not in self.cohort_caps:
            raise RuntimeError(f"unknown spend cohort {cohort!r}")
        with self._locked(exclusive=True) as handle:
            events = self._events(handle)
            if any(event.call_id == call_id for event in events):
                raise RuntimeError(f"call {call_id} already appears in the spend ledger")
            global_observed, global_reserved = self._totals(events)
            cohort_observed, cohort_reserved = self._totals(events, cohort)
            if global_observed + global_reserved + amount > self.global_cap + 1e-12:
                raise RuntimeError(
                    f"spend reservation would exceed ${self.global_cap:.2f} global cap"
                )
            cohort_cap = self.cohort_caps[cohort]
            if cohort_observed + cohort_reserved + amount > cohort_cap + 1e-12:
                raise RuntimeError(f"spend reservation would exceed ${cohort_cap:.2f} cohort cap")
            self._append(
                handle,
                LedgerEvent(
                    event_type="reserved",
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run_id,
                    cohort=cohort,
                    amount_usd=amount,
                    at=_now(),
                ),
            )

    def settle(
        self,
        *,
        call_id: str,
        trial_id: str,
        run_id: str,
        cohort: str,
        amount: float,
    ) -> None:
        if amount < 0:
            raise RuntimeError("observed cost cannot be negative")
        with self._locked(exclusive=True) as handle:
            events = self._events(handle)
            matching = [event for event in events if event.call_id == call_id]
            if len(matching) != 1 or matching[0].event_type != "reserved":
                raise RuntimeError(f"call {call_id} has no unique unsettled reservation")
            reserved = matching[0]
            if (reserved.trial_id, reserved.run_id, reserved.cohort) != (
                trial_id,
                run_id,
                cohort,
            ):
                raise RuntimeError("settlement identity does not match reservation")
            event = LedgerEvent(
                event_type="settled",
                call_id=call_id,
                trial_id=trial_id,
                run_id=run_id,
                cohort=cohort,
                amount_usd=amount,
                at=_now(),
            )
            self._append(handle, event)
            updated = [*events, event]
            global_observed, _reserved = self._totals(updated)
            cohort_observed, _cohort_reserved = self._totals(updated, cohort)
            if global_observed > self.global_cap + 1e-12:
                raise RuntimeError("observed spend exceeded the global cap")
            if cohort_observed > self.cohort_caps[cohort] + 1e-12:
                raise RuntimeError("observed spend exceeded the cohort cap")
