"""Verified runs: AbstainCheck sends each question to the user's model.

The user's API key is used for the duration of the run only. It is never stored, logged or
returned, and it is not included in error messages. Requests go only to the provider hosts
in providers.py, so the server cannot be pointed at arbitrary addresses.

In the standard format each question goes out with the paper's prompt and the two-line answer
is read directly. In the free-text formats the question goes out as a person would ask it, and
the server's grader reads the reply afterwards.
"""
import asyncio
import hashlib
import time

import httpx

from .bank import render_prompt
from .modes import FREE_TEXT, render_free
from .providers import PROVIDERS, ProviderError, call, client
from .scoring import parse_response

CONCURRENCY = 6
MAX_SYSTEM_PROMPT = 8000

__all__ = ["PROVIDERS", "run"]


def _fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()[:10]


async def run(service, provider, model, key, size="quick", publish=False, display_name=None,
              transport=None, fmt="standard", system_prompt="", seed=None):
    """Async generator of progress events; the last one has type 'done' or 'error'."""
    if provider not in PROVIDERS:
        yield {"type": "error", "message": f"Unknown provider. Choose one of {sorted(PROVIDERS)}."}
        return
    if not key or not model:
        yield {"type": "error", "message": "A model name and an API key are both required."}
        return
    system_prompt = (system_prompt or "").strip()
    if len(system_prompt) > MAX_SYSTEM_PROMPT:
        yield {"type": "error", "message": f"The system prompt is longer than {MAX_SYSTEM_PROMPT} characters."}
        return
    free = fmt in FREE_TEXT
    try:
        test = service.start_test(size=size, model_name=display_name or model, mode="api_run",
                                  fmt=fmt, seed=seed)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return
    qs = test["questions"]
    hmap = service.store.get("tests", test["test_id"])["handles"]
    yield {"type": "start", "test_id": test["test_id"], "n_questions": len(qs), "format": fmt}

    def prompt_for(q):
        it = service.bank.by_id[hmap[q["handle"]]]
        return render_free(it, fmt) if free else render_prompt(it)

    replies, done, tokens, failures = {}, 0, 0, 0
    sem = asyncio.Semaphore(CONCURRENCY)
    started = time.time()

    async with client(transport) as c:
        # one probe call first, so a bad key or model name fails fast and cheaply
        first = qs[0]
        try:
            text, usage = await call(c, provider, model, key, prompt_for(first), system=system_prompt or None)
        except ProviderError as e:
            yield {"type": "error", "message": str(e)}
            return
        except httpx.HTTPError as e:
            yield {"type": "error", "message": f"Could not reach the provider: {type(e).__name__}"}
            return

        async def one(q):
            async with sem:
                for attempt in range(3):
                    try:
                        return q, await call(c, provider, model, key, prompt_for(q),
                                             system=system_prompt or None)
                    except (ProviderError, httpx.HTTPError) as e:
                        if attempt == 2:
                            return q, e
                        await asyncio.sleep(2 * (attempt + 1))

        def take(q, text, usage):
            nonlocal tokens
            replies[q["handle"]] = text
            tokens += int(usage.get("total_tokens") or
                          (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)

        take(first, text, usage)
        done = 1
        yield {"type": "progress", "done": done, "total": len(qs)}
        for fut in asyncio.as_completed([one(q) for q in qs[1:]]):
            q, res = await fut
            if isinstance(res, Exception):
                failures += 1
                replies[q["handle"]] = None
            else:
                take(q, *res)
            done += 1
            if done % 5 == 0 or done == len(qs):
                yield {"type": "progress", "done": done, "total": len(qs)}

    meta = {"provider": PROVIDERS[provider][0], "model": model, "tokens": tokens,
            "failed_calls": failures, "seconds": round(time.time() - started, 1),
            "prompt": ("the question asked plainly, with no hint (see the format)" if free else
                       "AdverseMed-500 paper prompt") + ", temperature 0 where the model accepts one"}
    if system_prompt:
        # a fingerprint, not the text: system prompts can hold things their owners keep private
        meta["system_prompt"] = {"fingerprint": _fingerprint(system_prompt), "chars": len(system_prompt)}
    name = display_name or model
    if free:
        yield {"type": "grading", "message": "Grading the replies…"}
        rep = await service.submit_replies(
            test["test_id"], [{"handle": h, "reply": r or ""} for h, r in replies.items()],
            model_name=name, publish=publish, tier="verified", run_meta=meta)
    else:
        answers = []
        for h, text in replies.items():
            if text is None:
                answers.append({"handle": h, "answer": None, "confidence": None})
                continue
            ans, conf = parse_response(text)
            answers.append({"handle": h, "answer": ans or ("INVALID" if text.strip() else None),
                            "confidence": conf})
        rep = service.submit(test["test_id"], answers, model_name=name, publish=publish,
                             tier="verified", run_meta=meta)
    rep.pop("missed", None)
    yield {"type": "done", "result_id": rep["result_id"], "report": rep}
