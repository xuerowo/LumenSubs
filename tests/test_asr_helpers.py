import numpy as np

from app.asr import _kept, distribute_text, map_tokens, pick_text, repair_timings
from app.vad import FRAME_SEC, group_windows, plan_windows, speech_regions


def test_distribute_text_preserves_all_text():
    L = "今日はいい天気ですね。散歩に行きましょう。公園で会いましょう。"
    shorts = ["今日はいい天気ですね", "散歩に行きましょう", "公園で会いましょう"]
    parts = distribute_text(L, shorts)
    assert len(parts) == 3
    assert _kept("".join(parts)) == _kept(L)
    assert parts[0].startswith("今日") and parts[2].endswith("。")


def test_distribute_text_never_cuts_inside_a_word():
    L = "the quick brown fox jumps over the lazy dog"
    parts = distribute_text(L, ["the quick brow", "n fox jumps over the lazy dog"])
    assert " ".join(parts).split() == L.split()


def test_distribute_text_edge_cases():
    assert distribute_text("abc", []) == []
    assert distribute_text("abc", ["x"]) == ["abc"]
    assert distribute_text("", ["a", "b"]) == ["", ""]


def test_map_tokens_keeps_punctuation_with_words():
    words = map_tokens("Hello, world. Bye!", [("Hello", 0.0, 0.4), ("world", 0.5, 0.9), ("Bye", 1.2, 1.5)])
    assert [w["text"] for w in words] == ["Hello, ", "world. ", "Bye!"]
    assert [w["s"] for w in words] == [0.0, 0.5, 1.2]


def test_pick_text_prefers_long_context_when_similar():
    assert pick_text("hello there friend", "hello their friend", (0, 3), []) == "hello there friend"
    assert pick_text("", "local text", (0, 3), []) == "local text"


def test_repair_timings_monotonic_and_nonzero():
    probs = np.ones(400, dtype=np.float32)
    ws = [{"text": "a", "s": 1.0, "e": 1.0}, {"text": "b", "s": 0.5, "e": 0.9}, {"text": "c", "s": 2.0, "e": 2.4}]
    repair_timings(ws, probs, 400 * FRAME_SEC)
    for a, b in zip(ws, ws[1:]):
        assert b["s"] >= a["s"]
    assert all(w["e"] > w["s"] for w in ws)


def test_plan_windows_empty_and_short():
    assert plan_windows(np.zeros(0, dtype=np.float32), 0.0) == []
    wins = plan_windows(np.zeros(10, dtype=np.float32), 10 * FRAME_SEC)
    assert wins and wins[0][0] == 0.0


def test_plan_windows_cover_timeline():
    rng = np.random.default_rng(0)
    p = (rng.random(20000) > 0.3).astype(np.float32)       # ~10 minutes of frames
    p[3000:3100] = 0                                          # a clear pause
    total = len(p) * FRAME_SEC
    wins = plan_windows(p, total)
    assert wins[0][0] == 0.0 and abs(wins[-1][1] - total) < 1e-6
    for (a, b), (c, _) in zip(wins, wins[1:]):
        assert abs(b - c) < 1e-6 and b > a
    assert max(b - a for a, b in wins) <= 25.0
    groups = group_windows(wins)
    assert groups[0][0] == 0 and groups[-1][1] == len(wins)


def test_speech_regions():
    p = np.zeros(200, dtype=np.float32)
    p[50:100] = 0.9
    regs = speech_regions(p)
    assert len(regs) == 1
    s, e = regs[0]
    assert abs(s - 50 * FRAME_SEC) < 0.1 and abs(e - 100 * FRAME_SEC) < 0.1


# ---------------------------------------------------------------- repetition, hallucination, loading
from app.asr import _install_loop_guard, clearly_other_script, collapse_repeats, friendly_load_error  # noqa: E402


def test_collapse_repeats():
    assert collapse_repeats("やめて、" * 16) == "やめて、やめて…"
    assert collapse_repeats("あ" * 16) == "ああ…"
    assert collapse_repeats("本当に本当に") == "本当に本当に"          # two repeats are speech, not a loop
    assert collapse_repeats("10000 yen!!!!") == "10000 yen!!!!"        # digits / punctuation untouched


def test_hallucinated_line_from_the_long_pass_is_trimmed():
    lp = "今日はいい天気ですね。ご視聴ありがとうございました。"
    assert pick_text(lp, "今日はいい天気ですね", (0, 3), []) == "今日はいい天気ですね。"
    assert pick_text("ご視聴ありがとうございました。今日はいい天気ですね。", "今日はいい天気ですね", (0, 3), []) == "今日はいい天気ですね。"
    assert pick_text("今日はいい天気ですね。", "今日はいい天気ですね", (0, 3), []) == "今日はいい天気ですね。"


def test_clearly_other_script():
    assert clearly_other_script("Thank you so much for watching", "Japanese")
    assert not clearly_other_script("今日はいい天気", "Japanese")
    assert not clearly_other_script("Thank you so much for watching", "English")


def test_loop_guard_wraps_generate_and_reports_changed_internals(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    # the tests run without the (large) transformers package: a stand-in is enough
    fake = ModuleType("transformers")
    fake.StoppingCriteria = object
    fake.StoppingCriteriaList = list
    monkeypatch.setitem(sys.modules, "transformers", fake)
    seen = {}

    def generate(*a, **kw):
        seen.update(kw)
        return "ok"
    model = SimpleNamespace(model=SimpleNamespace(generate=generate))
    assert _install_loop_guard(model)
    assert model.model.generate(max_new_tokens=5) == "ok"
    assert len(seen["stopping_criteria"]) == 1 and seen["max_new_tokens"] == 5
    assert _install_loop_guard(SimpleNamespace()) is False     # qwen-asr changed shape: warn, don't crash


def test_friendly_load_errors():
    assert "顯示卡記憶體不足" in friendly_load_error(RuntimeError("CUDA out of memory. Tried to allocate"), "cuda")
    assert "太舊" in friendly_load_error(RuntimeError("no kernel image is available"), "cuda")
    assert "start.bat" in friendly_load_error(OSError("We couldn't find the files, offline mode"), "cpu")
