"""Calling a model provider. Only the hosts listed here can be reached, so the server cannot be
pointed at arbitrary addresses. Keys are passed in per call and never stored or logged, and
they are masked out of any error text."""
import httpx

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

TIMEOUT = 120.0
MAX_TOKENS = 2048   # reasoning models spend output tokens thinking before they answer


class ProviderError(Exception):
    """An error safe to show the user: never contains the key."""


def _safe(msg, key):
    msg = str(msg)[:300]
    return msg.replace(key, "[key]") if key else msg


async def call(client, provider, model, key, prompt, system=None, max_tokens=MAX_TOKENS):
    """One chat turn. Returns (reply text, usage dict)."""
    label, kind, base = PROVIDERS[provider]
    if kind == "anthropic":
        payload = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "user", "content": prompt}]}
        if system:
            payload["system"] = system
        r = await client.post(f"{base}/messages", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json=payload)
        if r.status_code != 200:
            raise ProviderError(f"{label} returned {r.status_code}: {_safe(r.text, key)}")
        body = r.json()
        return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"), body.get("usage", {})
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
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


def client(transport=None, timeout=TIMEOUT):
    return httpx.AsyncClient(timeout=timeout, transport=transport)
