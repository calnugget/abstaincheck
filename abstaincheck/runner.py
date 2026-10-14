"""Verified runs: AbstainCheck sends each question to the user's model with the paper's prompt.

The user's API key is used for the duration of the run only. It is never stored, logged or
returned, and it is not included in error messages. Requests go only to the provider hosts
listed below, so the server cannot be pointed at arbitrary addresses.
"""
import asyncio
import time

import httpx

from .bank import render_prompt
from .scoring import parse_response

PROVIDERS = {
    # name: (label, kind, base url)
    "openai":     ("OpenAI", "openai", "https://api.openai.com/v1"),
    "anthropic":  ("Anthropic", "anthropic", "https://api.anthropic.com/v1"),
    "google":     ("Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "deepseek":   ("DeepSeek", "openai", "https://api.deepseek.com/v1"),
    "openrouter": ("OpenRouter (hundreds of models)", "openai", "https://openrouter.ai/api/v1"),
    "mistral":    ("Mistral", "openai", "https://api.mistral.ai/v1"),
    "groq":       ("Groq", "openai", "https://api.groq.com/openai/v1"),
    "together":   ("Together AI", "openai", "https://api.together.xyz/v1"),
    "xai":        ("xAI", "openai", "https://api.x.ai/v1"),
}

CONCURRENCY = 6
TIMEOUT = 120.0
MAX_TOKENS = 2048   # reasoning models spend output tokens thinking before the two answer lines


class ProviderError(Exception):
    """An error safe to show the user: never contains the key."""


def _safe(msg, key):
    msg = str(msg)[:300]
    return msg.replace(key, "[key]") if key else msg


async def _call(client, provider, model, key, prompt):
    label, kind, base = PROVIDERS[provider]
    if kind == "anthropic":
        r = await client.post(f"{base}/messages", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": MAX_TOKENS, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            raise ProviderError(f"{label} returned {r.status_code}: {_safe(r.text, key)}")
        body = r.json()
        return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"), body.get("usage", {})
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0, "max_tokens": MAX_TOKENS}
    headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    r = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
    if r.status_code == 400 and ("temperature" in r.text or "max_tokens" in r.text):
        # some reasoning models accept neither a temperature nor max_tokens
        payload.pop("temperature", None)
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        r = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
    if r.status_code != 200:
        raise ProviderError(f"{label} returned {r.status_code}: {_safe(r.text, key)}")
    body = r.json()
    msg = ((body.get("choices") or [{}])[0].get("message") or {})
    return msg.get("content") or "", body.get("usage", {})


async def run(service, provider, model, key, size="quick", publish=False, display_name=None,
              transport=None):
    """Async generator of progress events; the last one has type 'done' or 'error'."""
    if provider not in PROVIDERS:
        yield {"type": "error", "message": f"Unknown provider. Choose one of {sorted(PROVIDERS)}."}
        return
    if not key or not model:
        yield {"type": "error", "message": "A model name and an API key are both required."}
        return
    test = service.start_test(size=size, model_name=display_name or model, mode="api_run")
    qs = test["questions"]
    hmap = service.store.get("tests", test["test_id"])["handles"]
    yield {"type": "start", "test_id": test["test_id"], "n_questions": len(qs)}

    answers, done, tokens, failures = [], 0, 0, 0
    sem = asyncio.Semaphore(CONCURRENCY)
    started = time.time()

    async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
        # one probe call first, so a bad key or model name fails fast and cheaply
        first = qs[0]
        try:
            text, usage = await _call(client, provider, model, key,
                                      render_prompt(service.bank.by_id[hmap[first["handle"]]]))
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
                        return q, await _call(client, provider, model, key,
                                              render_prompt(service.bank.by_id[hmap[q["handle"]]]))
                    except (ProviderError, httpx.HTTPError) as e:
                        if attempt == 2:
                            return q, e
                        await asyncio.sleep(2 * (attempt + 1))

        def take(q, text, usage):
            nonlocal tokens
            ans, conf = parse_response(text)
            answers.append({"handle": q["handle"], "answer": ans or ("INVALID" if text.strip() else None),
                            "confidence": conf})
            tokens += int(usage.get("total_tokens") or
                          (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)

        take(first, text, usage)
        done = 1
        yield {"type": "progress", "done": done, "total": len(qs)}
        for fut in asyncio.as_completed([one(q) for q in qs[1:]]):
            q, res = await fut
            if isinstance(res, Exception):
                failures += 1
                answers.append({"handle": q["handle"], "answer": None, "confidence": None})
            else:
                take(q, *res)
            done += 1
            if done % 5 == 0 or done == len(qs):
                yield {"type": "progress", "done": done, "total": len(qs)}

    meta = {"provider": PROVIDERS[provider][0], "model": model, "tokens": tokens,
            "failed_calls": failures, "seconds": round(time.time() - started, 1),
            "prompt": "AdverseMed-500 paper prompt, temperature 0 where the model accepts it"}
    rep = service.submit(test["test_id"], answers, model_name=display_name or model,
                         publish=publish, tier="verified", run_meta=meta)
    rep.pop("missed", None)
    yield {"type": "done", "result_id": rep["result_id"], "report": rep}
