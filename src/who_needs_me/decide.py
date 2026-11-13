"""Layer 1: one call to a decision model per screen, all questions at once.

Backend: Gemini Decisions API (``gemini-decision-1``). The API key is read from the
``GEMINI_API_KEY`` environment variable at call time and is never written anywhere.
Results are cached in ``data/runs/<name>.jsonl`` so tuning never re-calls the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

import yaml

from who_needs_me.rules import CONFIG_PATH, Layer0

FACT_KEYS = ("pane_command", "background_task", "screen_unchanged", "last_error_line")

Transport = Callable[[str, dict, dict, float], tuple[int, dict]]


@cache
def load_layer1(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["layer1"]


def build_input(l0: Layer0) -> str:
    """The model sees only the trimmed last message and code-computed receipts."""
    facts = {k: l0.facts[k] for k in FACT_KEYS if l0.facts.get(k) not in (None, "")}
    return json.dumps({"last_message": l0.last_message, "facts": facts}, ensure_ascii=False)


def build_body(input_text: str, cfg: dict) -> dict:
    return {"model": cfg["gemini"]["model"], "input": input_text, "questions": cfg["questions"]}


def http_transport(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def call(
    input_text: str,
    cfg: dict | None = None,
    transport: Transport = http_transport,
    retries: int = 2,
) -> dict:
    """One decision call. Returns {answers, usage, latency_s, status}."""
    cfg = cfg or load_layer1()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    headers = {"x-goog-api-key": key, "content-type": "application/json"}
    body = build_body(input_text, cfg)
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        status, data = transport(cfg["gemini"]["url"], headers, body, cfg["gemini"]["timeout_s"])
        latency = time.perf_counter() - t0
        if status == 200:
            return {
                "answers": data["answers"],
                "usage": data.get("usage", {}),
                "latency_s": round(latency, 4),
                "status": status,
            }
        if status not in (429, 500, 502, 503, 504) or attempt == retries:
            msg = json.dumps(data)[:300]
            raise RuntimeError(f"decision API returned {status}: {msg}")
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def input_sha(input_text: str, cfg: dict) -> str:
    blob = json.dumps(build_body(input_text, cfg), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def read_run(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r for r in rows}


def run_batch(
    items: dict[str, Layer0],
    out: Path,
    cfg: dict | None = None,
    transport: Transport = http_transport,
    reuse: bool = True,
) -> dict[str, dict]:
    """Call the model for every item not already in ``out`` (same id and same input)."""
    cfg = cfg or load_layer1()
    done = read_run(out) if reuse else {}
    todo = {}
    for id_, l0 in items.items():
        text = build_input(l0)
        sha = input_sha(text, cfg)
        if done.get(id_, {}).get("input_sha") != sha:
            todo[id_] = (text, sha)

    def one(id_: str) -> dict:
        text, sha = todo[id_]
        return {
            "id": id_,
            "input_sha": sha,
            "model": cfg["gemini"]["model"],
            **call(text, cfg, transport),
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(cfg["gemini"]["max_workers"]) as pool:
        results = list(pool.map(one, sorted(todo)))
    with out.open("a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    done.update({r["id"]: r for r in results})
    return {k: done[k] for k in items}
