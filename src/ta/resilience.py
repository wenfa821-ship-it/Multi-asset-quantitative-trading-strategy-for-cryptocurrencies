"""Venue call resilience: bounded retries, degraded mode, tick isolation.

A 24/7 loop has to survive the network. A transient failure must never:

  * kill the process,
  * let the bot act on stale data while pretending it is fresh,
  * mask the original error behind a second failure inside the shutdown path.

Retrying a *write* is safe here only because every order carries a client order
id: if the venue received the first attempt, the retry is rejected as a
duplicate instead of filling twice.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from .models import AccountState, Bar, Fill, Order, OrderRequest
from .venues.base import Venue

T = TypeVar("T")


class VenueDegraded(RuntimeError):
    """A venue call failed after every retry: stop trusting fresh data."""


@dataclass(slots=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25

    def delay_for(self, attempt: int) -> float:
        raw = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return max(0.0, raw * (1.0 + random.uniform(-self.jitter, self.jitter)))


READ_POLICY = RetryPolicy(attempts=3, base_delay=0.5, max_delay=8.0)
WRITE_POLICY = RetryPolicy(attempts=2, base_delay=0.4, max_delay=4.0)


def retry_call(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    label: str = "call",
    on_error: Callable[[int, Exception], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberate: any transport failure is retryable
            last = exc
            if on_error is not None:
                on_error(attempt, exc)
            if attempt < policy.attempts:
                sleep(policy.delay_for(attempt))
    raise VenueDegraded(f"{label} failed after {policy.attempts} attempt(s): {last}") from last


class ResilientVenue(Venue):
    """Delegate to a real venue, but survive transient failures.

    Reads retry three times, writes twice. When everything fails we raise
    VenueDegraded instead of returning half-true data, and the runner decides
    what to do (stop opening risk, keep the process alive, alert).
    """

    def __init__(
        self,
        inner: Venue,
        read_policy: RetryPolicy | None = None,
        write_policy: RetryPolicy | None = None,
        logger: logging.Logger | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.read_policy = read_policy or READ_POLICY
        self.write_policy = write_policy or WRITE_POLICY
        self.log = logger
        self._sleep = sleep
        self.reads = 0
        self.writes = 0
        self.retries = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.degraded = False
        self.last_error = ""
        self.last_good_account: AccountState | None = None

    # ---- identity / pass-through -------------------------------------------
    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def simulated(self) -> bool:
        return self.inner.simulated

    @property
    def base_ccy(self) -> str:
        return self.inner.base_ccy

    def __getattr__(self, item: str) -> Any:
        # non-interface helpers such as PaperVenue.set_mark
        return getattr(object.__getattribute__(self, "inner"), item)

    # ---- guarded calls -----------------------------------------------------
    def _guarded(self, label: str, fn: Callable[[], T], policy: RetryPolicy, kind: str) -> T:
        def on_error(attempt: int, exc: Exception) -> None:
            self.retries += 1
            if self.log is not None:
                self.log.warning(
                    "venue call failed, retrying",
                    extra={"extra_fields": {
                        "call": label, "attempt": attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                    }},
                )

        try:
            result = retry_call(fn, policy, label=label, on_error=on_error, sleep=self._sleep)
        except VenueDegraded as exc:
            self.failures += 1
            self.consecutive_failures += 1
            self.degraded = True
            self.last_error = str(exc)
            if self.log is not None:
                self.log.error(
                    "venue degraded",
                    extra={"extra_fields": {
                        "call": label,
                        "consecutive_failures": self.consecutive_failures,
                        "error": str(exc),
                    }},
                )
            raise
        self.consecutive_failures = 0
        self.degraded = False
        if kind == "read":
            self.reads += 1
        else:
            self.writes += 1
        return result

    def stats(self) -> dict[str, Any]:
        return {
            "reads": self.reads,
            "writes": self.writes,
            "retries": self.retries,
            "failures": self.failures,
            "degraded": self.degraded,
            "last_error": self.last_error,
        }

    # ---- Venue interface ---------------------------------------------------
    def connect(self) -> None:
        self._guarded("connect", self.inner.connect, self.read_policy, "read")

    def close(self) -> None:
        try:
            self.inner.close()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass

    def fetch_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        return self._guarded(
            f"fetch_bars({symbol})",
            lambda: self.inner.fetch_bars(symbol, timeframe, limit),
            self.read_policy,
            "read",
        )

    def refresh_marks(self, symbols: list[str]) -> dict[str, float]:
        return self._guarded(
            "refresh_marks",
            lambda: self.inner.refresh_marks(symbols),
            self.read_policy,
            "read",
        )

    def get_account(self) -> AccountState:
        account = self._guarded("get_account", self.inner.get_account, self.read_policy, "read")
        self.last_good_account = account
        return account

    def place_order(self, request: OrderRequest) -> Order:
        return self._guarded(
            f"place_order({request.symbol})",
            lambda: self.inner.place_order(request),
            self.write_policy,
            "write",
        )

    def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        return self._guarded(
            f"cancel_order({symbol})",
            lambda: self.inner.cancel_order(client_order_id, symbol),
            self.write_policy,
            "write",
        )

    def cancel_all(self, symbols: list[str]) -> int:
        return self._guarded(
            "cancel_all", lambda: self.inner.cancel_all(symbols), self.write_policy, "write"
        )

    def fetch_open_orders(self, symbols: list[str]) -> list[Order]:
        return self._guarded(
            "fetch_open_orders",
            lambda: self.inner.fetch_open_orders(symbols),
            self.read_policy,
            "read",
        )

    def drain_fills(self) -> list[Fill]:
        return self.inner.drain_fills()  # local buffer, no I/O

    def sync(self, symbols: list[str]) -> None:
        try:
            self._guarded("sync", lambda: self.inner.sync(symbols), self.read_policy, "read")
        except VenueDegraded:
            raise

    def min_notional(self, symbol: str) -> float:
        return self.inner.min_notional(symbol)

    def account_snapshot(self) -> dict[str, Any]:
        return self._guarded(
            "account_snapshot", self.inner.account_snapshot, self.read_policy, "read"
        )

