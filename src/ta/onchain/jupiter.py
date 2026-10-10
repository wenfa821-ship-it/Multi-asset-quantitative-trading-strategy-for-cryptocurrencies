"""Jupiter swap routing: quote -> swap transaction. Signing lives in signers.py.

Endpoints come from config/jupiter so a version bump is a config change, not a
code change.
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..config import OnchainConfig
from .http import HttpClient
from .models import Quote, SwapPlan


class JupiterError(RuntimeError):
    pass


class JupiterRouter:
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    def __init__(self, config: OnchainConfig, client: HttpClient) -> None:
        self.config = config
        self.client = client

    # ---- quote -------------------------------------------------------------
    def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None = None,
    ) -> Quote:
        bps = int(slippage_bps or self.config.execution.slippage_bps)
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(int(amount_lamports)),
            "slippageBps": str(bps),
            "restrictIntermediateTokens": "true",
        }
        url = f"{self.config.jupiter.quote_endpoint}?{urlencode(params)}"
        data = self.client.get_json(url)
        out_amount = data.get("outAmount")
        if not out_amount:
            raise JupiterError(f"quote returned no outAmount: {data}")
        route = data.get("routePlan") or []
        labels = [
            str((step.get("swapInfo") or {}).get("label"))
            for step in route
            if isinstance(step, dict)
        ]
        return Quote(
            input_mint=str(data.get("inputMint") or input_mint),
            output_mint=str(data.get("outputMint") or output_mint),
            in_amount=int(data.get("inAmount") or amount_lamports),
            out_amount=int(out_amount),
            price_impact_pct=float(data.get("priceImpactPct") or 0.0) * 100.0,
            slippage_bps=int(data.get("slippageBps") or bps),
            other_amount_threshold=int(data.get("otherAmountThreshold") or 0),
            route_labels=labels,
            raw=data,
        )

    def check(self, quote: Quote) -> tuple[bool, str]:
        safety = self.config.safety
        if quote.price_impact_pct > safety.max_price_impact_pct:
            return False, (
                f"price impact {quote.price_impact_pct:.3f}% > {safety.max_price_impact_pct}%"
            )
        if quote.slippage_bps > safety.max_slippage_bps:
            return False, f"slippage {quote.slippage_bps}bps > {safety.max_slippage_bps}bps"
        if not quote.route_labels:
            return False, "no route returned"
        if quote.out_amount <= 0:
            return False, "zero output amount"
        return True, f"ok impact={quote.price_impact_pct:.3f}% route={','.join(quote.route_labels)}"

    # ---- build -------------------------------------------------------------
    def build_swap(
        self,
        quote: Quote,
        user_pubkey: str,
        *,
        wrap_sol: bool | None = None,
        priority_level: str | None = None,
        max_priority_fee_lamports: int | None = None,
    ) -> SwapPlan:
        cfg = self.config.jupiter
        quote_response = quote.raw or {
            "inputMint": quote.input_mint,
            "outputMint": quote.output_mint,
            "inAmount": str(quote.in_amount),
            "outAmount": str(quote.out_amount),
            "otherAmountThreshold": str(quote.other_amount_threshold),
            "slippageBps": quote.slippage_bps,
        }
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": cfg.wrapped_sol if wrap_sol is None else wrap_sol,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {
                "priorityLevelWithMaxLamports": {
                    "maxLamports": int(max_priority_fee_lamports or cfg.max_priority_fee_lamports),
                    "priorityLevel": priority_level or cfg.priority_level,
                }
            },
        }
        data = self.client.post_json(cfg.swap_endpoint, payload)
        transaction = data.get("swapTransaction") or ""
        if transaction and not isinstance(transaction, str):
            raise JupiterError("swapTransaction was not a base64 string")
        return SwapPlan(
            transaction_b64=transaction,
            last_valid_block_height=int(data.get("lastValidBlockHeight") or 0),
            priority_fee_lamports=int(data.get("prioritizationFeeLamports") or 0),
            simulated_placeholder=not transaction,
            raw=data,
        )

    def sign_and_send(self, *_: object, **__: object) -> None:
        raise NotImplementedError(
            "Signing is not part of the router. Use OnchainExecutor with an explicit "
            "Signer (see ta.onchain.signers) so that consent and simulation checks "
            "cannot be bypassed."
        )

