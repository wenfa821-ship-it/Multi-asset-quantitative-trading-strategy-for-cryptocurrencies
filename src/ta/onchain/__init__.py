"""On-chain layer: safety screening, market data, Jupiter routing, gated signing.

This package does not import ta.risk (the risk engine is injected instead), which
keeps the import graph acyclic: ta.risk.engine -> ta.onchain (for TokenSnapshot).
"""

from .executor import OnchainExecutor
from .http import HttpClient, HttpError, JsonFetcher, urllib_fetcher
from .jupiter import JupiterError, JupiterRouter
from .models import (
    SAFETY_CRITICAL_FIELDS,
    ExecutionResult,
    Quote,
    ScreenResult,
    SimulationResult,
    SwapPlan,
    TokenSnapshot,
)
from .providers import (
    CompositeTokenService,
    DexScreenerProvider,
    FixtureFetcher,
    FixtureProvider,
    GMGNProvider,
    GoPlusProvider,
    Provider,
    ProviderError,
    RugCheckProvider,
)
from .rpc import RpcError, SolanaRpc
from .screener import TokenScreener, load_snapshots
from .signers import (
    HardwareWalletSigner,
    KmsSigner,
    LocalKeypairSigner,
    MockSigner,
    RefuseSigner,
    SignedTransaction,
    Signer,
    SignerRefused,
    base58_encode,
    build_signer,
)

__all__ = [
    "ExecutionResult",
    "Quote",
    "SAFETY_CRITICAL_FIELDS",
    "ScreenResult",
    "SimulationResult",
    "SwapPlan",
    "TokenSnapshot",
    "TokenScreener",
    "load_snapshots",
    "HttpClient",
    "HttpError",
    "JsonFetcher",
    "urllib_fetcher",
    "Provider",
    "ProviderError",
    "DexScreenerProvider",
    "GoPlusProvider",
    "RugCheckProvider",
    "GMGNProvider",
    "FixtureProvider",
    "FixtureFetcher",
    "CompositeTokenService",
    "JupiterRouter",
    "JupiterError",
    "SolanaRpc",
    "RpcError",
    "Signer",
    "SignerRefused",
    "SignedTransaction",
    "RefuseSigner",
    "MockSigner",
    "LocalKeypairSigner",
    "KmsSigner",
    "HardwareWalletSigner",
    "build_signer",
    "base58_encode",
    "OnchainExecutor",
]

