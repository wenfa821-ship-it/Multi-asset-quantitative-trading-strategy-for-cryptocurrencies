# v1.1.29

Frozen 2026-10-30T11:19:40+08:00. Package version 1.1.29. Tests: 514. Git: 2980ae5d611fd128ab224a96b3c55c62a933b207 (clean). Config: bfa03acb0e89a312.

Fix daily review funding attribution to use bill_ts with backfill-aware regression tests; classify OKX 50110/IP whitelist and Host is down as external job failures. Correct 2026-10-29 review: bill funding 643.7929 / 70 backfilled, public-rate 47.7407, replicable lower bound -216.3834. Full suite 514 passed.

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
