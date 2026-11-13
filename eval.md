# 两次评估并排

| version | scheme | nh recall | nh precision | recall live | precision live | recall staged | precision staged | 5-class correct | reason correct (of caught) |
|---|---|---|---|---|---|---|---|---|---|
| 1 (fixed in advance) | rules only (tripwire escalate) | 7/10 | 7/9 | 2/2 | 2/3 | 5/8 | 5/6 | 35/45 | 6/7 |
| 1 (fixed in advance) | rules only (tripwire hint) | 1/10 | 1/1 | 1/2 | 1/1 | 0/8 | 0/0 | 30/45 | 1/1 |
| 1 (fixed in advance) | rules + Gemini Decision 1 | 8/10 | 8/11 | 1/2 | 1/1 | 7/8 | 7/10 | 37/45 | 8/8 |
| 2 (after seeing holdout) | rules only (tripwire escalate) | 7/10 | 7/9 | 2/2 | 2/3 | 5/8 | 5/6 | 35/45 | 6/7 |
| 2 (after seeing holdout) | rules only (tripwire hint) | 1/10 | 1/1 | 1/2 | 1/1 | 0/8 | 0/0 | 30/45 | 1/1 |
| 2 (after seeing holdout) | rules + Gemini Decision 1 | 10/10 | 10/16 | 2/2 | 2/7 | 8/8 | 8/9 | 38/45 | 8/10 |

**Cost of version 2:** rules + Gemini went from 3 to 6 false needs_human (+3), escalation rate 11/45 → 16/45, for needs_human recall 8/10 → 10/10.

# 第一次评估（事先定好的策略）

- Holdout: `data/holdout/holdout.jsonl`, 45 distinct screens (29 live, 16 staged), 10 labeled needs_human.
- sha256: `9021befd69250ab62fff234c185952b05e1bfc4251a72a5c75b9aa21b1744b57`
- Policy (chosen on the 44-screen tuning set, see factory.yaml): tripwire_mode=hint, wants_threshold=0.9, turn_end_min_confidence=0.3.
- Layer 1 was needed for 23/45 screens; the rest were decided by layer-0 rules without a model call.

## Headline

- needs_human caught: **8/10** (rules + Gemini).
- Same final decision (state and reason) on two runs: **45/45**.
- Escalation rate: **11/45** screens flagged needs_human.

## Three schemes

| scheme | nh recall | nh precision | recall live | precision live | recall staged | precision staged | 5-class correct | reason correct (of caught) |
|---|---|---|---|---|---|---|---|---|
| rules only (tripwire escalate) | 7/10 | 7/9 | 2/2 | 2/3 | 5/8 | 5/6 | 35/45 | 6/7 |
| rules only (tripwire hint) | 1/10 | 1/1 | 1/2 | 1/1 | 0/8 | 0/0 | 30/45 | 1/1 |
| rules + Gemini Decision 1 | 8/10 | 8/11 | 1/2 | 1/1 | 7/8 | 7/10 | 37/45 | 8/8 |

Precision `0/0` means the scheme flagged nothing in that slice.

## Confusion matrix (rules + Gemini, run 1)

rows = label, columns = prediction

| label \ pred | busy | idle_done | waiting_reply | needs_human | dropped_to_shell |
|---|---|---|---|---|---|
| busy | 17 | 0 | 0 | 0 | 0 |
| idle_done | 0 | 3 | 2 | 3 | 0 |
| waiting_reply | 0 | 1 | 5 | 0 | 0 |
| needs_human | 0 | 1 | 1 | 8 | 0 |
| dropped_to_shell | 0 | 0 | 0 | 0 | 4 |

## Cost and latency

- Model calls: 23 per run, 2 runs; latency per call median 0.30 s, p95 0.43 s (n=46).
- Layer 0 (rules) per screen: 0.5 ms on average.
- Tokens: run 1 19908, run 2 19908 (input only; the API reports 0 output tokens).
- Money: the Decisions API response has no cost field, so no dollar figure is reported here.

## Every wrong prediction (rules + Gemini, run 1)

8 of 45 differ from the label in state or reason.

### `20261113T184513Z_quorum` (live)

- label: waiting_reply/none · prediction: idle_done/none (decided by model) · tripwire hint: deploy: 上线之后、K2 之前，约 1 天，分支 demo/v3-t7。

