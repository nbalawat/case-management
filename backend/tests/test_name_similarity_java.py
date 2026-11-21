"""The Java name matching that Flink runs, held to the Python it was ported from.

A stream job must score a name exactly as the application does, or the two would disagree about who is on a list.
This compiles deploy/flink/udf (the two classes with no Flink in them) and compares answers. It needs a JDK on the
path (javac and java), and is skipped where there is none.
"""
from __future__ import annotations

import difflib
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.detection import scenarios
from app.detection.scenarios import DEMO_WATCHLIST, Watch, subject_key
from app.domain.models import Transaction

SRC = Path(__file__).resolve().parents[2] / "deploy" / "flink" / "udf" / "caseforge"
pytestmark = pytest.mark.skipif(not (shutil.which("javac") and shutil.which("java")), reason="needs a JDK (javac and java) on the path")
LETTERS = "abcdefghij klmnop  ABCDE-.'éßÖ中𝒜"


@pytest.fixture(scope="module")
def java(tmp_path_factory):
    out = tmp_path_factory.mktemp("udf")
    built = subprocess.run(["javac", "--release", "17", "-proc:none", "-d", str(out), *(str(SRC / f"{c}.java") for c in ("NameSimilarity", "Watchlist", "Check"))],
                           capture_output=True, text=True, timeout=180, check=False)
    assert built.returncode == 0, built.stderr[-3000:]

    def ask(lines: list[str]) -> list[str]:
        done = subprocess.run(["java", "-cp", str(out), "caseforge.Check"], input="".join(line + "\n" for line in lines).encode(),
                              capture_output=True, timeout=180, check=False)
        assert done.returncode == 0, done.stderr.decode()[-3000:]
        return done.stdout.decode().splitlines()

    return ask


def made_up(rnd, n=None):
    return "".join(rnd.choice(LETTERS) for _ in range(rnd.randint(0, 30) if n is None else n))


def misspelt(rnd, name):
    w = list(name)
    for _ in range(rnd.randint(0, 4)):
        i, how = rnd.randrange(len(w) + 1), rnd.random()
        if how < 0.3 and w:
            w.pop(min(i, len(w) - 1))
        elif how < 0.6:
            w.insert(i, rnd.choice(LETTERS))
        elif w:
            w[min(i, len(w) - 1)] = rnd.choice(LETTERS)
    return "".join(w)


def test_two_names_are_exactly_as_alike_in_java_as_in_python(java):
    rnd = random.Random(7)
    pairs = [(made_up(rnd), made_up(rnd)) for _ in range(1500)] + [(misspelt(rnd, rnd.choice(DEMO_WATCHLIST)), rnd.choice(DEMO_WATCHLIST)) for _ in range(1500)]
    pairs += [(made_up(rnd, rnd.randint(150, 400)), made_up(rnd, rnd.randint(190, 400))) for _ in range(60)]  # long enough for difflib to set letters aside
    pairs += [("", ""), ("a", ""), ("", "a"), ("Volkov Maritime Trading", "Volkov Maritime Trading")]
    got = java([f"ratio\t{a}\t{b}" for a, b in pairs])
    assert len(got) == len(pairs)
    assert [float(x) for x in got] == [difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs]


def test_a_name_is_folded_for_exemptions_as_the_application_folds_it(java):
    names = ["  Volkov   MARITIME Trading Co ", "ÉCOLE  Über", "one", "a b", "Zarin Exchange House"]
    assert java([f"fold\t{n}" for n in names]) == [subject_key(n) for n in names]


def test_a_payment_matches_the_same_listed_name_at_the_same_score_as_the_applications_scenario(java):
    rnd = random.Random(11)
    names = [(n, f"e{i}") for i, n in enumerate([*DEMO_WATCHLIST, "Volkov Maritime Trade", "Darya Shipping Line Co", "Northgate Petrochemical"])]
    seen = [misspelt(rnd, rnd.choice(names)[0]) for _ in range(600)] + [made_up(rnd) for _ in range(200)]
    seen = [c for c in seen if c.strip()] + ["VOLKOV maritime  trading", "Darya Shipping Lines Co"]
    exempt = frozenset({(subject_key("VOLKOV maritime  trading"), "e0"), (subject_key("Darya Shipping Lines Co"), "e2"), (subject_key(seen[0]), "e1")})
    ruled_out = sorted(f"{i}:{k}" for k, e in exempt for i, (_n, entry) in enumerate(names) if entry == e)
    for threshold in (0.86, 0.7):
        txns = [Transaction(id=f"t{i}", counterparty=c, amount=100.0, channel="wire") for i, c in enumerate(seen)]
        with scenarios.screening_against(Watch(tuple(names), exempt, threshold)):
            hits = {h.window_key: h for h in scenarios.sanctions_name(txns)}
        got = java(["\t".join(["list", repr(threshold), *(n for n, _e in names)]), "\t".join(["exempt", *ruled_out]), *(f"match\t{c}" for c in seen)])
        assert len(got) == len(seen) and 50 < len(hits) < len(seen)
        for i, (c, line) in enumerate(zip(seen, got, strict=True)):
            hit = hits.get(f"t{i}")
            if hit is None:
                assert line == "-", (c, line)
            else:
                name, ratio = line.split("\t")
                assert f"matches watchlist entry '{name}' at" in hit.explanation and round(float(ratio), 2) == hit.score, (c, line, hit.explanation)
