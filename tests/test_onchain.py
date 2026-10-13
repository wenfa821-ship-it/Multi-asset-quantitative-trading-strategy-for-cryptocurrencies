"""On-chain layer tests: providers, merging, screening, quoting, signing consent chain.

Everything runs offline against fixtures/onchain/*.json - no network, no keys.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.onchain import (  # noqa: E402
    CompositeTokenService,
    FixtureFetcher,
    HttpClient,
    HttpError,
    JupiterRouter,
    MockSigner,
    OnchainExecutor,
    RefuseSigner,
    RpcError,
    SignedTransaction,
    Signer,
    SignerRefused,
    SolanaRpc,
    TokenScreener,
    TokenSnapshot,
    build_signer,
)
from ta.onchain.models import SimulationResult  # noqa: E402
from ta.onchain.providers import (  # noqa: E402
    DexScreenerProvider,
    GMGNProvider,
    GoPlusProvider,
    RugCheckProvider,
    as_bool,
    as_pct,
    dig,
    extract_mint,
)
from ta.onchain.signers import LocalKeypairSigner  # noqa: E402
from ta.portfolio import Ledger  # noqa: E402
from ta.risk.engine import RiskEngine  # noqa: E402

CONFIG_DIR = PROJECT / "config"
FIXTURES = PROJECT / "fixtures" / "onchain"

CLEAN = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
THIN = "3xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsV"
RUG = "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E"
IMPACT = "4mKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsW"


def fixture_client(tmp: str) -> HttpClient:
    return HttpClient(cache_dir=Path(tmp) / "cache", fetcher=FixtureFetcher(FIXTURES))


class Helpers(unittest.TestCase):
    """Shared base. Must be a TestCase subclass or pytest silently skips the tests."""

    def config(self) -> Config:
        return Config.load(CONFIG_DIR)


class CoercionTests(unittest.TestCase):
    def test_dig_and_bool_and_pct(self) -> None:
        payload = {"a": {"b": [{"c": 7}]}}
        self.assertEqual(dig(payload, "a.b.0.c"), 7)
        self.assertIsNone(dig(payload, "a.x.c"))
        self.assertIs(dig(payload, "nope", default=False), False)
        self.assertTrue(as_bool("1"))
        self.assertFalse(as_bool("0"))
        self.assertIsNone(as_bool("maybe"))
        self.assertAlmostEqual(as_pct(0.185) or 0, 18.5)
        self.assertAlmostEqual(as_pct(18.5) or 0, 18.5)

    def test_extract_mint_takes_the_last_match(self) -> None:
        url = (
            "https://lite-api.jup.ag/swap/v1/quote?"
            f"inputMint=So11111111111111111111111111111111111111112&outputMint={CLEAN}&amount=50000000"
        )
        self.assertEqual(extract_mint(url), CLEAN)


class ProviderTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.client = fixture_client(self.tmp.name)
        self.sources = self.config().onchain.data.sources

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dexscreener(self) -> None:
        fields = DexScreenerProvider(self.sources["dexscreener"], self.client).collect(CLEAN)
        self.assertAlmostEqual(fields["liquidity_usd"], 640000.0)
        self.assertAlmostEqual(fields["volume_24h_usd"], 1200000.55)
        self.assertAlmostEqual(fields["price_usd"], 0.128)
        self.assertEqual(fields["symbol"], "CLEAN")
        self.assertGreater(fields["age_minutes"], 30)
        # DexScreener knows nothing about taxes/honeypots and must not invent them
        self.assertNotIn("sell_tax_pct", fields)
        self.assertNotIn("is_honeypot", fields)

    def test_goplus(self) -> None:
        fields = GoPlusProvider(self.sources["goplus"], self.client).collect(CLEAN)
        self.assertFalse(fields["freezable"])
        self.assertFalse(fields["mintable"])
        self.assertEqual(fields["holder_count"], 10)
        self.assertAlmostEqual(fields["top10_holder_pct"], 18.2)
        self.assertTrue(fields["lp_locked"])
        self.assertIn("dev_wallet", fields)

    def test_rugcheck_sets_scan_and_detects_honeypot(self) -> None:
        clean = RugCheckProvider(self.sources["rugcheck"], self.client).collect(CLEAN)
        self.assertTrue(clean["safety_scan_ok"])
        self.assertAlmostEqual(clean["lp_locked_pct"], 100.0)
        self.assertEqual(clean["holder_count"], 5200)
        rug = RugCheckProvider(self.sources["rugcheck"], self.client).collect(RUG)
        self.assertTrue(rug["is_honeypot"])
        self.assertFalse(rug["safety_scan_ok"])
        self.assertFalse(rug["lp_locked"])

    def test_gmgn_rates_and_notes(self) -> None:
        fields = GMGNProvider(self.sources["gmgn"], self.client).collect(CLEAN)
        self.assertAlmostEqual(fields["top10_holder_pct"], 18.5)
        self.assertAlmostEqual(fields["dev_hold_pct"], 2.5)
        self.assertTrue(fields["lp_locked"])
        self.assertTrue(any("smart_degen" in note for note in fields.get("notes", [])))

    def test_missing_fixture_raises(self) -> None:
        provider = DexScreenerProvider(self.sources["dexscreener"], self.client)
        with self.assertRaises(Exception):
            provider.collect("11111111111111111111111111111111")


class CompositeTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = self.config()
        self.service = CompositeTokenService.from_config(
            self.config.onchain,
            PROJECT,
            client=fixture_client(self.tmp.name),
            fixture_dir=FIXTURES,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_merge_with_provenance(self) -> None:
        snapshot = self.service.snapshot(CLEAN)
        self.assertAlmostEqual(snapshot.liquidity_usd, 640000.0)
        self.assertEqual(snapshot.source_of("liquidity_usd"), "dexscreener")
        self.assertEqual(snapshot.source_of("lp_locked_pct"), "rugcheck")
        self.assertEqual(snapshot.source_of("dev_wallet"), "goplus")
        self.assertTrue(snapshot.safety_scan_ok)
        self.assertEqual(snapshot.missing(), [])

    def test_danger_flags_are_sticky_and_bools_conservative(self) -> None:
        # RUG: gmgn says honeypot + unlocked LP, rugcheck agrees, goplus is silent
        snapshot = self.service.snapshot(RUG)
        self.assertTrue(snapshot.is_honeypot)
        self.assertFalse(snapshot.lp_locked)
        self.assertTrue(snapshot.freezable)
        self.assertTrue(snapshot.mintable)

    def test_partial_sources_still_produce_a_snapshot(self) -> None:
        snapshot = self.service.snapshot(THIN)
        self.assertAlmostEqual(snapshot.liquidity_usd, 12000.0)
        self.assertGreater(snapshot.top10_holder_pct, 40.0)


class ScreenerTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = self.config()
        self.screener = TokenScreener(self.config.onchain)
        self.service = CompositeTokenService.from_config(
            self.config.onchain,
            PROJECT,
            client=fixture_client(self.tmp.name),
            fixture_dir=FIXTURES,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clean_passes_and_rug_is_blocked(self) -> None:
        self.assertTrue(self.screener.screen(self.service.snapshot(CLEAN)).passed)
        rug = self.screener.screen(self.service.snapshot(RUG))
        self.assertFalse(rug.passed)
        joined = " ".join(rug.reasons)
        self.assertIn("honeypot", joined)
        self.assertIn("lp locked", joined)

    def test_thin_liquidity_blocked(self) -> None:
        result = self.screener.screen(self.service.snapshot(THIN))
        self.assertFalse(result.passed)
        self.assertIn("liquidity", " ".join(result.reasons))

    def test_unknown_safety_fields_fail_closed(self) -> None:
        # a hand-built snapshot that never resolved lp lock status
        token = TokenSnapshot(
            mint="abc",
            symbol="X",
            liquidity_usd=1_000_000,
            holder_count=9_999,
            top10_holder_pct=5.0,
            age_minutes=10_000,
            is_honeypot=False,
            provided=["liquidity_usd", "holder_count", "top10_holder_pct", "age_minutes", "is_honeypot"],
        )
        result = self.screener.screen(token)
        self.assertFalse(result.passed)
        self.assertIn("unknown safety fields", " ".join(result.reasons))
        self.assertIn("lp_locked", " ".join(result.reasons))

    def test_honeypot_unknown_is_rejected_even_when_fields_are_present(self) -> None:
        token = TokenSnapshot(
            mint="abc",
            symbol="X",
            liquidity_usd=1_000_000,
            holder_count=9_999,
            top10_holder_pct=5.0,
            age_minutes=10_000,
            lp_locked=True,
            provided=["liquidity_usd", "holder_count", "top10_holder_pct", "age_minutes", "lp_locked"],
        )
        result = self.screener.screen(token)
        self.assertFalse(result.passed)
        self.assertIn("honeypot status unknown", " ".join(result.reasons))


class JupiterTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = self.config()
        self.client = fixture_client(self.tmp.name)
        self.jupiter = JupiterRouter(self.config.onchain, self.client)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_quote_scales_price_impact(self) -> None:
        quote = self.jupiter.quote(JupiterRouter.SOL_MINT, CLEAN, 200_000_000)
        self.assertAlmostEqual(quote.price_impact_pct, 0.42)
        self.assertEqual(quote.out_amount, 5_800_000_000)
        self.assertIn("Raydium", quote.route_labels)
        ok, detail = self.jupiter.check(quote)
        self.assertTrue(ok, detail)

    def test_quote_gate_blocks_high_impact(self) -> None:
        quote = self.jupiter.quote(JupiterRouter.SOL_MINT, IMPACT, 200_000_000)
        ok, detail = self.jupiter.check(quote)
        self.assertFalse(ok)
        self.assertIn("price impact", detail)

    def test_build_swap_returns_decodable_transaction(self) -> None:
        quote = self.jupiter.quote(JupiterRouter.SOL_MINT, CLEAN, 200_000_000)
        plan = self.jupiter.build_swap(quote, MockSigner().pubkey)
        self.assertFalse(plan.simulated_placeholder)
        self.assertGreater(len(plan.transaction_bytes), 128)
        self.assertGreater(plan.priority_fee_lamports, 0)

    def test_router_never_signs(self) -> None:
        with self.assertRaises(NotImplementedError):
            self.jupiter.sign_and_send()


class SignerTests(unittest.TestCase):
    def test_mock_signer_is_deterministic_and_unbroadcastable(self) -> None:
        signer = MockSigner()
        a = signer.sign(b"transaction-bytes")
        b = signer.sign(b"transaction-bytes")
        self.assertEqual(a.raw, b.raw)
        self.assertEqual(len(a.raw), 64)
        self.assertTrue(a.simulated)
        self.assertFalse(a.broadcast_allowed)
        self.assertTrue(a.signature.startswith("SIMULATED-"))

    def test_mock_signer_refuses_empty_input(self) -> None:
        with self.assertRaises(SignerRefused):
            MockSigner().sign(b"")

    def test_refuse_signer(self) -> None:
        with self.assertRaises(SignerRefused):
            RefuseSigner().sign(b"x")

    def test_stub_signers_fail_loudly(self) -> None:
        for name in ("kms", "hardware"):
            with self.assertRaises(SignerRefused) as ctx:
                build_signer(name).sign(b"x")
            self.assertIn("stub", str(ctx.exception))

    def test_unknown_signer(self) -> None:
        with self.assertRaises(SignerRefused):
            build_signer("nonsense")

    def test_local_keypair_needs_env_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            path.write_text(json.dumps(list(range(64))), encoding="utf-8")
            with self.assertRaises(SignerRefused) as ctx:
                LocalKeypairSigner(path, env={})
            self.assertIn("TA_ALLOW_LOCAL_KEY", str(ctx.exception))
            path.chmod(0o644)
            with self.assertRaises(SignerRefused) as ctx:
                LocalKeypairSigner(path, env={"TA_ALLOW_LOCAL_KEY": "1"})
            self.assertIn("readable", str(ctx.exception))


class RpcTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = self.config()
        self.rpc = SolanaRpc("https://api.mainnet-beta.solana.com", fixture_client(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_simulation_parses_the_fixture(self) -> None:
        result = self.rpc.simulate_transaction("ZmFrZQ==")
        self.assertTrue(result.ok)
        self.assertEqual(result.units_consumed, 124877)
        self.assertTrue(any("swap executed" in line for line in result.logs))

    def test_broadcast_is_refused_without_explicit_flag(self) -> None:
        with self.assertRaises(RpcError):
            self.rpc.send_transaction("ZmFrZQ==")

    def test_missing_rpc_config(self) -> None:
        self.assertIsNone(
            SolanaRpc.from_config(self.config.onchain, fixture_client(self.tmp.name), env={})
        )


class _FakeRealSigner(Signer):
    """Stands in for a KMS/hardware signer: real-shaped output, no key involved."""

    name = "fake-hsm"
    simulated = False
    broadcast_allowed = True

    def sign(self, transaction_bytes: bytes) -> SignedTransaction:
        raw = hashlib.sha256(b"fake-hsm" + transaction_bytes).digest() * 2
        return SignedTransaction(
            signer=self.name, pubkey=self.pubkey, raw=raw, simulated=False, broadcast_allowed=True
        )


class _FakeRpc:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def simulate_transaction(self, transaction_b64: str) -> SimulationResult:
        return SimulationResult(ok=True, units_consumed=1_000, logs=["fake simulate ok"])

    def send_transaction(self, transaction_b64: str, *, allow_broadcast: bool = False, max_retries: int = 3) -> str:
        if not allow_broadcast:
            raise RpcError("broadcast refused: allow_broadcast=False")
        self.sent.append(transaction_b64)
        return "FAKESIGNATURE123"


class ExecutorTests(Helpers):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.config = self.config()
        self.client = fixture_client(self.tmp.name)
        self.service = CompositeTokenService.from_config(
            self.config.onchain, PROJECT, client=self.client, fixture_dir=FIXTURES
        )
        self.jupiter = JupiterRouter(self.config.onchain, self.client)
        self.ledger = Ledger(self.tmp_path / "ledger.jsonl")
        self.risk = RiskEngine(
            self.config.risk, self.tmp_path / "KILL_SWITCH", self.config.onchain
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _executor(self, signer=None, rpc=None, risk="default", rehearsal=True) -> OnchainExecutor:
        return OnchainExecutor(
            self.config.onchain,
            self.service,
            self.jupiter,
            signer or MockSigner(),
            rpc=rpc if rpc is not None else SolanaRpc("https://api.mainnet-beta.solana.com", self.client),
            risk=self.risk if risk == "default" else risk,
            ledger=self.ledger,
            rehearsal=rehearsal,
        )

    def test_rug_is_blocked_before_any_quote(self) -> None:
        result = self._executor().execute(RUG, 200_000_000, allow_signing=True, equity_usd=5000)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.stage, "screen")
        self.assertIsNone(result.quote)
        kinds = {event["kind"] for event in self.ledger.events}
        self.assertIn("onchain_snapshot", kinds)
        self.assertIn("onchain_screen", kinds)
        self.assertNotIn("onchain_quote", kinds)

    def test_risk_gate_blocks_an_oversized_onchain_order(self) -> None:
        result = self._executor().execute(CLEAN, 5_000_000_000, allow_signing=True, equity_usd=200)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.stage, "risk")

    def test_quote_impact_blocks_a_screened_token(self) -> None:
        result = self._executor().execute(IMPACT, 300_000_000, allow_signing=True, equity_usd=5000)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.stage, "quote")
        self.assertIn("price impact", result.reason)

    def test_happy_path_is_simulated_and_never_broadcast(self) -> None:
        result = self._executor().execute(
            CLEAN, 200_000_000, allow_signing=True, allow_broadcast=True, equity_usd=5000
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.signer, "mock")
        self.assertFalse(result.broadcast_allowed)
        self.assertTrue(result.signature.startswith("SIMULATED-"))
        self.assertTrue(result.simulation and result.simulation["ok"])
        self.assertAlmostEqual(result.notional_usd, 30.0, places=6)
        self.assertEqual(result.details["rehearsal"], True)

    def test_executor_refuses_without_a_risk_engine(self) -> None:
        result = self._executor(risk=None).execute(CLEAN, 200_000_000, allow_signing=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stage, "risk")

    def test_plan_only_does_not_sign(self) -> None:
        result = self._executor().execute(CLEAN, 200_000_000, allow_signing=False, equity_usd=5000)
        self.assertEqual(result.signature, "")
        self.assertIn("signing not requested", result.reason)

    def test_real_signer_stops_at_signed_without_full_consent(self) -> None:
        fake_rpc = _FakeRpc()
        executor = self._executor(signer=_FakeRealSigner(), rpc=fake_rpc)
        result = executor.execute(
            CLEAN, 200_000_000, allow_signing=True, allow_broadcast=True, equity_usd=5000
        )
        self.assertEqual(result.status, "signed")
        self.assertEqual(fake_rpc.sent, [])
        consents = result.details["consents"]
        self.assertFalse(consents["config_allow_broadcast"])
        self.assertFalse(consents["config_dry_run_disabled"])

    def test_broadcast_requires_every_consent(self) -> None:
        fake_rpc = _FakeRpc()
        self.config.onchain.execution.allow_broadcast = True
        self.config.onchain.execution.dry_run = False
        executor = self._executor(signer=_FakeRealSigner(), rpc=fake_rpc)
        result = executor.execute(
            CLEAN, 200_000_000, allow_signing=True, allow_broadcast=True, equity_usd=5000
        )
        self.assertEqual(result.status, "broadcast")
        self.assertEqual(result.signature, "FAKESIGNATURE123")
        self.assertEqual(len(fake_rpc.sent), 1)


class HttpClientTests(unittest.TestCase):
    def test_retry_until_success(self) -> None:
        calls: list[int] = []

        def flaky(method, url, headers, body, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HttpError("boom")
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            client = HttpClient(cache_dir=Path(tmp), retries=2, backoff=0.0, fetcher=flaky)
            self.assertEqual(client.get_json("https://example.com/x"), {"ok": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.source_counts["network"], 1)

    def test_cache_is_a_fallback_never_a_source(self) -> None:
        """A dead network may serve the cache, but a live call always goes to the network."""
        calls: list[int] = []

        def ok(method, url, headers, body, timeout):
            calls.append(1)
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            client = HttpClient(cache_dir=Path(tmp), retries=0, fetcher=ok)
            client.get_json("https://example.com/x")
            client.get_json("https://example.com/x")
            self.assertEqual(len(calls), 2, "a live call must not be short-circuited by the cache")

            def dead(method, url, headers, body, timeout):
                raise HttpError("network down")

            fallback = HttpClient(cache_dir=Path(tmp), retries=0, fetcher=dead)
            self.assertEqual(fallback.get_json("https://example.com/x"), {"ok": True})
            self.assertEqual(fallback.source_counts["cache"], 1)

    def test_offline_never_reads_or_writes_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = HttpClient(cache_dir=Path(tmp), offline=True, fetcher=lambda *a: {"ok": True})
            self.assertEqual(client.get_json("https://example.com/x"), {"ok": True})
            self.assertEqual(client.source_counts["offline"], 1)
            self.assertEqual(list(Path(tmp).glob("*.json")), [], "offline must not write the cache")

    def test_offline_without_a_source_raises(self) -> None:
        def nothing(method, url, headers, body, timeout):
            raise HttpError("no fixture recorded")

        with tempfile.TemporaryDirectory() as tmp:
            client = HttpClient(cache_dir=Path(tmp), offline=True, retries=0, fetcher=nothing)
            with self.assertRaises(HttpError) as ctx:
                client.get_json("https://example.com/x")
            self.assertIn("offline mode", str(ctx.exception))



class CollapseStatsTests(unittest.TestCase):
    """2026-10-13：`ta scan evaluate` 每轮打印「11% 是重复写入，说明有多个扫描器在写同一账本」。

    按天拆开那份账本：被折叠的 7,269 行里 91% 来自 09-29/09-30（清理多扫描器之前），
    近 24 小时为 0。把历史合计当当前状况念出来，会让读者去找不存在的重复进程 ——
    而这个项目的 `ps`/`pgrep` 在沙箱里本来就是假阴性，正好凑成一次误报。
    """

    @staticmethod
    def _r(mint: str, ts: int, price: float = 1.0) -> dict:
        return {"mint": mint, "ts": ts, "price_usd": price}

    def test_collapse_keeps_the_newest_of_a_minute_and_reports_the_rest(self) -> None:
        from ta.onchain.study import collapse

        base = 1_790_000_000_000
        rows = [self._r("A", base, 1.0), self._r("A", base + 20_000, 2.0),
                self._r("A", base + 61_000, 3.0), self._r("B", base, 9.0)]
        kept, dropped = collapse(rows, bucket_seconds=60)
        # 结果按时间排序：B@base（9.0）、A@base+20s（2.0，同分钟里取代 1.0）、A@base+61s（3.0）
        self.assertEqual([r["price_usd"] for r in kept], [9.0, 2.0, 3.0])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["price_usd"], 1.0)   # 同一分钟里被最新那条取代

    def test_stats_say_when_the_collapse_happened(self) -> None:
        """关键不是「折叠了多少」，而是「现在还折叠吗」。"""
        from ta.onchain.study import collapse_stats

        base = 1_790_000_000_000
        rows = [self._r("A", base, 1.0), self._r("A", base + 5_000, 2.0)]
        stats = collapse_stats(rows, now_ms=base + 10 * 86_400_000, recent_hours=24)
        self.assertEqual((stats["rows"], stats["kept"], stats["dropped"]), (2, 1, 1))
        self.assertEqual(stats["recent_dropped"], 0)     # 十天前的折叠，不是现在
        stats_now = collapse_stats(rows, now_ms=base + 60_000, recent_hours=24)
        self.assertEqual(stats_now["recent_dropped"], 1)

    def test_the_note_does_not_cry_multiple_writers_when_the_rows_are_old(self) -> None:
        from ta.onchain.study import collapse_note, collapse_stats

        base = 1_790_000_000_000
        rows = [self._r("A", base, 1.0), self._r("A", base + 5_000, 2.0)]
        old = collapse_note(collapse_stats(rows, now_ms=base + 10 * 86_400_000))
        self.assertIn("不是现在有多个扫描器", old)
        now = collapse_note(collapse_stats(rows, now_ms=base + 60_000))
        self.assertIn("多个扫描器在写同一账本", now)
        self.assertIn("lsof", now)                       # 给可执行的下一步，而不是猜
        self.assertIn("无同分钟重复采样", collapse_note(
            collapse_stats([self._r("A", base)], now_ms=base)))

    def test_dedupe_rows_still_returns_just_the_rows(self) -> None:
        """`dedupe_rows` 的对外行为不变（看板与 evaluate 都在用它）。"""
        from ta.onchain.study import dedupe_rows

        base = 1_790_000_000_000
        rows = [self._r("A", base, 1.0), self._r("A", base + 5_000, 2.0)]
        self.assertEqual([r["price_usd"] for r in dedupe_rows(rows)], [2.0])
        self.assertEqual(dedupe_rows(rows, bucket_seconds=0), rows)

class EventStudyHonestyTests(unittest.TestCase):
    """The study must refuse to report a trend when the sample cannot support one.

    Measured 2026-09-30 on the live store: 98% coverage on the +5m horizon, and
    yet the median forward return was exactly 0.00% with a 0.8% win rate. The
    cause is that most observations are tokens nobody is trading, so "the price
    five minutes later" is the same number. These tests pin that down.
    """

    def test_a_pool_switch_is_not_a_price_move(self) -> None:
        """Two snapshots priced from different pools are not a time series.

        Measured 2026-10-02: the DexScreener provider picks the deepest pair on
        every call, so a mint with several pools produces a price that jumps when
        the "deepest" flips. One such artefact read +88,183% on a token whose own
        series never moved more than 15% - and it was carrying the entire positive
        mean of the study (+8.68% with it, -1.69% without).
        """
        from ta.onchain.study import attach_forward_returns

        rows = [
            {"ts": 0, "mint": "M", "price_usd": 1.0, "pair_address": "POOL_A"},
            {"ts": 3_600_000, "mint": "M", "price_usd": 2.0, "pair_address": "POOL_A"},
        ]
        same_pool = attach_forward_returns(rows, [3600])
        self.assertAlmostEqual(same_pool[0]["fwd"][3600], 100.0, places=6)

        switched = [
            {"ts": 0, "mint": "M", "price_usd": 1.0, "pair_address": "POOL_A"},
            {"ts": 3_600_000, "mint": "M", "price_usd": 2.0, "pair_address": "POOL_B"},
        ]
        cross_pool = attach_forward_returns(switched, [3600])
        self.assertNotIn(3600, cross_pool[0]["fwd"], "a pool switch is not a return")
        self.assertEqual(cross_pool[0]["pool_switch_dropped"], 1)

    def test_unknown_pool_is_tolerated_for_old_rows(self) -> None:
        """Rows written before the field existed must still produce a series."""
        from ta.onchain.study import attach_forward_returns

        rows = [
            {"ts": 0, "mint": "M", "price_usd": 1.0},
            {"ts": 3_600_000, "mint": "M", "price_usd": 1.5},
        ]
        out = attach_forward_returns(rows, [3600])
        self.assertAlmostEqual(out[0]["fwd"][3600], 50.0, places=6)

    def test_bucket_reports_distinct_tokens_not_just_rows(self) -> None:
        """A bucket with 40 rows from 2 tokens is NOT a 40-sample bucket.

        Measured 2026-10-01: the +1h "20+" bucket showed n=44, which passed the
        n>=20 rule, but those 44 rows came from 3 mints - one of them contributing
        23. The median was that token's behaviour repeated, not a population
        statistic, so the verdict must weigh distinct tokens too.
        """
        from ta.onchain.study import (
            attach_forward_returns,
            bucket_report,
            format_report,
        )

        rows: list[dict] = []
        for mint in ("A", "B"):
            for i in range(20):
                rows.append(
                    {
                        "ts": i * 300_000,
                        "mint": mint,
                        "price_usd": 1.0 + 0.01 * i,
                        "liquidity_usd": 50_000.0,
                        "volume_24h_usd": 2_000_000.0,  # turnover 40 -> "20+" bucket
                    }
                )
        report = bucket_report(attach_forward_returns(rows, [300]), [300], bucket_by="turnover")
        stats = report["buckets"][300]["20+"]
        self.assertGreaterEqual(stats["n"], 20)
        self.assertEqual(stats["tokens"], 2, "only two mints produced those rows")

        text = format_report(report, len(rows))
        self.assertIn("极少数代币", text)
        self.assertIn("样本不足", text, "n is large but the tokens behind it are not")

    @staticmethod
    def _rows() -> list[dict]:
        rows: list[dict] = []
        # FROZEN: price never moves across four scans.
        for i in range(4):
            rows.append(
                {
                    "ts": i * 300_000,
                    "mint": "FROZEN",
                    "price_usd": 1.0,
                    "liquidity_usd": 50_000.0,
                    "volume_24h_usd": 10_000.0,
                    "txns_m5": 0,
                }
            )
        # LIVE: price moves every scan.
        for i in range(4):
            rows.append(
                {
                    "ts": i * 300_000,
                    "mint": "LIVE",
                    "price_usd": 1.0 + 0.1 * i,
                    "liquidity_usd": 50_000.0,
                    "volume_24h_usd": 10_000.0,
                    "txns_m5": 40,
                }
            )
        return rows

    def test_zero_change_ratio_counts_frozen_observations(self) -> None:
        from ta.onchain.study import zero_change_ratio

        frozen = zero_change_ratio(self._rows())
        # 3 pairs per token; FROZEN contributes 3 unchanged, LIVE contributes 0.
        self.assertEqual(frozen["pairs"], 6)
        self.assertEqual(frozen["unchanged_pairs"], 3)
        self.assertAlmostEqual(frozen["unchanged_fraction"], 0.5, places=6)
        self.assertEqual(frozen["tokens_with_pairs"], 2)
        self.assertEqual(frozen["tokens_frozen"], 1)

    def test_activity_filter_reports_what_it_dropped(self) -> None:
        from ta.onchain.study import attach_forward_returns, bucket_report

        rows = attach_forward_returns(self._rows(), [300])
        report = bucket_report(rows, [300], bucket_by="txns_m5", min_txns_m5=5)
        self.assertEqual(report["activity_filter"]["rows_with_activity_field"], 8)
        self.assertEqual(report["activity_filter"]["rows_kept"], 4)  # LIVE only
        buckets = report["buckets"][300]
        # the last LIVE scan has no later observation, so 3 carry a forward return
        self.assertEqual(sum(s.get("n", 0) for s in buckets.values()), 3)

    def test_a_row_without_the_activity_field_is_excluded_not_called_zero(self) -> None:
        """Old rows predate `txns_m5`. They must not be silently treated as 0."""
        from ta.onchain.study import bucket_report

        rows = [
            {"ts": 0, "mint": "OLD", "price_usd": 1.0, "liquidity_usd": 5e4},
            {"ts": 300_000, "mint": "OLD", "price_usd": 1.1, "liquidity_usd": 5e4},
        ]
        report = bucket_report(rows, [300], bucket_by="turnover", min_txns_m5=5)
        self.assertEqual(report["activity_filter"]["rows_with_activity_field"], 0)
        self.assertEqual(report["activity_filter"]["rows_kept"], 0)

    def test_off_by_default_changes_nothing(self) -> None:
        from ta.onchain.study import bucket_report

        rows = self._rows()
        report = bucket_report(rows, [300], bucket_by="turnover")
        self.assertNotIn("activity_filter", report)
        self.assertIn("zero_change", report)


class TrendingDiscoveryTests(unittest.TestCase):
    """A second discovery source exists because the first one is too small.

    Measured 2026-10-01: the DexScreener promotion feeds only ever carry ~12-17
    live tokens (even with limit=200 and a $1k floor), while a bucketed
    forward-return study needs 60-80 distinct tokens. GeckoTerminal's trending
    pools are public, keyless and paginated.
    """

    def test_trending_pools_map_onto_the_candidate_model(self) -> None:
        from ta.onchain.discovery import _trending

        payload = {
            "data": [
                {
                    "id": "solana_POOLADDR",
                    "type": "pool",
                    "attributes": {
                        "name": "SI / SOL",
                        "base_token_price_usd": "0.0380176",
                        "reserve_in_usd": "858865.20",
                        "volume_usd": {"m5": "375.79", "h1": "71698.35", "h24": "2561762.9"},
                        "transactions": {
                            "m5": {"buys": 0, "sells": 4},
                            "h1": {"buys": 900, "sells": 850},
                        },
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP"}}
                    },
                },
                {"id": "solana_X", "attributes": {}, "relationships": {}},  # no base token -> skipped
            ]
        }

        class _Client:
            def get_json(self, url):  # noqa: ANN001
                return payload if "page=1" in url else {"data": []}

        rows = _trending(_Client(), "solana", pages=2)
        self.assertEqual(len(rows), 1, "a pool without a base token must be skipped")
        c = rows[0]
        self.assertEqual(c.mint, "DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP")
        self.assertEqual(c.symbol, "SI")
        self.assertEqual(c.sources, ["geckoterminal:trending"])
        self.assertAlmostEqual(c.liquidity_usd, 858865.20, places=2)
        self.assertAlmostEqual(c.volume_24h_usd, 2561762.9, places=1)
        self.assertAlmostEqual(c.volume_h1_usd, 71698.35, places=2)
        self.assertEqual(c.txns_h1, 1750, "buys + sells")
        self.assertEqual(c.txns_m5, 4)
        self.assertTrue(c.is_active)

    def test_discover_merges_trending_with_the_promotion_feeds(self) -> None:
        from ta.onchain.discovery import discover

        class _Client:
            def get_json(self, url):  # noqa: ANN001
                if "trending_pools" in url:
                    return {
                        "data": [
                            {
                                "id": "solana_P",
                                "attributes": {
                                    "name": "TREND / SOL",
                                    "base_token_price_usd": "1.5",
                                    "reserve_in_usd": "50000",
                                    "volume_usd": {"h1": "200000", "h24": "900000"},
                                    "transactions": {"h1": {"buys": 60, "sells": 50}},
                                },
                                "relationships": {"base_token": {"data": {"id": "solana_TRENDMINT"}}},
                            }
                        ]
                    }
                return []  # promotion feeds empty

        config = Config.load(CONFIG_DIR).onchain
        rows = discover(config, _Client(), limit=10, min_liquidity_usd=10_000, trending_pages=1)
        self.assertEqual([c.mint for c in rows], ["TRENDMINT"])
        self.assertAlmostEqual(rows[0].recent_turnover, 4.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
