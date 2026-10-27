PY ?= python3
export PYTHONPATH := src
CONFIG ?= config
STRATEGY ?= trend_pullback
VENUE ?= okx_demo

.PHONY: help doctor smoke test backtest paper okx binance screen reconcile lint \
	onchain-screen onchain-quote onchain-execute symbols-check symbols-scan \
	dashboard release-check deploy-verify release-status rollback-plan

MINT ?= 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU

help:
	@echo "make doctor                          # environment + config self check"
	@echo "make smoke                           # offline end-to-end self test"
	@echo "make test                            # unit tests (stdlib unittest / pytest)"
	@echo "make backtest STRATEGY=trend_breakout"
	@echo "make paper                           # paper trading loop, synthetic market"
	@echo "make okx                             # OKX Demo Trading (dry-run by default)"
	@echo "make binance                         # Binance Spot Testnet (dry-run by default)"
	@echo "make symbols-check                   # validate every configured symbol (needs proxy)"
	@echo "make symbols-scan                    # build a universe block from real 24h volume"
	@echo "make screen                          # on-chain token safety screen"
	@echo "make onchain-screen MINT=...         # merge sources + safety filter (offline fixtures)"
	@echo "make onchain-quote MINT=...          # screen + risk + Jupiter quote + simulate"
	@echo "make onchain-execute MINT=...        # the same, plus a mock signature"
	@echo "make dashboard                       # read-only web UI on http://127.0.0.1:9200"
	@echo "make release-check                   # clean-tree tests + version/progress/changelog gates"
	@echo "make deploy-verify                   # prove the running process matches this checkout"
	@echo "make release-status                  # checkout/runtime build identity"
	@echo "make rollback-plan                   # print tag-based rollback checklist"

doctor:
	$(PY) -m ta doctor --config-dir $(CONFIG)

dashboard:
	$(PY) -m ta dashboard --config-dir $(CONFIG) --venue $(VENUE) --port $(or $(PORT),9200)

smoke:
	$(PY) scripts/smoke_test.py

test:
	$(PY) -m pytest -q tests || $(PY) -m unittest discover -s tests -v

backtest:
	$(PY) -m ta backtest --strategy $(STRATEGY) --config-dir $(CONFIG)

paper:
	$(PY) -m ta run --venue paper --strategy $(STRATEGY) --iterations 3000 --interval 0 --config-dir $(CONFIG)

symbols-check:
	$(PY) -m ta symbols check --venue $(VENUE) --config-dir $(CONFIG)

symbols-scan:
	$(PY) -m ta symbols scan --venue $(VENUE) --top 40 --config-dir $(CONFIG)

okx:
	$(PY) -m ta run --venue okx_demo --strategy $(STRATEGY) --config-dir $(CONFIG)

binance:
	$(PY) -m ta run --venue binance_testnet --strategy $(STRATEGY) --config-dir $(CONFIG)

screen:
	$(PY) -m ta screen --file fixtures/tokens.json --config-dir $(CONFIG)

onchain-screen:
	$(PY) -m ta onchain screen --mint $(MINT) --offline --config-dir $(CONFIG)

onchain-quote:
	$(PY) -m ta onchain quote --mint $(MINT) --offline --amount 0.2 --equity-usd 5000 --config-dir $(CONFIG)

onchain-execute:
	$(PY) -m ta onchain execute --mint $(MINT) --offline --amount 0.2 --equity-usd 5000 --sign --config-dir $(CONFIG)

reconcile:
	$(PY) -m ta reconcile --venue paper --config-dir $(CONFIG)

lint:
	$(PY) -m ruff check src tests scripts

release-check:
	$(PY) scripts/lifecycle.py preflight

deploy-verify:
	$(PY) scripts/lifecycle.py verify

release-status:
	$(PY) scripts/lifecycle.py status

rollback-plan:
	$(PY) scripts/lifecycle.py rollback-plan
