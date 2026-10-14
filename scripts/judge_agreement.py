"""How often each candidate grader agrees with the hand labels.

    python3 scripts/judge_agreement.py --judge openai:gpt-5.4-mini --judge google:gemini-flash-latest

Run grade_free.py --sample-only for each grader first. The number that matters is agreement
on the outcome the score uses: refused (FLAG), the option recommended, or neither.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from abstaincheck.bank import DATA, Bank  # noqa: E402
from abstaincheck.judge import to_answer  # noqa: E402
from grade_free import slug  # noqa: E402


def kappa(a, b):
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main(a):
    bank = Bank()
    sample = json.loads((DATA / "label_sample.json").read_text())
    labels = json.loads((DATA / "labels.json").read_text())
    print(f"{len(labels)} of {len(sample)} sampled replies labelled\n")
    for spec in a.judge:
        f = DATA / "free_grades" / f"{slug(spec)}.jsonl"
        grades = {}
        for l in open(f):
            g = json.loads(l)
            grades[(g["format"], g["model"], g["id"])] = g["grade"]
        hum, mac, rows = [], [], []
        for n, s in enumerate(sample):
            lab, g = labels.get(str(n)), grades.get((s["format"], s["model"], s["id"]))
            if lab is None or g is None:
                continue
            h, _ = to_answer(lab)
            m, _ = to_answer(g)
            hum.append(h)
            mac.append(m)
            rows.append((n, s, h, m, lab, g))
        ref_h = ["refused" if x == "FLAG" else "not" for x in hum]
        ref_m = ["refused" if x == "FLAG" else "not" for x in mac]
        same = sum(x == y for x, y in zip(hum, mac))
        print(f"== {spec}: {len(rows)} compared")
        print(f"  same outcome (refused / same letter / neither): {same}/{len(rows)}")
        print(f"  refused-or-not agreement: {sum(x == y for x, y in zip(ref_h, ref_m))}/{len(rows)}, "
              f"kappa {kappa(ref_h, ref_m):.2f}")
        conc = [(r[4]["concern"], r[5]["concern"]) for r in rows]
        print(f"  'raised a concern' agreement: {sum(x == y for x, y in conc)}/{len(conc)}")
        by = {}
        for n, s, h, m, *_ in rows:
            k = s["model"]
            by.setdefault(k, [0, 0])
            by[k][0] += h == m
            by[k][1] += 1
        print("  by model: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by.items())))
        for n, s, h, m, lab, g in rows:
            if h != m:
                kind = bank.by_id[s["id"]]["kind"]
                print(f"  disagree #{n} ({s['format']}, {s['model']}, {kind}): you {h}, grader {m}"
                      + (f"  note: {lab['note']}" if lab.get("note") else ""))
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", action="append", required=True)
    main(ap.parse_args())
