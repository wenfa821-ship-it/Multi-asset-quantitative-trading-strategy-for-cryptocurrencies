"""Transaction signing, behind explicit consent and a refusal-first default.

Consent chain (all of them must line up before a real broadcast):
    1. config: execution.signer is a real signer (not "refuse"/"mock")
    2. config: execution.dry_run = false AND execution.allow_broadcast = true
    3. CLI:    --i-understand-the-risk
    4. local keys additionally need TA_ALLOW_LOCAL_KEY=1 and file mode 600
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    out = ""
    while number > 0:
        number, rem = divmod(number, 58)
        out = _B58[rem] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + (out or "")


class SignerRefused(RuntimeError):
    """Raised instead of signing when no consent chain is complete."""


@dataclass(slots=True)
class SignedTransaction:
    signer: str
    pubkey: str
    raw: bytes
    simulated: bool = True
    broadcast_allowed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        """Display-only. For mock signatures this is a deterministic placeholder."""
        if self.simulated:
            return "SIMULATED-" + base58_encode(self.raw[:8])
        return base58_encode(self.raw[:64])

    def base64(self) -> str:
        import base64

        return base64.b64encode(self.raw).decode("ascii")


class Signer(ABC):
    name: str = "signer"
    simulated: bool = True
    broadcast_allowed: bool = False

    def __init__(self, pubkey: str = "") -> None:
        self.pubkey = pubkey or self._derive_pubkey()

    def _derive_pubkey(self) -> str:
        return base58_encode(hashlib.sha256(self.name.encode()).digest())

    @abstractmethod
    def sign(self, transaction_bytes: bytes) -> SignedTransaction: ...


class RefuseSigner(Signer):
    """Default. Never signs anything; it exists so misconfiguration fails loudly."""

    name = "refuse"
    simulated = True
    broadcast_allowed = False

    def sign(self, transaction_bytes: bytes) -> SignedTransaction:
        raise SignerRefused(
            "signer=refuse: this process is configured never to sign. Set "
            "onchain.execution.signer to mock (rehearsal) or a real signer backend."
        )


class MockSigner(Signer):
    """Produces a deterministic placeholder signature. Structurally realistic, useless
    on-chain, and impossible to broadcast - exactly what a rehearsal needs."""

    name = "mock"
    simulated = True
    broadcast_allowed = False

    def sign(self, transaction_bytes: bytes) -> SignedTransaction:
        if not transaction_bytes:
            raise SignerRefused("refusing to 'sign' an empty transaction")
        digest = hashlib.sha256(b"trading-agent-mock-signer" + transaction_bytes).digest()
        raw = digest * 2  # 64 bytes, shaped like an ed25519 signature
        return SignedTransaction(
            signer=self.name,
            pubkey=self.pubkey,
            raw=raw,
            simulated=True,
            broadcast_allowed=False,
            details={"note": "placeholder signature - cannot be broadcast, no key exists"},
        )


class LocalKeypairSigner(Signer):
    """Signs with a local solana-keygen JSON keypair. Off by default, for good reasons."""

    name = "local-keypair"
    simulated = False
    broadcast_allowed = True

    def __init__(self, keypair_path: str | Path, env: dict[str, str] | None = None) -> None:
        env = env if env is not None else os.environ
        if env.get("TA_ALLOW_LOCAL_KEY") != "1":
            raise SignerRefused(
                "refusing to load a local private key. Export TA_ALLOW_LOCAL_KEY=1 only "
                "after you have accepted that this process can move funds. Prefer a KMS "
                "or hardware signer for anything material."
            )
        path = Path(keypair_path)
        if not path.exists():
            raise SignerRefused(f"keypair file not found: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise SignerRefused(f"keypair file {path} is group/world readable (mode {oct(mode)}); chmod 600")
        try:
            import solders.keypair  # type: ignore
            import solders.transaction  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SignerRefused(
                'local signing needs solders: pip install "trading-agent[onchain]"'
            ) from exc

        self._solders = (solders.keypair, solders.transaction)
        secret = bytes(json.loads(path.read_text(encoding="utf-8")))
        self._keypair = solders.keypair.Keypair.from_bytes(secret)
        super().__init__(pubkey=str(self._keypair.pubkey()))

    def sign(self, transaction_bytes: bytes) -> SignedTransaction:
        keypair_mod, tx_mod = self._solders
        transaction = tx_mod.VersionedTransaction.from_bytes(transaction_bytes)
        transaction.sign([self._keypair])
        raw = bytes(transaction)
        return SignedTransaction(
            signer=self.name,
            pubkey=self.pubkey,
            raw=raw,
            simulated=False,
            broadcast_allowed=True,
            details={"keypair": "local-file", "bytes": len(raw)},
        )


class _NotImplementedSigner(Signer):
    simulated = False
    broadcast_allowed = False
    guidance = ""

    def sign(self, transaction_bytes: bytes) -> SignedTransaction:
        raise SignerRefused(
            f"{self.name} signer is a stub. {self.guidance}\n"
            "Do not paper over this by enabling the local keypair signer in production."
        )


class KmsSigner(_NotImplementedSigner):
    name = "kms"
    guidance = (
        "Implement: request the ed25519 key handle from your KMS (AWS KMS / GCP KMS), "
        "send the transaction *message* (not the whole tx) to be signed, splice the "
        "returned 64-byte signature back into the transaction's signature slot. "
        "The private key must never leave the KMS."
    )


class HardwareWalletSigner(_NotImplementedSigner):
    name = "hardware"
    guidance = (
        "Implement: export the unsigned message, have the operator sign on a Ledger, "
        "read the signature back and splice it in. Best paired with a multisig so no "
        "single device can move the whole balance."
    )


def build_signer(
    name: str,
    *,
    keypair_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Signer:
    if name == "refuse":
        return RefuseSigner()
    if name == "mock":
        return MockSigner()
    if name == "local-keypair":
        if keypair_path is None:
            raise SignerRefused("local-keypair signer needs keypair_path")
        return LocalKeypairSigner(keypair_path, env)
    if name == "kms":
        return KmsSigner()
    if name == "hardware":
        return HardwareWalletSigner()
    raise SignerRefused(f"unknown signer {name!r}")

