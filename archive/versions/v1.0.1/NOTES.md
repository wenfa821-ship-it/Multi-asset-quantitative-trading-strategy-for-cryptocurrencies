# v1.0.1

Frozen 2026-10-10T12:10:46+08:00. Package version 1.0.1. Tests: 292.

PATCH：对账脚本单位修一致（场馆侧与账本都用张）—— 此前 _venue_swap_positions 换算成 base 单位，ACT/NEAR（ctVal 10）虚报 10 倍差额，且喂给修正逻辑的数量会被当张数写进账本（错 10 倍）。修后五条永续腿全部 ok、nothing to correct；+2 测试（292 全绿）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