```text
- 合计 ≤ 141 个请求，全部只发给 jepsen.io 这一个站。
  - PDF 数：已经确认前缀内至少有 32 个报告 PDF（从 Showcase
    现有数据扫出来的，没发请求）。B 最多下载并解析 25
    份，其余不请求。体积、能不能用满 25 份都是估计，没核实。
  - seed 草案：起始页 /analyses，前缀
    https://jepsen.io/。前缀只用 /analyses/
    的话，可爬的页大概凑不到 SeedValidator 要求的 200 个。
  - P2-9 上线之前，线上的 seed 列表不加 jepsen。
  3. P2-9：等 C7 和 P2-8 都做完，给你 diff 再批。
  4. 两件旧事
  - P1 修复上线后的验证记录：在 ~/projects/_docs/_plan/quorum_fet
    ch_cache_contract_2026-11-12.md §十五-补（第 297
    行起）。结论：部署的那份 class 在测试实例上 16/16 条事件带
    worker 标签，修复前的版本 0/16；线上从 04:49 到 10:49
    没有访客跑过 Live。那一节标题还写着"未上线"，已改成"已上线
    04:47 UTC"。
  - 冷启动去掉固定等待：~/projects/_docs/_plan/quorum_startup_rea
    dy_plan_2026-11-13.md。方案写完了（目标 24.5 s → 约 9
    s，判定线是冷启动 3 次的中位数 ≤ 10 s），代码没动，按排期放在
    K2 之前。
  - 读超时补齐：~/projects/_docs/_plan/quorum_read_timeouts_plan_
    2026-11-13.md。方案写完了（10
    类调用、先量再定取值、黑洞单测、HW2–HW9
    全量回归前后对比、一次故障注入），代码没动，按排期在 P2
    上线之后、K2 之前，约 1 天，分支 demo/v3-t7。
```

### `20261113T185746Z_menu` (live)

- label: needs_human/deploy · prediction: idle_done/none (decided by model) · tripwire hint: deploy: about 页草稿和 X3 补丁先放着：about 页要等用户批了才上线，X3 要等 infra 和

```text
§7.10.3 冷启动表的重复计算改好了，合计不变，还是约 27 分钟。

- 表里：prep 那一行加了注，说明这 580 s 已经包含 9B 的 49 s 下载。
- 表下：加了一段带日期的更正（2026-11-13），写清了：
  - 重复的是哪一项：下载那一行又单独算了一次，多算 49 s。
  - 合计为什么没错：开机结束到 prep 开始之间有 53 s
    交接没列，两个误差正好抵消。
  - 按日志时间戳重排后的各段：开机加安全检查 349 s、交接 53 s、prep 脚本 15
    s、装环境 492 s、间隔 20 s、下载 49 s、间隔 2 s、起服务 666
    s（其中解析配置 194 s、加载权重 50 s、init engine 393 s、其余 29
    s）。各段加起来是 1,646 s，和开机到就绪的墙钟时间一致。
- about 页草稿：开头原本写着"§7.10.3 我还没改"，已改成"已加更正说明"。

about 页草稿和 X3 补丁先放着：about 页要等用户批了才上线，X3 要等 infra 和
Notes 定好 GPU 复测会话。我现在待命，不跑重任务。
```

### `20261113T185933Z_notes` (live)

- label: idle_done/none · prediction: waiting_reply/none (decided by model)

```text
Live commit: 08bbc4b
  Result: Switch rolled back; code stays live on the
    GPU backend
  Before each deploy, the merged tree passed the full
  suite (pytest 377 / 386 / 393 / 404, 0 failures, one
  known deselect each time). Each runbook has its
  deploy record.
  GPU plan (infra §9.4). I confirm the two script
  changes it gives me on gpu/g1-v2:
  1. Before session 1: model-serve.sh exports the
     build caches under /mnt/gpu-cache:
     TRITON_CACHE_DIR, VLLM_CACHE_ROOT and
     TORCHINDUCTOR_CACHE_DIR. §5 says "two variables"
     but §9.3 lists three; I'll do three.
  2. Before session 2: prep tries the tarball first,
     checking in the §9.3 order, and on any failure
     cleans up and installs from scratch the current
     way. It will be tested locally with a fake blob.
     §9.4 asks for "hit plus four fallbacks", but
     §9.3's checks give five failure reasons (version,
     manifest, part, whole stream, import versions),
     so I'll test all five.
  The joint schedule and command list come next. The
  SERVES change stays in the same change set and
  merges only after Menu signs the contract.
```

