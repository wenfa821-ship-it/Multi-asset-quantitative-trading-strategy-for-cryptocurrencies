"""The on-chain execution pipeline, in a fixed and auditable order:

    snapshot -> screen -> risk gate -> quote -> build swap -> simulate -> sign -> broadcast

Every stage can stop the flow. The risk engine is injected (never imported) so the
exact same gate that guards spot trading also guards on-chain tokens.
"""

from __future__ import annotations

import base64
import logging
import uuid
from typing import Any

from ..config import OnchainConfig
from ..models import AccountState, OrderRequest, Position
from ..util import sanitize_id
from .jupiter import JupiterError, JupiterRouter
from .models import ExecutionResult, SimulationResult
from .providers import CompositeTokenService
from .rpc import RpcError, SolanaRpc
from .screener import TokenScreener
from .signers import Signer, SignerRefused


class OnchainExecutor:
    def __init__(
        self,
        config: OnchainConfig,
        service: CompositeTokenService,
        jupiter: JupiterRouter,
        signer: Signer,
        *,
        rpc: SolanaRpc | None = None,
        risk: Any = None,  # duck-typed RiskEngine, injected to keep imports acyclic
        ledger: Any = None,
        logger: logging.Logger | None = None,
        alerts: Any = None,
        rehearsal: bool = False,
        sol_price_usd: float | None = None,
    ) -> None:
        self.config = config
        self.service = service
        self.jupiter = jupiter
        self.signer = signer
        self.rpc = rpc
        self.risk = risk
        self.ledger = ledger
        self.log = logger
        self.alerts = alerts
        self.rehearsal = rehearsal
        self.sol_price_usd = float(sol_price_usd or config.sizing.sol_price_usd)
        self.screener = TokenScreener(config)

    # ---- helpers -----------------------------------------------------------
    def _record(self, kind: str, **payload: Any) -> None:
        if self.ledger is not None:
            self.ledger.record(kind, **payload)
        if self.log is not None:
            self.log.info(kind, extra={"extra_fields": payload})

    def _alert(self, level: str, title: str, **fields: Any) -> None:
        if self.alerts is not None:
            self.alerts.send(level, title, **fields)

    @staticmethod
    def _fail(stage: str, mint: str, reason: str, **extra: Any) -> ExecutionResult:
        return ExecutionResult(status="failed", stage=stage, mint=mint, reason=reason, **extra)

    def notional_usd(self, amount_lamports: int) -> float:
        return amount_lamports / 1_000_000_000 * self.sol_price_usd

    def _account(
        self, equity_usd: float, positions: dict[str, Position] | None = None
    ) -> AccountState:
        """On-chain the wallet is the account, so cash == equity for gate purposes."""
        return AccountState(
            venue="solana",
            base_ccy="USDC",
            cash=equity_usd,
            equity=equity_usd,
            positions=dict(positions or {}),
            day_start_equity=equity_usd,
            peak_equity=equity_usd,
        )

    # ---- stages 1-6: everything short of signing ---------------------------
    def prepare(
        self,
        mint: str,
        amount_lamports: int,
        *,
        input_mint: str | None = None,
        equity_usd: float | None = None,
        user_pubkey: str | None = None,
        open_positions: dict[str, Position] | None = None,
        chain: str = "solana",
    ) -> ExecutionResult:
        cfg = self.config
        input_mint = input_mint or JupiterRouter.SOL_MINT
        equity = float(equity_usd or cfg.sizing.equity_usd)

        if self.risk is None:
            return self._fail(
                "risk", mint, "no risk engine wired - refusing to touch on-chain funds"
            )
        if amount_lamports <= 0:
            return self._fail("input", mint, "amount must be > 0")

        # 1. snapshot: merge every configured source
        try:
            snapshot = self.service.snapshot(mint, chain=chain)
        except Exception as exc:  # noqa: BLE001 - total data failure is a hard stop
            self._record("onchain_snapshot_failed", mint=mint, error=str(exc))
            return self._fail("snapshot", mint, f"no token data: {exc}")
        self._record(
            "onchain_snapshot",
            mint=mint,
            provided=snapshot.provided,
            sources=snapshot.sources,
            notes=snapshot.notes,
        )

        # 2. safety filter
        screen = self.screener.screen(snapshot)
        self._record(
            "onchain_screen",
            mint=mint,
            passed=screen.passed,
            score=screen.score,
            reasons=screen.reasons,
        )
        if not screen.passed:
            self._alert("warning", "onchain token blocked", mint=mint, reasons=screen.reasons)
            return ExecutionResult(
                status="blocked",
                stage="screen",
                mint=mint,
                symbol=snapshot.symbol,
                reason="; ".join(screen.reasons),
                screen={"passed": screen.passed, "score": screen.score, "reasons": screen.reasons},
                details={"snapshot": snapshot.dict()},
            )

        # 3. sizing + the shared risk gate
        if not snapshot.has("price_usd") or snapshot.price_usd <= 0:
            return self._fail("sizing", mint, "token price unknown, cannot size a position")
        notional = self.notional_usd(amount_lamports)
        qty = notional / snapshot.price_usd
        request = OrderRequest(
            client_order_id=f"OC{sanitize_id(mint, 20)}{uuid.uuid4().hex[:8]}",
            symbol=mint,
            side="buy",
            qty=qty,
            type="market",
            tag="onchain",
        )
        account = self._account(equity, open_positions)
        decision = self.risk.pre_trade(
            request,
            account,
            snapshot.price_usd,
            {mint: snapshot.price_usd},
            token=snapshot,
            is_onchain=True,
        )
        self._record(
            "onchain_risk",
            mint=mint,
            approved=decision.approved,
            reason=decision.reason,
            qty=decision.qty,
            adjusted=decision.adjusted,
            notional_usd=round(notional, 4),
        )
        if decision.rejected:
            return ExecutionResult(
                status="rejected",
                stage="risk",
                mint=mint,
                symbol=snapshot.symbol,
                reason=f"risk gate: {decision.reason}",
                notional_usd=notional,
                screen={"passed": screen.passed, "score": screen.score, "reasons": screen.reasons},
            )
        # The quote must match what the risk gate approved, so re-derive the input
        # amount from the approved quantity BEFORE asking Jupiter for a route.
        planned_notional = notional
        if decision.adjusted:
            qty = decision.qty
            notional = qty * snapshot.price_usd
            shrunk_lamports = int(notional / self.sol_price_usd * 1_000_000_000)
            if shrunk_lamports <= 0:
                return self._fail(
                    "sizing",
                    mint,
                    f"risk gate shrank the order below 1 lamport "
                    f"(approved ${notional:.4f} at SOL=${self.sol_price_usd})",
                )
            amount_lamports = shrunk_lamports

        # 4. quote + price impact gate
        try:
            quote = self.jupiter.quote(
                input_mint, mint, amount_lamports, slippage_bps=cfg.execution.slippage_bps
            )
        except (JupiterError, Exception) as exc:  # noqa: BLE001
            self._record("onchain_quote_failed", mint=mint, error=str(exc))
            return self._fail("quote", mint, f"quote failed: {exc}", notional_usd=notional)

        ok, detail = self.jupiter.check(quote)
        self._record("onchain_quote", mint=mint, ok=ok, detail=detail, quote=quote.dict())
        if not ok:
            return ExecutionResult(
                status="rejected",
                stage="quote",
                mint=mint,
                symbol=snapshot.symbol,
                reason=f"quote rejected: {detail}",
                notional_usd=notional,
                quote=quote.dict(),
            )

        # 5. build the swap transaction
        try:
            plan = self.jupiter.build_swap(quote, user_pubkey or self.signer.pubkey)
        except Exception as exc:  # noqa: BLE001
            return self._fail("swap_build", mint, f"swap build failed: {exc}", quote=quote.dict())
        self._record("onchain_swap_built", mint=mint, plan=plan.dict())

        # 6. simulate - a swap that fails simulation is never signed
        if self.rpc is None:
            simulation = SimulationResult(ok=False, err="rpc_not_configured")
        elif not cfg.rpc.simulate:
            simulation = SimulationResult(ok=False, err="simulation_disabled_in_config")
        else:
            try:
                simulation = self.rpc.simulate_transaction(plan.transaction_b64)
            except RpcError as exc:
                simulation = SimulationResult(ok=False, err=str(exc))
        self._record("onchain_simulate", mint=mint, **simulation.dict())
        if not simulation.ok:
            return ExecutionResult(
                status="failed",
                stage="simulate",
                mint=mint,
                symbol=snapshot.symbol,
                reason=f"simulation failed: {simulation.err}",
                notional_usd=notional,
                amount_lamports=amount_lamports,
                quote=quote.dict(),
                plan=plan.dict(),
                simulation=simulation.dict(),
            )

        return ExecutionResult(
            status="simulated",
            stage="simulate",
            mint=mint,
            symbol=snapshot.symbol,
            reason="simulation passed",
            notional_usd=notional,
            amount_lamports=amount_lamports,
            screen={"passed": screen.passed, "score": screen.score, "reasons": screen.reasons},
            quote=quote.dict(),
            plan=plan.dict(),
            simulation=simulation.dict(),
            simulated=True,
            broadcast_allowed=False,
            details={
                "snapshot": snapshot.dict(),
                "risk": decision.reason,
                "qty": qty,
                "rehearsal": self.rehearsal,
                "placeholder_tx": plan.simulated_placeholder,
                "plan_b64": plan.transaction_b64,
                "planned_notional_usd": round(planned_notional, 4),
                "shrunk": bool(decision.adjusted),
                "approved_qty": qty,
            },
        )

    # ---- stages 7-8: signing and (maybe) broadcast -------------------------
    def execute(
        self,
        mint: str,
        amount_lamports: int,
        *,
        allow_signing: bool = False,
        allow_broadcast: bool = False,
        **kwargs: Any,
    ) -> ExecutionResult:
        result = self.prepare(mint, amount_lamports, **kwargs)
        if not result.ok:
            return result
        if not allow_signing:
            result.reason = "plan complete; signing not requested"
            return result

        cfg = self.config.execution
        if result.details.get("placeholder_tx"):
            result.status = "failed"
            result.stage = "sign"
            result.reason = "refusing to sign a placeholder transaction (no real swap was built)"
            return result

        transaction_bytes = base64.b64decode(result.details.get("plan_b64") or "")
        try:
            signed = self.signer.sign(transaction_bytes)
        except SignerRefused as exc:
            result.status = "failed"
            result.stage = "sign"
            result.reason = f"signer refused: {exc}"
            self._record("onchain_sign_refused", mint=mint, error=str(exc))
            return result

        result.signer = signed.signer
        result.signature = signed.signature
        result.simulated = signed.simulated
        result.broadcast_allowed = signed.broadcast_allowed
        self._record(
            "onchain_sign",
            mint=mint,
            signer=signed.signer,
            signature=signed.signature,
            simulated=signed.simulated,
        )

        if signed.simulated:
            result.status = "simulated"
            result.stage = "sign"
            result.reason = "mock signature produced - structurally realistic, never broadcastable"
            return result

        consents = {
            "cli_i_understand_risk": allow_broadcast,
            "config_allow_broadcast": cfg.allow_broadcast,
            "config_dry_run_disabled": not cfg.dry_run,
            "signer_allows_broadcast": signed.broadcast_allowed,
            "rpc_configured": self.rpc is not None,
        }
        if not all(consents.values()):
            result.status = "signed"
            result.stage = "sign"
            missing = ", ".join(name for name, granted in consents.items() if not granted)
            result.reason = f"signed but not broadcast (missing consent: {missing})"
            result.details["consents"] = consents
            return result

        try:
            signature = self.rpc.send_transaction(  # type: ignore[union-attr]
                signed.base64(), allow_broadcast=True
            )
        except RpcError as exc:
            result.status = "failed"
            result.stage = "broadcast"
            result.reason = f"broadcast failed: {exc}"
            return result

        result.status = "broadcast"
        result.stage = "broadcast"
        result.signature = signature
        result.broadcast_allowed = True
        result.reason = "broadcast to the cluster"
        self._record("onchain_broadcast", mint=mint, signature=signature)
        self._alert("critical", "on-chain broadcast", mint=mint, signature=signature)
        return result
