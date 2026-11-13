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
