#!/usr/bin/env python3
"""Build AbstainCheck's question bank and baselines from the public AdverseMed-500 release.

    python3 scripts/build_data.py                      # downloads the latest Zenodo release
    python3 scripts/build_data.py --release DIR        # an already extracted release

The release is the archived replication package for the AdverseMed-500 paper (Zenodo concept
DOI 10.5281/zenodo.21961770). Every file used is checked against the release's SHA256SUMS.
The download is an anonymous GET: no account, key or identifying information is sent.

Writes three files into abstaincheck/data/ (gitignored, baked into the deploy image):

  traps.jsonl      AdverseMed-500 v1.2, all 500 items. The correct behaviour on every one
                   is to refuse (FLAG). The 25 items marked `contestable` are kept in the file
                   but never served in a test.
  controls.jsonl   The 285 answerable twins, with their keyed letters (public in the release).
  baselines.json   Per-question answers of the five models from the paper, so a new model
                   can be compared with them on exactly the questions it was given.

Baseline method: temperature_0 (one call, stated confidence), the closest match to an agent
answering once. GPT-5.4-mini has no temperature_0 control run, so it uses self_consistency on
both arms; the paper reports the same pairing.
"""
import argparse
import hashlib
import io
import json
import pathlib
import tarfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
OUT = HERE / "abstaincheck" / "data"
CACHE = HERE / ".release_cache"
CONCEPT_RECORD = "https://zenodo.org/api/records/21961770"   # redirects to the latest version

MODELS = {
    # key in paper files       display name           method used on both arms
    "gemini-2.5-pro":   ("Gemini Pro (paper run)",       "temperature_0"),
    "claude-opus-4-7":  ("Claude Opus 4.7 (paper run)",  "temperature_0"),
    "gpt-5.4-mini":     ("GPT-5.4-mini (paper run)",     "self_consistency"),
    "claude-haiku-4-5": ("Claude Haiku 4.5 (paper run)", "temperature_0"),
    "deepseek-chat":    ("DeepSeek-Chat (paper run)",    "temperature_0"),
}


def jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def answers(path):
    out = {}
    for r in jsonl(path):
        if "question_id" not in r:
            continue
        ans = (r.get("chosen_answer") or "").strip()
        # the paper's rule: an error or empty answer is no data; a parse_failure is a reply
        # that could not be read, and counts as an answer that is wrong (kept as INVALID)
        if r.get("error") not in (None, "None") or not ans:
            continue
        out[r["question_id"]] = {"answer": {"abstain": "FLAG", "parse_failure": "INVALID"}.get(ans, ans),
                                 "confidence": r.get("confidence")}
    return out


def _get(url):
    # anonymous request; urllib's default User-Agent carries nothing identifying
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def download():
    """Fetch and extract the latest release into .release_cache/. Returns its directory."""
    rec = json.loads(_get(CONCEPT_RECORD))
    version = rec["metadata"]["version"]
    (f,) = [f for f in rec["files"] if f["key"].endswith(".tar.gz")]
    root = CACHE / f["key"].removesuffix(".tar.gz")
    if not root.exists():
        blob = _get(f["links"]["self"])
        want = f.get("checksum", "")
        if want.startswith("md5:"):
            assert hashlib.md5(blob).hexdigest() == want[4:], "download corrupted"
        CACHE.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(blob)) as t:
            t.extractall(CACHE, filter="data")
    print(f"release {version}: {root}")
    return root


def checked(root, rel):
    """Path of a release file, after checking it against the release's SHA256SUMS."""
    sums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        h, name = line.split(maxsplit=1)
        sums[name.strip().removeprefix("./")] = h
    path = root / rel
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sums[rel], f"checksum mismatch: {rel}"
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", help="an extracted AdverseMed-500 release; default: download it")
    a = ap.parse_args()
    root = pathlib.Path(a.release) if a.release else download()
    OUT.mkdir(parents=True, exist_ok=True)

    traps = []
    for r in jsonl(checked(root, "benchmark/adversemed_500_v1.2.jsonl")):
        assert r["ground_truth"] == "abstain"
        v = r.get("verification") or {}
        traps.append({
            "id": r["question_id"], "kind": "trap",
            "category": r["false_premise_category"], "difficulty": r.get("difficulty"),
            "question": r["question"], "choices": r["choices"],
            "contestable": bool(r.get("contestable")),
            "explanation": " ".join((r.get("false_premise_description") or "").split()),
            "sources": sorted({u for u in (v.get("reference_urls") or []) if u}),
        })
    controls = []
    for r in jsonl(checked(root, "benchmark/adversemed_500_control_v1.jsonl")):
        assert r["ground_truth"] in r["choices"]
        v = r.get("verification") or {}
        controls.append({
            "id": r["question_id"], "kind": "control", "parent_id": r["parent_id"],
            "category": r["false_premise_category"], "difficulty": r.get("difficulty"),
            "question": r["question"], "choices": r["choices"], "truth": r["ground_truth"],
            "sources": sorted({u for u in (v.get("reference_urls") or []) if u}),
        })
    assert len(traps) == 500 and len(controls) == 285
    assert sum(t["contestable"] for t in traps) == 25

    baselines = {}
    for key, (name, method) in MODELS.items():
        par = answers(checked(root, f"inference_outputs/adversemed_500_final/{key}__{method}.jsonl"))
        ctl = answers(checked(root, f"inference_outputs/adversemed_control/{key}__{method}.jsonl"))
        assert len(par) >= 450 and len(ctl) >= 256, (key, len(par), len(ctl))
        baselines[key] = {"name": name, "method": method, "answers": {**par, **ctl}}

    for name, rows in (("traps.jsonl", traps), ("controls.jsonl", controls)):
        with open(OUT / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (OUT / "baselines.json").write_text(json.dumps(baselines))
    print(f"wrote {len(traps)} traps, {len(controls)} controls, {len(baselines)} baselines to {OUT}")


if __name__ == "__main__":
    main()
