"""Run the paper's models on the free-text panel and save their raw replies.

    DEEPSEEK_API_KEY=... python3 scripts/run_free_baselines.py --format realistic \
        --model deepseek:deepseek-chat [--limit 10]

Keys come from the environment only (DEEPSEEK_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
ANTHROPIC_API_KEY) and are never written anywhere. Replies go to
abstaincheck/data/free_runs/<format>/<model>.jsonl, which is not committed. The run resumes:
items already answered are skipped. Grading is a separate step (grade_free.py), so a reply is
paid for once however many graders read it.
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from abstaincheck.bank import DATA, Bank  # noqa: E402
from abstaincheck.modes import FREE_TEXT, render_free  # noqa: E402
from abstaincheck.providers import call, client  # noqa: E402

KEY_ENV = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY",
           "anthropic": "ANTHROPIC_API_KEY"}


async def main(a):
    provider, model = a.model.split(":", 1)
    key = os.environ.get(KEY_ENV[provider])
    if not key:
        sys.exit(f"set {KEY_ENV[provider]}")
    out = DATA / "free_runs" / a.format / f"{model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["id"] for l in open(out)} if out.exists() else set()
    items = [it for it in Bank().panel if it["id"] not in done][: a.limit or None]
    print(f"{model} {a.format}: {len(done)} done, {len(items)} to go", flush=True)
    sem, tok_in, tok_out, fails = asyncio.Semaphore(a.concurrency), 0, 0, 0

    async with client(timeout=a.timeout) as c:
        async def one(it):
            async with sem:
                for attempt in range(3):
                    try:
                        return it, await call(c, provider, model, key, render_free(it, a.format),
                                              max_tokens=a.max_tokens)
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            return it, RuntimeError(f"{type(e).__name__}: {e}")
                        await asyncio.sleep(3 * (attempt + 1))

        with open(out, "a") as f:
            for n, fut in enumerate(asyncio.as_completed([one(it) for it in items]), 1):
                it, res = await fut
                if isinstance(res, Exception):
                    fails += 1
                    print(f"  fail {it['id']}: {str(res)[:120]}", flush=True)
                    continue
                text, usage = res
                ti = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
                to = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
                tok_in += ti
                tok_out += to
                f.write(json.dumps({"id": it["id"], "format": a.format, "provider": provider,
                                    "model": model, "reply": text, "tokens_in": ti,
                                    "tokens_out": to}) + "\n")
                f.flush()
                if n % 20 == 0:
                    print(f"  {n}/{len(items)}", flush=True)
    print(f"done: {len(items) - fails} saved, {fails} failed, tokens in {tok_in}, out {tok_out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=FREE_TEXT, required=True)
    ap.add_argument("--model", required=True, help="provider:model, e.g. deepseek:deepseek-chat")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=float, default=300.0)
    asyncio.run(main(ap.parse_args()))
