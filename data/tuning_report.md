# Tuning report (tuning set only)

44 distinct screens, 8 of them needs_human.

| scheme | wants / min conf | nh recall | nh precision | false nh | 5-class |
|---|---|---|---|---|---|
| rules only (escalate) | — | 6/8 | 6/10 | 4 | 33/44 |
| rules only (hint) | — | 2/8 | 2/2 | 0 | 31/44 |
| rules + Gemini (escalate), chosen | 0.2 / 0.3 | 8/8 | 8/17 | 9 | 35/44 |

## Sweep (rules + Gemini, escalate, min conf 0.3)

| mode | wants / min conf | nh recall | nh precision | false nh | 5-class |
|---|---|---|---|---|---|
| escalate | 0.2 / 0.3 | 8/8 | 8/17 | 9 | 35/44 |
| escalate | 0.3 / 0.3 | 8/8 | 8/15 | 7 | 36/44 |
| escalate | 0.4 / 0.3 | 8/8 | 8/14 | 6 | 37/44 |
| escalate | 0.5 / 0.3 | 8/8 | 8/14 | 6 | 37/44 |
| escalate | 0.6 / 0.3 | 8/8 | 8/14 | 6 | 37/44 |
| escalate | 0.7 / 0.3 | 8/8 | 8/13 | 5 | 38/44 |
| escalate | 0.8 / 0.3 | 8/8 | 8/13 | 5 | 38/44 |
| escalate | 0.9 / 0.3 | 8/8 | 8/12 | 4 | 39/44 |

**chosen** (lowest_wants_among_max_recall, floor 0.2): tripwire_mode=escalate, wants_threshold=0.2, turn_end_min_confidence=0.3

## Red threshold (display tier only)

| red_threshold | red | red that truly need a human | yellow |
|---|---|---|---|
| 0.5 | 13 | 8/13 | 4 |
| 0.6 | 13 | 8/13 | 4 |
| 0.7 | 12 | 8/12 | 5 |
| 0.8 | 11 | 8/11 | 6 |
| 0.85 | 10 | 8/10 | 7 |
| 0.9 | 10 | 8/10 | 7 |
| 0.95 | 10 | 8/10 | 7 |

**chosen red_threshold**: 0.85