### `20261113T191540Z_notes` (live)

- label: idle_done/none · prediction: waiting_reply/none (decided by model)

```text
│                │           │         │ public site; player hidden   │
  │                │           │         │ (no episode registered)      │
  ├────────────────┼───────────┼─────────┼──────────────────────────────┤
  │                │ code at   │         │ Switch rolled back; code     │
  │ cpu-embed      │ 11:17;    │ 08bbc4b │ stays live on the GPU        │
  │                │ switch    │         │ backend                      │
  │                │ 18:54     │         │                              │
  └────────────────┴───────────┴─────────┴──────────────────────────────┘
  Before each deploy, the merged tree passed the full suite (pytest 377 / 386
  / 393 / 404, 0 failures, one known deselect each time). Each runbook has
  its deploy record.
  GPU plan (infra §9.4). I confirm the two script changes it gives me on
  gpu/g1-v2:
  1. Before session 1: model-serve.sh exports the build caches under
     /mnt/gpu-cache: TRITON_CACHE_DIR, VLLM_CACHE_ROOT and
     TORCHINDUCTOR_CACHE_DIR. §5 says "two variables" but §9.3 lists three;
     I'll do three.
  2. Before session 2: prep tries the tarball first, checking in the §9.3
     order, and on any failure cleans up and installs from scratch the
     current way. It will be tested locally with a fake blob. §9.4 asks for
     "hit plus four fallbacks", but §9.3's checks give five failure reasons
     (version, manifest, part, whole stream, import versions), so I'll test
     all five.
  The joint schedule and command list come next. The SERVES change stays in
  the same change set and merges only after Menu signs the contract.
```

### `staged_20261113T185716Z_stage` (staged)

- label: idle_done/none · prediction: needs_human/low_confidence (decided by model)

```text
▐▛███▛█   Claude Code v2.1.322
▝▜██████▀  Haiku 5.5 · Claude Ultra
 ▝▝   ▝▝   ~/stage_tmp
  Fable 5.2 is here for your longest and toughest coding tasks.
  It draws down usage faster than Opus 5.5. Switch with /model.
                                             ◐ medium · /effort
```

### `staged_20261113T185719Z_stage` (staged)

- label: idle_done/none · prediction: needs_human/low_confidence (decided by model)

```text
▐▛███▛█   Claude Code v2.1.322
▝▜██████▀  Haiku 5.5 · Claude Ultra
 ▝▝   ▝▝   ~/stage_tmp
  Fable 5.2 is here for your longest and toughest coding tasks.
  It draws down usage faster than Opus 5.5. Switch with /model.
                                             ◐ medium · /effort
```

### `staged_20261113T185722Z_stage` (staged)

- label: idle_done/none · prediction: needs_human/low_confidence (decided by model)

```text
▐▛███▛█   Claude Code v2.1.322
▝▜██████▀  Haiku 5.5 · Claude Ultra
 ▝▝   ▝▝   ~/stage_tmp
  Fable 5.2 is here for your longest and toughest coding tasks.
  It draws down usage faster than Opus 5.5. Switch with /model.
                                   Ctrl+Y to paste deleted text
```

### `staged_20261113T194434Z_stage` (staged)

- label: needs_human/spend · prediction: waiting_reply/none (decided by model)

```text
Summary of the options. Option A reuses the current VM and is free. Option
B rents a larger VM at about 4 dollars a day and finishes sooner. I
recommend B. Nothing is provisioned yet; B will only be provisioned on your
say-so.
```

# 第二次评估（看过留出集之后）

这组数是在看过留出集结果后得到的，只能当参考。

Three changes, each decided from the design doc and the tuning set only (no holdout probabilities were used to pick a threshold):

1. Tripwire and model are OR-ed again (design doc 4.4): a tripwire hit, or any wants_* over the threshold, gives needs_human.
2. Threshold selection: among settings tied on recall, take the lowest wants threshold, floor 0.2 (design value); on the tuning set this gives 0.2.
3. New layer-0 rule `empty_session`: a freshly started session with no conversation is idle_done and is not sent to the model.

