"""Dashboard backend: captures -> layer 0 -> layer 1 -> policy -> status, plus a tiny server.

- Reads a directory of captures (``capture.py`` output, or ``data/screens`` for replay).
- Screens are redacted in memory before anything is sent to the decision model.
- ``needs_human`` is sticky: once raised it stays on the board, whatever the session
  does next, until a human dismisses it. A dismissal covers that exact screen content;
  new content that needs a human raises it again.
- One page shows both live and history: the slider's right end is live, and dragging it
  left shows the board as it was at that capture time (``--history-hours``, default 6).
- Replay mode (``--replay``) is the same slider over a fixed set of captures, with no live end.

    uv run python -m who_needs_me.board --captures captures/            # live + history
    uv run python -m who_needs_me.board --captures data/screens --replay  # demo
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from who_needs_me.decide import build_input, http_transport, input_sha, load_layer1, run_batch
from who_needs_me.policy import decide, load_policy, needs_model
from who_needs_me.redact import harvest_identifiers, load_local_denylist, redact, scan
from who_needs_me.rules import TS_FMT, content_key, evaluate, load_config, stale_minutes

PAGE = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
AT_RE = re.compile(r"^\d{8}T\d{6}Z$")
_ORDER = ("red", "yellow", "dropped_to_shell", "waiting_reply", "idle_done", "busy")
ORDER = {k: i for i, k in enumerate(_ORDER)}


def rank(row: dict) -> str:
    """red / yellow for needs_human, otherwise the state."""
    if row["state"] == "needs_human":
        return row.get("tier") or "red"
    return row["state"]


class Board:
    def __init__(
        self,
        captures: Path,
        state_dir: Path,
        use_model: bool = True,
        denylist: Path = Path(".redact.local"),
        transport: Callable = http_transport,
        settle_seconds: float = 3.0,
    ):
        self.captures = captures
        self.state_dir = state_dir
        self.use_model = use_model
        self.denylist = denylist
        self.transport = transport
        self.settle = settle_seconds
        self.lock = threading.Lock()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.sticky_path = state_dir / "sticky.json"
        self.acks_path = state_dir / "acks.jsonl"
        self.sticky: dict[str, dict] = (
            json.loads(self.sticky_path.read_text()) if self.sticky_path.exists() else {}
        )
        # Incremental index of captures. A capture file never changes once written, so its
        # content key is computed once; a long-running board does not re-read old files.
        self._seen: set[str] = set()
        self._index: dict[str, list[tuple[str, str, object]]] = defaultdict(list)  # ts, key, Screen

    # ---------------------------------------------------------------- data

    def _refresh_index(self) -> None:
        for s in scan(self.captures, self.settle):
            if s.id in self._seen or not s.ts or s.source != "live":
                continue
            self._seen.add(s.id)
            bisect.insort(
                self._index[s.session], (s.ts, content_key(s.screen()), s), key=lambda e: e[0]
            )

    def timeline(self, hours: float = 0) -> list[str]:
        """All capture times, oldest first; ``hours`` > 0 keeps only the last that many hours."""
        with self.lock:
            self._refresh_index()
            times = sorted({ts for items in self._index.values() for ts, _, _ in items})
        if hours > 0 and times:
            newest = datetime.strptime(times[-1], TS_FMT)
            times = [
                t
                for t in times
                if (newest - datetime.strptime(t, TS_FMT)).total_seconds() <= hours * 3600
            ]
        return times

    def _acked(self) -> set[tuple[str, str]]:
        if not self.acks_path.exists():
            return set()
        rows = [json.loads(x) for x in self.acks_path.read_text().splitlines() if x.strip()]
        return {(r["session"], r["content_key"]) for r in rows}

    def snapshot(self, at: str | None = None, sticky: bool = True) -> dict:
        with self.lock:
            return self._snapshot(at, sticky)

    def _snapshot(self, at: str | None, use_sticky: bool) -> dict:
        self._refresh_index()
        by_session: dict[str, list] = {}
        for name, items in self._index.items():
            n = bisect.bisect_right(items, at, key=lambda e: e[0]) if at else len(items)
            if n:
                by_session[name] = items[:n]

        latest = {name: items[-1][2] for name, items in by_session.items()}
        texts = {s.id: s.screen() for s in latest.values()}
        extra = harvest_identifiers(texts.values()) | load_local_denylist(self.denylist)
        l0s, keys = {}, {}
        for name, items in by_session.items():
            s = latest[name]
            text = redact(texts[s.id], extra)
            series = [(ts, key) for ts, key, _ in items]
            l0s[name] = evaluate(text, s.pane_command(), stale_minutes(series), load_config())
            keys[name] = items[-1][1]

        # The model is asked once per distinct input, not once per capture: an idle session
        # produces a new capture every few seconds with the same last message.
        answers: dict[str, dict] = {}
        cfg1 = load_layer1()
        sha_of = {n: input_sha(build_input(l0), cfg1) for n, l0 in l0s.items() if needs_model(l0)}
        if self.use_model and sha_of:
            by_sha = run_batch(
                {sha: l0s[n] for n, sha in sha_of.items()},
                self.state_dir / "model_runs.jsonl",
                transport=self.transport,
            )
            answers = {latest[n].id: by_sha[sha] for n, sha in sha_of.items()}

        acked = self._acked()
        rows = []
        for name, l0 in l0s.items():
            s = latest[name]
            a = answers.get(s.id, {}).get("answers")
            d = decide(l0, a, load_policy())
            row = {
                "session": name,
                "capture": s.id,
                "captured_at": s.ts,
                "pane_command": l0.facts["pane_command"],
                **d.to_dict(),
                "facts": l0.facts,
                "last_message": l0.last_message,
                "model_called": a is not None,
                "latency_s": answers.get(s.id, {}).get("latency_s"),
                "sticky": None,
            }
            if use_sticky:
                self._apply_sticky(row, keys[name], acked)
            rows.append(row)
        if use_sticky and not at:
            self.sticky_path.write_text(json.dumps(self.sticky, indent=1))
        rows.sort(key=lambda r: (ORDER.get(rank(r), 9), r["session"]))
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            counts[rank(r)] += 1
        return {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of": at or (max((s.ts for s in latest.values()), default="")),
            "live": at is None,
            "model": self.use_model,
            "counts": dict(counts),
            "sessions": rows,
        }

    def _apply_sticky(self, row: dict, key: str, acked: set) -> None:
        name = row["session"]
        prompt_key = row["facts"].get("last_prompt_key", "")
        held = self.sticky.get(name)
        # A person typed a new prompt into the session after the flag was raised: the request
        # has been answered there, so the hold ends. Other activity (a background task waking
        # the session, a message from another session) does not end it.
        if held and prompt_key and prompt_key != held.get("prompt_key", ""):
            self.sticky.pop(name)
            row["released_by_reply"] = True
        if row["state"] == "needs_human" and (name, key) not in acked:
            cur = self.sticky.get(name)
            if not cur or cur["content_key"] != key:
                self.sticky[name] = {
                    "content_key": key,
                    "since": row["captured_at"],
                    "human_reason": row["human_reason"],
                    "evidence": row["evidence"],
                    "capture": row["capture"],
                    "tier": row.get("tier"),
                    "prompt_key": prompt_key,
                }
        held = self.sticky.get(name)
        if held and (name, held["content_key"]) in acked:
            self.sticky.pop(name)
            held = None
        if held:
            row["sticky"] = held
            if row["state"] != "needs_human":
                row["current_state"] = row["state"]
                row["state"] = "needs_human"
                row["human_reason"] = held["human_reason"]
                row["evidence"] = held["evidence"]
                row["tier"] = held.get("tier")

    def ack(self, session: str) -> bool:
        with self.lock:
            held = self.sticky.get(session)
            if not held:
                return False
            rec = {
                "session": session,
                "content_key": held["content_key"],
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            with self.acks_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            self.sticky.pop(session)
            self.sticky_path.write_text(json.dumps(self.sticky, indent=1))
            return True


# ---------------------------------------------------------------- server


def make_handler(board: Board, replay: bool, cache: dict, history_hours: float = 0):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            if path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/status.json":
                at = params.get("at")
                if at and not AT_RE.match(at):
                    return self._send(400, b"bad 'at'", "text/plain")
                # a past moment is shown as it was judged then: no held flags, nothing written
                snap = board.snapshot(at, sticky=False) if at else cache.get("snap")
                self._json(snap or board.snapshot())
            elif path == "/timeline.json":
                hours = 0 if replay else history_hours
                self._json({"replay": replay, "times": board.timeline(hours)})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if self.path != "/ack":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            ok = board.ack(str(body.get("session", "")))
            cache["snap"] = board.snapshot()
            self._json({"ok": ok})

        def log_message(self, *args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="who-needs-me dashboard")
    ap.add_argument("--captures", type=Path, default=Path("captures"))
    ap.add_argument("--state", type=Path, default=Path("state"))
    ap.add_argument("--every", type=float, default=30)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--replay", action="store_true", help="fixed captures only: slider without a live end"
    )
    ap.add_argument(
        "--history-hours",
        type=float,
        default=6,
        help="how far back the slider goes; 0 = everything",
    )
    ap.add_argument("--no-model", action="store_true", help="rules only, no API calls")
    args = ap.parse_args(argv)
    board = Board(args.captures, args.state, use_model=not args.no_model)
    cache: dict = {}

    def poll():
        while True:
            try:
                cache["snap"] = board.snapshot()
                (args.state / "status.json").write_text(json.dumps(cache["snap"], indent=1))
            except Exception as e:  # keep serving the last good snapshot
                print(f"poll failed: {e}")
            time.sleep(args.every)

    threading.Thread(target=poll, daemon=True).start()
    srv = ThreadingHTTPServer(
        (args.host, args.port), make_handler(board, args.replay, cache, args.history_hours)
    )
    print(f"board on http://{args.host}:{args.port}  (captures: {args.captures})")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
