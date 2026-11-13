# who-needs-me

When several AI coding sessions run in parallel tmux panes, one board tells you **which
one is waiting on you, and for what**. Code decides what code can decide; a small decision
model reads only the last thing the session said; deploy, spend and delete always go to a
human.

This is not a software factory. It is one part of one: the escalation step that says when
a human has to look, plus the measurement loop that keeps that step honest.

![board](docs/board.png)

## How it decides

```
tmux screen (with colors)
   │
 layer 0  code — receipts, not the screen's own story          src/who_needs_me/rules.py
   │  pane runs bash, not claude            → dropped_to_shell
   │  "/login" or "API Error" in last lines → needs_human: machine
   │  "Yes, I trust this folder" dialog     → needs_human: machine
   │  spinner "Word… (" / "esc to interrupt"→ busy
   │  welcome banner, no conversation yet   → idle_done
   │  dim ESC[2m text in the input box      → stripped, ghost_text_ignored
   │  unchanged N min + background shell    → fact
   │  keyword tripwire on the last message  → needs_human (OR-ed with the model)
   │
 layer 1  Gemini Decision 1, one call, five questions           src/who_needs_me/decide.py
   │  turn_end: reporting_done / asking_question / awaiting_approval /
   │            waiting_on_background / unclear
   │  wants_deploy / wants_spend / wants_delete / wants_machine (yes-or-no probabilities)
   │
 layer 2  policy in code                                        src/who_needs_me/policy.py
      busy · idle_done · waiting_reply · dropped_to_shell · needs_human
      needs_human is shown red (needs you) or yellow (to check), and stays on the board
      until a human dismisses it
```

Every threshold, rule switch, keyword and question text lives in
[`factory.yaml`](factory.yaml). The classifier only reads that file.

Each decision records `decided_by` (rule or model) and an `evidence` line that **code**
picks from the screen. The model never quotes text. The model only sees the trimmed last
message and the receipts code computed: pane command, background task, how long the
screen has been unchanged, last error line. Screens are redacted before any call.

## Results

Full tables, confusion matrix and every wrong prediction: [`eval.md`](eval.md).
All numbers are "k of n"; the holdout is small.

Data: 153 labeled screens from real sessions plus deliberately staged ones (`source: live`
/ `staged`, reported separately). Duplicate captures of stalled screens were removed,
leaving 89 distinct screens: 44 for tuning and 45 locked as the holdout.

**First evaluation (policy fixed in advance), holdout of 45:**

| scheme | needs_human caught | flagged that were right | 5-class |
|---|---|---|---|
| rules only, tripwire escalates | 7/10 | 7/9 | 35/45 |
| rules only, tripwire as hint | 1/10 | 1/1 | 30/45 |
| rules + Gemini Decision 1 | **8/10** | 8/11 | 37/45 |

It missed two. One was a real stalled session ("about 页要等用户批了才上线"): the tripwire
caught it, but in that policy the tripwire was only a hint. The thresholds had been chosen
by "fewest false alarms" on a tuning set with only 8 positives, which pushed them to the
edge of the grid.

**Second evaluation (made after seeing the first; reference only):** tripwire OR model
again, as in the original design; the lowest threshold among recall ties (0.2); and a rule
for freshly started empty sessions. Result: **10/10 caught**, at the cost of 6 false alarms
instead of 3, and an escalation rate of 16/45 instead of 11/45. Split for the board: of the
16 reported, 9 red (9/9 truly need a human) and 7 yellow (1/7).

Both runs of the model gave the same final decision on 45/45 screens, in both evaluations.
Model call latency: median 0.29 s, p95 0.44 s; rules: under 1 ms per screen. 18–20k input
tokens per 45-screen run; the API returns no cost field, so no dollar figure is given.

## Holdout lock

The evaluation holdout is `data/holdout/holdout.jsonl`: 45 distinct screens drawn from
89 (153 labels minus 64 duplicate captures of stalled screens), stratified by
(state, reason, source), seed `20261113` in `src/who_needs_me/split.py`.

```
sha256(data/holdout/holdout.jsonl) = 9021befd69250ab62fff234c185952b05e1bfc4251a72a5c75b9aa21b1744b57
```

Thresholds were chosen by `python -m who_needs_me.tune`, which reads only
`data/tuning.jsonl`. `eval.md` holds two evaluations on the same holdout: the first with
the policy fixed in advance (kept verbatim, frozen in `data/eval_v1.md`), and a second one
made **after seeing the first** (three changes from the design doc and tuning set; for
reference only).

## Run it

```bash
uv sync
uv run pytest                                   # 94 tests; one per layer-0 rule
uv run ruff check .

# replay the labeled day on the board (time slider), no tmux needed
export GEMINI_API_KEY=...                        # or add --no-model for rules only
uv run python -m who_needs_me.board --captures data/screens --replay
# open http://127.0.0.1:8765

# live: read-only capture every 30 s, then the board on the capture directory
uv run python -m who_needs_me.capture --ssh user@host --out captures --every 30
uv run python -m who_needs_me.board --captures captures

# reproduce the numbers (reuses cached model answers in data/runs/)
uv run python -m who_needs_me.tune
uv run python -m who_needs_me.eval
```

`capture.py` can only run `tmux list-panes` and `tmux capture-pane`; any other tmux
subcommand, `send-keys` included, is refused before a process starts (tested). It never
types into a session.

## Data and privacy

- `data/screens/`: redacted captures. Internal IPs, hostnames, emails, tokens, usernames
  (including those inside OSC 8 hyperlinks and Claude Code's path slugs) are replaced;
  colors are kept. The labeling tool refuses to start if anything still looks sensitive.
- `data/labels.jsonl`: append-only labels; corrections are new rows.
- `data/runs/`: cached model answers, so tuning and evaluation re-run without new calls.
- Raw captures, live captures (`captures/`) and the API key never enter the repo.

## Who did what

- **Human (the author):** the design, the labels for all 153 screens and the labeling
  conventions, staging the missing cases, every threshold-selection rule, the decision to
  publish the first evaluation unchanged, and what got merged.
- **Agent (Claude Code):** the code, the tests, and running the tools. Every model call and
  every number in `eval.md` comes from the scripts in this repo.

## Limits

- The holdout has 10 needs_human screens and two evaluations ran on it; the second is not
  an unbiased estimate.
- Staged screens are real screens, but staged. Live and staged are reported separately.
- One decision model only (Gemini Decision 1). The layer-1 function is backend-agnostic;
  a shadow second model was planned but not built.
- Autonomy level: L1. It only reads and escalates; it never answers for you.