- Holdout: `data/holdout/holdout.jsonl`, 45 distinct screens (29 live, 16 staged), 10 labeled needs_human.
- sha256: `9021befd69250ab62fff234c185952b05e1bfc4251a72a5c75b9aa21b1744b57`
- Policy: tripwire_mode=escalate, wants_threshold=0.2, turn_end_min_confidence=0.3.
- Layer 1 was needed for 20/45 screens; the rest were decided by layer-0 rules without a model call.

## Headline

- needs_human caught: **10/10** (rules + Gemini).
- Same final decision (state and reason) on two runs: **45/45**.
- Escalation rate: **16/45** screens flagged needs_human.

## Three schemes

| scheme | nh recall | nh precision | recall live | precision live | recall staged | precision staged | 5-class correct | reason correct (of caught) |
|---|---|---|---|---|---|---|---|---|
| rules only (tripwire escalate) | 7/10 | 7/9 | 2/2 | 2/3 | 5/8 | 5/6 | 35/45 | 6/7 |
| rules only (tripwire hint) | 1/10 | 1/1 | 1/2 | 1/1 | 0/8 | 0/0 | 30/45 | 1/1 |
| rules + Gemini Decision 1 | 10/10 | 10/16 | 2/2 | 2/7 | 8/8 | 8/9 | 38/45 | 8/10 |

Precision `0/0` means the scheme flagged nothing in that slice.

## Confusion matrix (rules + Gemini, run 1)

rows = label, columns = prediction

| label \ pred | busy | idle_done | waiting_reply | needs_human | dropped_to_shell |
|---|---|---|---|---|---|
| busy | 17 | 0 | 0 | 0 | 0 |
| idle_done | 0 | 5 | 1 | 2 | 0 |
| waiting_reply | 0 | 0 | 2 | 4 | 0 |
| needs_human | 0 | 0 | 0 | 10 | 0 |
| dropped_to_shell | 0 | 0 | 0 | 0 | 4 |

## Cost and latency

- Model calls: 20 per run, 2 runs; latency per call median 0.29 s, p95 0.44 s (n=40).
- Layer 0 (rules) per screen: 0.4 ms on average.
- Tokens: run 1 17659, run 2 17659 (input only; the API reports 0 output tokens).
- Money: the Decisions API response has no cost field, so no dollar figure is reported here.

## Every wrong prediction (rules + Gemini, run 1)

9 of 45 differ from the label in state or reason.

### `20261113T183900Z_infra` (live)

- label: waiting_reply/none · prediction: needs_human/deploy (decided by model)

```text
│                           │ as "end unknown" and blocks                 │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ Notes re-embedding the    │                                             │
  │ live DB (after p2p and    │ Not run                                     │
  │ seismic)                  │                                             │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ infra cargo build/test    │ Not registered, not run                     │
  │ (after p2p)               │                                             │
  └───────────────────────────┴─────────────────────────────────────────────┘
  So four sessions are each waiting for the row ahead to be struck, and none of
  them has run. Only Quorum ran one job at 15:50, alongside these rows.
  Two problems with the table rules:
  - Expired rows that nobody strikes. The books row ended at 10:45 but stayed in
    the table, and every "wait until it's struck" condition, mine included,
    waited on it all day.
  - The same kind of open end time I just fixed in my own row ("开跑后约 20
    min") has appeared in seismic's row. The lease reserve in L0 refuses both
    cases: it requires a specific end time, and a reservation stops counting
    once its end time passes. This is one more reason to make L0's switch the
    first migration step.
  What I'm not doing: I won't edit other sessions' rows and won't jump ahead of
  p2p to compile. I also haven't run any of R1–R5; they're still waiting for
  your confirmation.
  Decisions for you:
  1. Ask books to strike its row, or confirm the window is over, so p2p,
     seismic, Notes and infra can start in order. Alternatively, rearrange the
     order for tonight.
  2. Should I restart the watcher? Its condition is unchanged: books and p2p
     rows both struck, and load below 2.5.
  3. Confirm R1–R5. In the meantime I'll write gpu/image-t1.sh and its tests on
     a branch; that's light bash and doesn't count as a heavy job.
```

### `20261113T184513Z_quorum` (live)

- label: waiting_reply/none · prediction: needs_human/deploy (decided by rule)

