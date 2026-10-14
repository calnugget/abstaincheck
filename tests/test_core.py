import random

import pytest
from fastapi.testclient import TestClient

from abstaincheck.bank import Bank
from abstaincheck.scoring import score
from abstaincheck.service import Service
from abstaincheck.store import MemoryStore

BANK = Bank()

# The paper's reported values (analysis/score_2x2.py, all 500 + 285 items)
PAPER_D = {"gemini-2.5-pro": 0.935, "claude-opus-4-7": 0.879, "gpt-5.4-mini": 0.781,
           "claude-haiku-4-5": 0.736, "deepseek-chat": 0.693}


def svc():
    return Service(MemoryStore(), BANK)


def test_reproduces_paper():
    items = BANK.full_set(include_contestable=True)
    for k, d in PAPER_D.items():
        assert round(score(items, BANK.baselines[k]["answers"])["D"], 3) == d, k


def test_draw_is_stratified_and_twin_free():
    for size, k in (("quick", 5), ("standard", 15)):
        items = BANK.draw(size, seed=1)
        assert len(items) == 8 * k
        traps = {i["id"] for i in items if i["kind"] == "trap"}
        assert not any(i["kind"] == "control" and i["parent_id"] in traps for i in items)
        assert not any(i["kind"] == "trap" and i["contestable"] for i in items)


def test_questions_hide_answers_and_ids():
    t = svc().start_test("quick", "x")
    blob = str(t["questions"])
    assert "adversemed" not in blob and "truth" not in blob and "explanation" not in blob
    assert all(set(q) == {"handle", "number", "question", "choices"} for q in t["questions"])


def _answer_all(s, t, policy):
    hmap = s.store.get("tests", t["test_id"])["handles"]
    out = []
    for q in t["questions"]:
        it = BANK.by_id[hmap[q["handle"]]]
        out.append({"handle": q["handle"], "answer": policy(it), "confidence": 0.9})
    return out


def test_perfect_refuser_and_answerer_score_zero():
    for policy in (lambda it: "FLAG", lambda it: "A"):
        s = svc()
        t = s.start_test("quick", "m")
        rep = s.submit(t["test_id"], _answer_all(s, t, policy))
        assert rep["D"] == 0


def test_oracle_scores_one_and_ranks_first():
    s = svc()
    t = s.start_test("standard", "oracle")
    rep = s.submit(t["test_id"], _answer_all(s, t, lambda it: "FLAG" if it["kind"] == "trap" else it["truth"]),
                   publish=True)
    assert rep["D"] == 1 and rep["control_accuracy"] == 1 and not rep["missed"]
    assert rep["comparison"]["rows"][0]["is_you"]
    assert s.leaderboard()["entries"][0]["model"] == "oracle"


def test_missing_answers_withhold_score_and_block_publish():
    s = svc()
    t = s.start_test("quick", "m")
    ans = _answer_all(s, t, lambda it: "FLAG")[:20]
    rep = s.submit(t["test_id"], ans, publish=True)
    assert rep["D"] is None and rep["withheld"]
    assert s.leaderboard()["entries"] == []


def test_test_cannot_be_resubmitted():
    s = svc()
    t = s.start_test("quick", "m")
    s.submit(t["test_id"], _answer_all(s, t, lambda it: "FLAG"))
    with pytest.raises(ValueError):
        s.submit(t["test_id"], _answer_all(s, t, lambda it: "FLAG"))


def test_profile_and_guidance():
    s = svc()
    t = s.start_test("standard", "weak-on-ddi")
    rep = s.submit(t["test_id"], _answer_all(
        s, t, lambda it: ("A" if it["category"] == "drug_drug_interaction" else "FLAG")
        if it["kind"] == "trap" else it["truth"]))
    g = s.check_question("Patient on warfarin is prescribed...", "drug_drug_interaction",
                         result_id=rep["result_id"])
    assert g["checks"][0]["caution"] == "weak" and g["checks"][0]["your_catch_rate"] == 0
    g = s.check_question("x", "contraindication", result_id=rep["result_id"])
    assert g["checks"][0]["caution"] == "strong"


def test_api_and_mcp_end_to_end():
    from abstaincheck import app as appmod
    with TestClient(appmod.app) as c:
        t = c.post("/api/v1/tests", json={"model_name": "api", "size": "quick"}).json()
        hmap = appmod.service.store.get("tests", t["test_id"])["handles"]
        ans = [{"handle": q["handle"], "answer": "FLAG" if BANK.by_id[hmap[q["handle"]]]["kind"] == "trap"
                else BANK.by_id[hmap[q["handle"]]]["truth"], "confidence": 1.0} for q in t["questions"]]
        r = c.post(f"/api/v1/tests/{t['test_id']}/answers", json={"answers": ans}).json()
        assert r["D"] == 1
        assert c.get(f"/api/v1/results/{r['result_id']}").status_code == 200
        assert c.get("/api/v1/results/nope").status_code == 404
        h = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        init = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert init.status_code == 200, init.text
        tools = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {x["name"] for x in tools.json()["result"]["tools"]}
        assert {"start_self_test", "submit_answers", "check_medical_question"} <= names
        call = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "start_self_test", "arguments": {"model_name": "mcp-test"}}})
        assert call.status_code == 200 and "test_id" in call.text, call.text[:500]


def test_short_perfect_score_keeps_honest_interval():
    s = svc()
    t = s.start_test("quick", "perfect")
    rep = s.submit(t["test_id"], _answer_all(s, t, lambda it: "FLAG" if it["kind"] == "trap" else it["truth"]))
    lo, hi = rep["D_ci95"]
    assert rep["D"] == 1 and hi == 1 and lo < 0.85
    assert all("id" not in m for m in rep["missed"])
