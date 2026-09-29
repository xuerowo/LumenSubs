import random
import re

import pytest

from app.segmenter import ends_sentence, is_meaningful, is_vocal, polish_timing, segment


def words(text, step=0.32, dur=0.3):
    out, t = [], 0.0
    for w in re.findall(r"\S+\s*", text):
        out.append({"text": w, "s": t, "e": t + dur})
        t += step
    return out


@pytest.mark.parametrize("text,expected", [
    ("home.", True), ("home. ", True), ('end."', True), ("2020.", True), ("wait...", True),
    ("完了。", True), ("本当？", True),
    ("Mr.", False), ("e.g.", False), ("J.", False), ("etc.", False),
    ("nice,", False), ("word", False), ("3.5", False),
])
def test_ends_sentence(text, expected):
    assert ends_sentence(text) is expected


def test_english_breaks_at_full_stops():
    cues = segment(words("I went home. Then I slept for a while, and it was nice. The end came soon."), 42, 30)
    assert [c["src"] for c in cues] == [
        "I went home.", "Then I slept for a while, and it was nice.", "The end came soon."]


def test_abbreviation_does_not_split():
    cues = segment(words("Mr. Smith arrived late."), 42, 30)
    assert [c["src"] for c in cues] == ["Mr. Smith arrived late."]


def test_empty_input():
    assert segment([], 42, 10) == []


def _check_invariants(cues):
    prev_start, prev_end = -1.0, 0.0
    for c in cues:
        assert c["start"] < c["end"], c
        assert c["start"] >= prev_start
        assert c["start"] >= prev_end - 1e-9, (prev_end, c)
        prev_start, prev_end = c["start"], c["end"]


def test_polish_timing_regression_overlap():
    cues = [{"start": 0, "end": 1}, {"start": 1.02, "end": 1.05}, {"start": 1.08, "end": 2.0}]
    polish_timing(cues)
    _check_invariants(cues)


@pytest.mark.parametrize("seed", range(40))
def test_polish_timing_random(seed):
    rnd = random.Random(seed)
    t, cues = 0.0, []
    for _ in range(rnd.randint(1, 40)):
        t += rnd.choice([0.0, 0.01, 0.03, 0.2, 1.0, 3.0]) * rnd.random()
        d = rnd.choice([0.02, 0.1, 0.5, 2.0, 6.0]) * (0.2 + rnd.random())
        cues.append({"start": round(t, 3), "end": round(t + d, 3)})
        t += d
    polish_timing(cues, duration=t + rnd.random())
    _check_invariants(cues)


def test_segment_output_never_overlaps():
    rnd = random.Random(7)
    text = " ".join(rnd.choice(["yes.", "no,", "maybe", "the", "cat", "sat.", "oh", "well,", "Mr.", "X."])
                    for _ in range(300))
    ws, t = [], 0.0
    for w in re.findall(r"\S+\s*", text):
        d = rnd.choice([0.05, 0.2, 0.4])
        ws.append({"text": w, "s": t, "e": t + d})
        t += d + rnd.choice([0.0, 0.0, 0.1, 0.8, 1.5])
    cues = segment(ws, 42, t + 1)
    assert cues
    _check_invariants(cues)


# ---------------------------------------------------------------- short answers and punctuation
def _w(*items):
    return [{"text": t, "s": s, "e": e} for t, s, e in items]


def test_short_answers_are_never_dropped():
    out = segment(_w(("本当に行くの？", 0.0, 1.5), ("はい。", 2.5, 2.85), ("じゃあ行こう。", 4.0, 5.2)))
    assert [c["src"] for c in out] == ["本当に行くの？", "はい。", "じゃあ行こう。"]


def test_numbers_are_not_vocalisations():
    assert not is_vocal("3, 2, 1") and not is_vocal("2020.")
    out = segment(_w(("Ready? ", 0.0, 0.6), ("3. ", 1.5, 1.8), ("2. ", 2.5, 2.8), ("1. ", 3.5, 3.8), ("Go!", 4.5, 4.9)))
    assert [c["src"] for c in out] == ["Ready?", "3.", "2.", "1.", "Go!"]


def test_isolated_short_breath_is_dropped_but_close_one_is_merged():
    assert [c["src"] for c in segment(_w(("あっ", 0.0, 0.2), ("今日は。", 3.0, 4.0)))] == ["今日は。"]
    out = segment(_w(("あっ", 0.0, 0.2), ("やめて。", 0.3, 1.2)))
    assert [c["src"] for c in out] == ["あっやめて。"]


def test_meaningful_detection():
    assert is_meaningful("はい。") and is_meaningful("いいえ") and is_meaningful("네")
    assert not is_meaningful("あっ") and not is_meaningful("はぁ…")


@pytest.mark.parametrize("text", ["كيف حالك؟", "यह अच्छा है।", "چطوری؟", "Τι κάνεις;"])
def test_non_latin_sentence_ends(text):
    assert ends_sentence(text)
