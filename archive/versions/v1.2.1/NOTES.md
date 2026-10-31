# v1.2.1

Frozen 2026-10-31T12:48:14+08:00. Package version 1.2.1. Tests: 525. Git: 0ac8c5c01020dab1362abe3f57b63c6fc3820b90 (clean). Config: bfa03acb0e89a312.

Patch release: persist protective stops in equity snapshots and calculate per-leg heat while preserving the v1.2.0 strategy admission policy. Reporting-only change; no trading parameters or risk thresholds changed. Historical Demo key ID remains a known accepted pre-live exception.

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