```text
- 合计 ≤ 141 个请求，全部只发给 jepsen.io 这一个站。
  - PDF 数：已经确认前缀内至少有 32 个报告 PDF（从 Showcase
    现有数据扫出来的，没发请求）。B 最多下载并解析 25
    份，其余不请求。体积、能不能用满 25 份都是估计，没核实。
  - seed 草案：起始页 /analyses，前缀
    https://jepsen.io/。前缀只用 /analyses/
    的话，可爬的页大概凑不到 SeedValidator 要求的 200 个。
  - P2-9 上线之前，线上的 seed 列表不加 jepsen。
  3. P2-9：等 C7 和 P2-8 都做完，给你 diff 再批。
  4. 两件旧事
  - P1 修复上线后的验证记录：在 ~/projects/_docs/_plan/quorum_fet
    ch_cache_contract_2026-11-12.md §十五-补（第 297
    行起）。结论：部署的那份 class 在测试实例上 16/16 条事件带
    worker 标签，修复前的版本 0/16；线上从 04:49 到 10:49
    没有访客跑过 Live。那一节标题还写着"未上线"，已改成"已上线
    04:47 UTC"。
  - 冷启动去掉固定等待：~/projects/_docs/_plan/quorum_startup_rea
    dy_plan_2026-11-13.md。方案写完了（目标 24.5 s → 约 9
    s，判定线是冷启动 3 次的中位数 ≤ 10 s），代码没动，按排期放在
    K2 之前。
  - 读超时补齐：~/projects/_docs/_plan/quorum_read_timeouts_plan_
    2026-11-13.md。方案写完了（10
    类调用、先量再定取值、黑洞单测、HW2–HW9
    全量回归前后对比、一次故障注入），代码没动，按排期在 P2
    上线之后、K2 之前，约 1 天，分支 demo/v3-t7。
```

### `20261113T185933Z_notes` (live)

- label: idle_done/none · prediction: needs_human/deploy (decided by model)

```text
Live commit: 08bbc4b
  Result: Switch rolled back; code stays live on the
    GPU backend
  Before each deploy, the merged tree passed the full
  suite (pytest 377 / 386 / 393 / 404, 0 failures, one
  known deselect each time). Each runbook has its
  deploy record.
  GPU plan (infra §9.4). I confirm the two script
  changes it gives me on gpu/g1-v2:
  1. Before session 1: model-serve.sh exports the
     build caches under /mnt/gpu-cache:
     TRITON_CACHE_DIR, VLLM_CACHE_ROOT and
     TORCHINDUCTOR_CACHE_DIR. §5 says "two variables"
     but §9.3 lists three; I'll do three.
  2. Before session 2: prep tries the tarball first,
     checking in the §9.3 order, and on any failure
     cleans up and installs from scratch the current
     way. It will be tested locally with a fake blob.
     §9.4 asks for "hit plus four fallbacks", but
     §9.3's checks give five failure reasons (version,
     manifest, part, whole stream, import versions),
     so I'll test all five.
  The joint schedule and command list come next. The
  SERVES change stays in the same change set and
  merges only after Menu signs the contract.
```

### `20261113T190827Z_books` (live)

- label: waiting_reply/none · prediction: needs_human/deploy (decided by model)

```text
HOT, because the seeded rows fill their pages.
    That's realistic for an append-only table, so A2's
    HOT column will show only a small difference; the
    wall time and WAL columns still carry the cost.
  - MANIFEST is updated.
  - New baseline.py remeasure: re-times only A2 and
    its noise band, merges the result in, and marks
    the baseline table as re-measured.
  - E13: the test's noise bands for write scripts can
    now be raised to the full-scale baseline bands.
  Also updated:
  - The plan (§十 #20), the checklist doc and the
    self-test record.
  - The candidate list: L1 rewrites can now be
    submitted, and N2a / N2b are marked "not submitted
    (agreed)".
  - The R6 draft has a "Not tried" sentence for N2a /
    N2b with the reason.
  Needs your approval: a second timed window of about
  45 minutes, any day after 11-14, for the full
  self-test suite. The A2 change feeds into every T2
  judgement, so E1–E12 have to be re-run after it too.
  Tomorrow's 45 minutes only covers the A2 re-measure
  and E13–E17. I'd only merge demo/v3-sql-cost after
  that full run is green.
  running
```

### `20261113T191540Z_infra` (live)

- label: waiting_reply/none · prediction: needs_human/deploy (decided by model)

