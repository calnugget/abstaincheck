"""Grade the saved free-text replies with a grader model, and optionally write the reference
rows the app compares against.

    OPENAI_API_KEY=... python3 scripts/grade_free.py --judge openai:gpt-5.4-mini
    OPENAI_API_KEY=... python3 scripts/grade_free.py --judge openai:gpt-5.4-mini --write-baselines

Grades are cached in abstaincheck/data/free_grades/<judge>.jsonl, so re-running costs nothing
for replies already graded. --sample-only grades just the hand-labelling sample, which is how
candidate graders are compared before one is chosen.
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from abstaincheck.bank import DATA, Bank  # noqa: E402
from abstaincheck.judge import RUBRIC_VERSION, Judge, to_answer  # noqa: E402

KEY_ENV = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY",
           "anthropic": "ANTHROPIC_API_KEY"}
NAMES = {"deepseek-chat": "DeepSeek-Chat", "gpt-5.4-mini": "GPT-5.4-mini",
         "gemini-pro-latest": "Gemini Pro", "gemini-2.5-pro": "Gemini 2.5 Pro", "claude-opus-4-7": "Claude Opus 4.7",
         "claude-haiku-4-5-20251001": "Claude Haiku 4.5"}


def load_replies():
    out = []
    for f in sorted((DATA / "free_runs").glob("*/*.jsonl")):
        out += [json.loads(l) for l in open(f)]
    return out


def slug(spec):
    return spec.replace(":", "__").replace("/", "_") + f"__v{RUBRIC_VERSION}"


async def main(a):
    provider, model = a.judge.split(":", 1)
    key = os.environ.get(KEY_ENV[provider])
    if not key:
        sys.exit(f"set {KEY_ENV[provider]}")
    bank = Bank()
    cache_f = DATA / "free_grades" / f"{slug(a.judge)}.jsonl"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if cache_f.exists():
        for l in open(cache_f):
            g = json.loads(l)
            cache[(g["format"], g["model"], g["id"])] = g["grade"]
    rows = load_replies()
    if a.sample_only:
        sample = json.loads((DATA / "label_sample.json").read_text())
        want = {(s["format"], s["model"], s["id"]) for s in sample}
        rows = [r for r in rows if (r["format"], r["model"], r["id"]) in want]
    todo = [r for r in rows if (r["format"], r["model"], r["id"]) not in cache]
    print(f"{len(rows)} replies, {len(todo)} to grade with {a.judge}", flush=True)
    judge = Judge(provider, model, key, concurrency=a.concurrency)
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        grades = await judge.grade_many([(bank.by_id[r["id"]], r["reply"]) for r in chunk])
        with open(cache_f, "a") as f:
            for r, g in zip(chunk, grades):
                if g is None and r["reply"].strip():
                    print(f"  ungraded {r['format']} {r['model']} {r['id']}", flush=True)
                    continue   # retried on the next run
                cache[(r["format"], r["model"], r["id"])] = g
                f.write(json.dumps({"format": r["format"], "model": r["model"], "id": r["id"],
                                    "grade": g}) + "\n")
        print(f"  {min(i + 100, len(todo))}/{len(todo)}", flush=True)

    if a.write_baselines:
        out = {}
        panel_ids = {it["id"] for it in bank.panel}
        for r in load_replies():
            k = (r["format"], r["model"], r["id"])
            if k not in cache or r["id"] not in panel_ids:
                continue
            ans, warned = to_answer(cache[k])
            m = out.setdefault(r["format"], {}).setdefault(r["model"], {
                "name": NAMES.get(r["model"], r["model"]), "method": "single reply, temperature 0 "
                "where accepted", "grader": judge.name, "answers": {}})
            m["answers"][r["id"]] = {"answer": ans, "confidence": None, "warned": warned}
        for fmt, ms in out.items():
            for mk, m in ms.items():
                print(f"{fmt:10s} {mk:20s} {len(m['answers'])} graded answers")
        (DATA / "baselines_free.json").write_text(json.dumps(out))
        print("wrote", DATA / "baselines_free.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", required=True, help="provider:model")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--sample-only", action="store_true")
    ap.add_argument("--write-baselines", action="store_true")
    asyncio.run(main(ap.parse_args()))
