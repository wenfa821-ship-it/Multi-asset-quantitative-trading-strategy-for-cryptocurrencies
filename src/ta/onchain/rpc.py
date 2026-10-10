"""Minimal Solana JSON-RPC client: simulate, and (heavily gated) send."""

from __future__ import annotations

import os
import time
from typing import Any

from ..config import OnchainConfig
from .http import HttpClient
from .models import SimulationResult

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


class RpcError(RuntimeError):
    pass


class SolanaRpc:
    def __init__(self, url: str, client: HttpClient, commitment: str = "processed") -> None:
        self.url = url
        self.client = client
        self.commitment = commitment
        self._id = 0

    @classmethod
    def from_config(
        cls,
        config: OnchainConfig,
        client: HttpClient,
        env: dict[str, str] | None = None,
        *,
        fallback_url: str | None = None,
    ) -> "SolanaRpc | None":
        env = env if env is not None else os.environ
        url = env.get(config.rpc.url_env, "").strip() or (fallback_url or "")
        if not url:
            return None
        return cls(url, client, config.rpc.commitment)

    # ---- transport ---------------------------------------------------------
    def _call(self, method: str, params: list[Any]) -> dict[str, Any]:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        response = self.client.post_json(self.url, payload)
        if "error" in response:
            raise RpcError(f"{method} failed: {response['error']}")
        if "result" not in response:
            raise RpcError(f"{method} returned no result: {response}")
        return response["result"]

    # ---- reads -------------------------------------------------------------
    def get_latest_blockhash(self) -> dict[str, Any]:
        return self._call("getLatestBlockhash", [{"commitment": self.commitment}])

    def simulate_transaction(self, transaction_b64: str, *, replace_recent_blockhash: bool = True) -> SimulationResult:
        if not transaction_b64:
            return SimulationResult(ok=False, err="empty_transaction")
        result = self._call(
            "simulateTransaction",
            [
                transaction_b64,
                {
                    "encoding": "base64",
                    "commitment": self.commitment,
                    "replaceRecentBlockhash": replace_recent_blockhash,
                    "sigVerify": False,
                },
            ],
        )
        value = result.get("value") or {}
        err = value.get("err")
        return SimulationResult(
            ok=err is None,
            err=err,
            units_consumed=int(value.get("unitsConsumed") or 0),
            logs=list(value.get("logs") or []),
            raw=result,
        )

    # ---- writes (gated) ----------------------------------------------------
    def send_transaction(
        self,
        transaction_b64: str,
        *,
        allow_broadcast: bool = False,
        max_retries: int = 3,
    ) -> str:
        """Broadcast is refused unless the caller passes allow_broadcast=True.

        That flag is only ever set by OnchainExecutor after BOTH the config
        (execution.dry_run=false, execution.allow_broadcast=true) and the CLI
        (--i-understand-the-risk) consents are present.
        """
        if not allow_broadcast:
            raise RpcError("broadcast refused: allow_broadcast=False")
        if not transaction_b64:
            raise RpcError("broadcast refused: empty transaction")
        signature = self._call(
            "sendTransaction",
            [
                transaction_b64,
                {"encoding": "base64", "skipPreflight": False, "maxRetries": max_retries},
            ],
        )
        return str(signature)

    def confirm(self, signature: str, timeout: float = 30.0, poll: float = 1.5) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
            value = (status.get("value") or [None])[0]
            if value and (value.get("confirmationStatus") in ("confirmed", "finalized") or value.get("err")):
                return value
            time.sleep(poll)
        return {"timeout": True, "signature": signature}