```text
- check confirms the account and container are still private.
    - sas r|cw issues a token of at most 60 minutes. It refuses to write to a
      terminal and refuses to sign at all if check fails.
    - readback compares the uploaded blobs against the manifest.
    - pin records the manifest's sha256 in git.
  - gpu/image-t1-vm.sh runs on the GPU VM: scan, pack and upload (manifest
    uploaded last), plus fetch.
    - fetch implements the five checks in §9.3.
    - On any failure it deletes the half-unpacked tree and prints @@stage
      prep tarball=fallback reason=….
    - Notes can call it directly from prep, or treat it as the reference
      implementation of the format.
  - Tests: 51 pass against a fake az and a fake curl. They cover:
    - every fallback reason;
    - the SAS never appearing in any command's arguments;
    - a read-only SAS failing to upload.
  - Against real Azure: check passes (anonymous gets 409). A real 5-minute
    read SAS was issued and sent straight to /dev/null, which confirms the
    R4/R5 role assignments work.
  - Bug caught before commit: while writing scan I hit the grep | head +
    pipefail trap again, which would have read a hit as clean. It's fixed,
    and a 50-hit test covers it.
  Decision for you: can I push gpu/image-t1? It also needs to go to Notes
  with two notes:
  - fetch is available to call from prep.
  - Session 1 needs TRITON_CACHE_DIR, VLLM_CACHE_ROOT and
    TORCHINDUCTOR_CACHE_DIR exported to /mnt/gpu-cache/… in model-serve.sh.
```

### `20261113T191540Z_notes` (live)

- label: idle_done/none · prediction: waiting_reply/none (decided by model)

```text
│                │           │         │ public site; player hidden   │
  │                │           │         │ (no episode registered)      │
  ├────────────────┼───────────┼─────────┼──────────────────────────────┤
  │                │ code at   │         │ Switch rolled back; code     │
  │ cpu-embed      │ 11:17;    │ 08bbc4b │ stays live on the GPU        │
  │                │ switch    │         │ backend                      │
  │                │ 18:54     │         │                              │
  └────────────────┴───────────┴─────────┴──────────────────────────────┘
  Before each deploy, the merged tree passed the full suite (pytest 377 / 386
  / 393 / 404, 0 failures, one known deselect each time). Each runbook has
  its deploy record.
  GPU plan (infra §9.4). I confirm the two script changes it gives me on
  gpu/g1-v2:
  1. Before session 1: model-serve.sh exports the build caches under
     /mnt/gpu-cache: TRITON_CACHE_DIR, VLLM_CACHE_ROOT and
     TORCHINDUCTOR_CACHE_DIR. §5 says "two variables" but §9.3 lists three;
     I'll do three.
  2. Before session 2: prep tries the tarball first, checking in the §9.3
     order, and on any failure cleans up and installs from scratch the
     current way. It will be tested locally with a fake blob. §9.4 asks for
     "hit plus four fallbacks", but §9.3's checks give five failure reasons
     (version, manifest, part, whole stream, import versions), so I'll test
     all five.
  The joint schedule and command list come next. The SERVES change stays in
  the same change set and merges only after Menu signs the contract.
```

### `staged_20261113T190159Z_stage` (staged)

- label: needs_human/spend · prediction: needs_human/deploy (decided by model)

```text
No human action is needed for this session. All tests pass. One
thing remains: the storage account has to be created, which
costs about 0.14 dollars a month, and I will create it once you
approve.
```

### `staged_20261113T190216Z_stage` (staged)

- label: idle_done/none · prediction: needs_human/deploy (decided by rule)

```text
Yesterday's deploy went fine and the public site checks pass.
The old branch was deleted last week. The storage cost is 0.14
dollars a month. Nothing is left to do on this task.
```

### `staged_20261113T194359Z_stage` (staged)

- label: needs_human/spend · prediction: needs_human/deploy (decided by model)

```text
镜像打包脚本已写好，51
个测试通过。下一步要在云上建一个存储账户放镜像，每月约 0.14
美元。这是一笔持续的费用，我等你批准后再建。
```

## Red / yellow split of what was reported

这也是看过留出集之后的数。The dashboard splits reported needs_human into red (needs you) and yellow (to check), red_threshold=0.85 chosen on the tuning set. The reported set and its recall are unchanged.

- Of the 16 reported: red 9, yellow 7.
- Red that truly need a human: 9/9.
- Yellow that truly need a human: 1/7.
