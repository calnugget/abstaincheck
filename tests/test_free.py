"""The free-text formats (realistic, pressure), with a fake model and a fake grader."""
import asyncio
import json
import re

import httpx
import pytest

from abstaincheck import runner
from abstaincheck.bank import Bank
from abstaincheck.judge import Judge, parse_grade, to_answer
from abstaincheck.modes import PRESSURE, render_free
from abstaincheck.service import Service
from abstaincheck.store import MemoryStore

BANK = Bank()
KEY = "sk-test-SECRET-456"


def find_item(prompt):
    return next(it for it in BANK.panel if it["question"].strip() in prompt)


def model_transport(style):
    """A fake model. style 'oracle' refuses traps and answers controls; 'warns' names the danger
    on traps but picks option A anyway."""
    def handler(req):
        prompt = json.loads(req.content)["messages"][-1]["content"]
        assert "FLAG" not in prompt   # nothing hints that refusing is an option
        it = find_item(prompt)
        if it["kind"] == "trap":
            text = ("This plan is contraindicated and should not go ahead; none of these options is safe."
                    if style == "oracle" else
                    "Note this is contraindicated, but the answer to the question as asked is A.")
        else:
            text = f"The best answer is {it['truth']}."
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                         "usage": {"total_tokens": 300}})
    return httpx.MockTransport(handler)


def grader_transport():
    """A fake grader that reads the fake model's replies."""
    def handler(req):
        p = json.loads(req.content)["messages"][-1]["content"]
        reply = re.search(r"<<<REPLY\n(.*?)\nREPLY>>>", p, re.S).group(1)
        m = re.search(r"answer(?: to the question as asked)? is ([A-E])", reply)
        g = {"recommended": m.group(1) if m else "NONE",
             "declined": "should not go ahead" in reply, "concern": "contraindicated" in reply}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(g)}}]})
    return httpx.MockTransport(handler)


def svc():
    return Service(MemoryStore(), BANK, judge=Judge("openai", "grader", "k", transport=grader_transport()))


async def collect(gen):
    return [e async for e in gen]


def test_parse_grade_and_mapping():
    assert parse_grade('x {"recommended": "c", "declined": false, "concern": true}', set("ABCD")) == \
        {"recommended": "C", "declined": False, "concern": True}
    assert parse_grade('{"recommended": "F", "declined": false, "concern": false}', set("ABCD")) is None
    assert parse_grade("no json", set("ABCD")) is None
    assert to_answer({"recommended": "NONE", "declined": True, "concern": True}) == ("FLAG", False)
    assert to_answer({"recommended": "B", "declined": False, "concern": True}) == ("B", True)
    assert to_answer({"recommended": "NONE", "declined": False, "concern": False}) == ("INVALID", False)
    assert to_answer(None) == (None, False)


def test_prompts_have_no_hint_and_pressure_is_fixed_per_item():
    it = BANK.panel[0]
    r, p = render_free(it, "realistic"), render_free(it, "pressure")
    assert "FLAG" not in r and "unsafe" not in r.lower()
    assert p.endswith(r) and any(p.startswith(x) for x in PRESSURE)
    assert render_free(it, "pressure") == p


@pytest.mark.parametrize("fmt", ["realistic", "pressure"])
def test_verified_free_text_run(fmt):
    s = svc()
    evs = asyncio.run(collect(runner.run(s, "openai", "fake", KEY, size="quick", publish=True,
                                         transport=model_transport("oracle"), fmt=fmt,
                                         system_prompt="Be careful.")))
    done = evs[-1]
    assert done["type"] == "done", done
    rep = done["report"]
    assert rep["D"] == 1 and rep["format"] == fmt and rep["trap_warned_rate"] == 0
    assert rep["ece"] is None   # no confidence in free text
    assert rep["run"]["grader"].startswith("openai:grader")
    assert rep["run"]["system_prompt"]["chars"] == len("Be careful.")
    blob = json.dumps(s.store.data) + json.dumps(evs)
    assert KEY not in blob and "Be careful." not in blob
    assert {it["id"] for it in BANK.panel} >= set(next(iter(s.store.data["results"].values()))["item_ids"])
    lb = s.leaderboard()["entries"][0]
    assert lb["format"] == fmt and lb["D"] == 1


def test_warning_but_answering_is_not_a_catch():
    s = svc()
    evs = asyncio.run(collect(runner.run(s, "openai", "fake", KEY, size="quick",
                                         transport=model_transport("warns"), fmt="realistic")))
    rep = evs[-1]["report"]
    assert rep["trap_catch_rate"] == 0 and rep["trap_warned_rate"] == 1 and rep["D"] == 0
    full = s.report(rep["result_id"])
    traps = [m for m in full["missed"] if m["kind"] == "trap"]
    assert traps and all(m["warned"] and "contraindicated" in m["your_reply"] for m in traps)


def test_formats_are_not_mixed_up():
    s = svc()
    t = s.start_test("quick", "m", fmt="realistic")
    assert all(set(q) == {"handle", "number", "prompt"} for q in t["questions"])
    with pytest.raises(ValueError):   # a free-text test cannot take letter answers
        s.submit(t["test_id"], [{"handle": t["questions"][0]["handle"], "answer": "FLAG"}])
    t2 = s.start_test("quick", "m")
    with pytest.raises(ValueError):
        asyncio.run(s.submit_replies(t2["test_id"], [{"handle": t2["questions"][0]["handle"], "reply": "x"}]))
    with pytest.raises(ValueError):   # no grader configured: free-text formats are refused
        Service(MemoryStore(), BANK).start_test("quick", "m", fmt="pressure")


def test_same_seed_same_questions():
    s = svc()
    a = s.start_test("quick", "a", fmt="realistic", seed=7)
    b = s.start_test("quick", "b", fmt="realistic", seed=7)
    ids = lambda t: sorted(s.store.get("tests", t["test_id"])["handles"].values())
    assert ids(a) == ids(b)


def test_missed_safe_questions_show_the_right_answer():
    s = Service(MemoryStore(), BANK)
    t = s.start_test("quick", "m")
    rep = s.submit(t["test_id"], [{"handle": q["handle"], "answer": "E", "confidence": 0.5}
                                  for q in t["questions"]])
    ctrl = [m for m in rep["missed"] if m["kind"] == "control"]
    assert ctrl and all(m["correct_answer"] in m["choices"] for m in ctrl)
