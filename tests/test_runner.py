import asyncio
import json
import re

import httpx

from abstaincheck import runner
from abstaincheck.bank import Bank
from abstaincheck.service import Service
from abstaincheck.store import MemoryStore

BANK = Bank()
KEY = "sk-test-SECRET-123"
BY_TEXT = {it["question"]: it for it in BANK.traps + BANK.controls}


def oracle_transport(seen):
    """A fake OpenAI-compatible provider that answers every question correctly."""
    def handler(req):
        seen.append(req)
        prompt = json.loads(req.content)["messages"][0]["content"]
        q = re.search(r"Question: (.*?)\n\nChoices:", prompt, re.S).group(1)
        it = BY_TEXT[q]
        ans = "FLAG" if it["kind"] == "trap" else it["truth"]
        return httpx.Response(200, json={"choices": [{"message": {"content": f"ANSWER: {ans}\nCONFIDENCE: 0.9"}}],
                                         "usage": {"total_tokens": 400}})
    return httpx.MockTransport(handler)


async def collect(gen):
    return [e async for e in gen]


def test_verified_run_with_oracle_model():
    s, seen = Service(MemoryStore(), BANK), []
    evs = asyncio.run(collect(runner.run(s, "openai", "fake-model", KEY, size="quick", publish=True,
                                         transport=oracle_transport(seen))))
    done = evs[-1]
    assert done["type"] == "done", evs[-1]
    assert done["report"]["D"] == 1 and done["report"]["tier"] == "verified"
    assert len(seen) == 40
    assert all(str(r.url).startswith("https://api.openai.com/v1/") for r in seen)
    # the key goes to the provider and nowhere else
    stored = json.dumps(s.store.data)
    assert KEY not in stored and KEY not in json.dumps(evs)
    assert s.leaderboard()["entries"][0]["tier"] == "verified"


def test_bad_key_fails_fast_and_key_is_not_echoed():
    def handler(req):
        return httpx.Response(401, json={"error": f"Incorrect API key provided: {KEY}"})
    s = Service(MemoryStore(), BANK)
    evs = asyncio.run(collect(runner.run(s, "openai", "m", KEY, transport=httpx.MockTransport(handler))))
    assert evs[-1]["type"] == "error" and "401" in evs[-1]["message"]
    assert KEY not in json.dumps(evs)


def test_unknown_provider_rejected():
    s = Service(MemoryStore(), BANK)
    evs = asyncio.run(collect(runner.run(s, "http://169.254.169.254", "m", KEY)))
    assert evs[-1]["type"] == "error"


def test_anthropic_model_that_rejects_temperature():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append("temperature" in body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "`temperature` is deprecated for this model"}})
        prompt = body["messages"][0]["content"]
        it = BY_TEXT[re.search(r"Question: (.*?)\n\nChoices:", prompt, re.S).group(1)]
        ans = "FLAG" if it["kind"] == "trap" else it["truth"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": f"ANSWER: {ans}\nCONFIDENCE: 0.9"}],
                                         "usage": {"input_tokens": 300, "output_tokens": 10}})
    s = Service(MemoryStore(), BANK)
    evs = asyncio.run(collect(runner.run(s, "anthropic", "claude-x", KEY, transport=httpx.MockTransport(handler))))
    assert evs[-1]["type"] == "done" and evs[-1]["report"]["D"] == 1
    assert seen[0] is True and seen[1] is False
