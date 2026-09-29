import json
import re
import threading
import time
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.translator import TranslateError, Translator, _guard_fix, _id_map, _parse_json, plan_batches, postprocess


class FakeCompletions:
    """Stands in for client.chat.completions: translates '[n] text' → 'T:text'."""

    def __init__(self, drop=(), error=None, delay=0.0, finish=None, copy=(), reject=()):
        self.drop, self.error, self.delay, self.finish = set(drop), error, delay, finish
        self.copy, self.reject = set(copy), set(reject)     # ids answered with the source / batches refused (400)
        self.calls = 0
        self.sizes = []                                     # ids asked for per translation request
        self.kwargs = []
        self.lock = threading.Lock()

    def create(self, **kw):
        with self.lock:
            self.calls += 1
            self.kwargs.append(kw)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        msgs = kw["messages"]
        if msgs[0]["role"] != "system":           # analysis request
            content = json.dumps({"synopsis": "x"})
        else:
            ask = msgs[-1]["content"].split("Translate these cues now.")[-1]
            ids = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\[(\d+)\] (.*)$", ask, re.M)]
            with self.lock:
                self.sizes.append(len(ids))
            if any(i in self.reject for i, _ in ids):
                raise openai.BadRequestError("Content Exists Risk", response=httpx.Response(
                    400, request=httpx.Request("POST", "https://x")), body=None)
            got = {str(i): (t if i in self.copy else "T:" + t) for i, t in ids if i not in self.drop}
            content = json.dumps({"t": got})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                        finish_reason=self.finish or "stop")])


def make(fake):
    tr = Translator({"api_key": "sk-test", "model": "m"})
    tr.client = SimpleNamespace(chat=SimpleNamespace(completions=fake), close=lambda: None)
    return tr


def cues(n):
    return [{"src": f"line {i}"} for i in range(n)]


def test_translates_every_cue():
    tr = make(FakeCompletions())
    out = tr.translate_all(cues(100), "en", "de")
    assert out == [f"T:line {i}" for i in range(100)]
    assert tr.failed == []


def test_partial_failure_is_reported():
    tr = make(FakeCompletions(drop={3, 50}))
    out = tr.translate_all(cues(60), "en", "de", opts={"context": False})
    assert out[2] == "" and out[49] == ""
    assert tr.failed == [2, 49]


def test_all_failed_raises_with_reason():
    tr = make(FakeCompletions(error=openai.APIConnectionError(request=httpx.Request("POST", "https://x"))))
    with pytest.raises(TranslateError, match="沒有回傳結果"):
        tr.translate_all(cues(5), "en", "de", opts={"context": False})


def test_auth_error_stops_immediately():
    err = openai.AuthenticationError("bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                                     body=None)
    fake = FakeCompletions(error=err)
    tr = make(fake)
    with pytest.raises(TranslateError, match="401"):
        tr.translate_all(cues(200), "en", "de", opts={"context": False})
    assert fake.calls <= 6                      # no per-batch retries


def test_truncated_output_is_retried_smaller():
    fake = FakeCompletions(finish="length")
    tr = make(fake)
    with pytest.raises(TranslateError):
        tr.translate_all(cues(10), "en", "de", opts={"context": False})
    # the very first retry already halves the batch (same size would be cut off again)
    assert fake.sizes[0] == 10 and fake.sizes[1] == 5 and max(fake.sizes[1:]) <= 5


def test_cancel_returns_quickly():
    fake = FakeCompletions(delay=1.0)
    tr = make(fake)
    flag = threading.Event()
    threading.Timer(0.2, flag.set).start()
    t0 = time.time()
    with pytest.raises(TranslateError, match="取消"):
        tr.translate_all(cues(400), "en", "de", opts={"context": False}, cancelled=flag.is_set)
    assert time.time() - t0 < 1.5


def test_retranslate_rejects_bad_index():
    tr = make(FakeCompletions())
    with pytest.raises(TranslateError):
        tr.retranslate(cues(3), 5, "en", "de")


def test_id_map_and_parse_json():
    assert _id_map({"1": "a", "[2]": "b", "x": "c", "3": 4}) == {0: "a", 1: "b"}
    assert _parse_json('```json\n{"t": {"1": "a"}}\n```') == {"t": {"1": "a"}}
    assert _parse_json('noise {"a": 1} noise') == {"a": 1}


@pytest.mark.parametrize("raw,tgt,expected", [
    ('"Hello there"', "en", "Hello there"),
    ('He said "hi"', "en", 'He said "hi"'),              # inner quotes are kept
    ("line one\n\nline two", "en", "line one line two"),  # no blank lines inside a cue
    ("你好。", "zh-TW", "你好"),
    ("第一句\n第二句", "ja", "第一句　第二句"),
])
def test_postprocess(raw, tgt, expected):
    assert postprocess(raw, tgt) == expected


# ---------------------------------------------------------------- assessment fixes
def test_one_rejected_batch_does_not_discard_the_rest():
    fake = FakeCompletions(reject={5})                 # e.g. a content filter on one batch
    tr = make(fake)
    out = tr.translate_all(cues(200), "en", "de", opts={"context": False})
    assert out[100] == "T:line 100" and out[4] == ""
    # retries narrow the refusal down to part of the first batch; everything else is translated
    assert 4 in tr.failed and all(i < 45 for i in tr.failed) and len(tr.failed) <= 25


def test_untranslated_copies_are_retried_then_accepted():
    fake = FakeCompletions(copy={3})                   # ids are 1-based: the third cue
    tr = make(fake)
    out = tr.translate_all([{"src": "Konnichiwa minasan"}, {"src": "OK"}, {"src": "Konnichiwa minasan"}], "ja", "de",
                           opts={"context": False})
    assert out[0] == "T:Konnichiwa minasan" and out[1] == "T:OK"
    assert out[2] == "Konnichiwa minasan" and fake.calls == 3   # retried twice, then kept (it may be a name)


def test_batches_end_at_sentence_boundaries():
    cs = [{"src": f"line {i}" + ("." if i % 7 == 6 else "")} for i in range(130)]
    bs = plan_batches(cs)
    assert sum(len(b) for b in bs) == 130 and [b[0] for b in bs] == sorted(b[0] for b in bs)
    for b in bs[:-1]:
        assert cs[b[-1]]["src"].endswith(".")


def test_usage_is_counted():
    fake = FakeCompletions()

    def create(**kw):
        r = FakeCompletions.create(fake, **kw)
        r.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_cache_hit_tokens=60)
        return r
    tr = make(SimpleNamespace(create=create))
    tr.translate_all(cues(10), "en", "de", opts={"context": False})
    assert tr.usage == {"requests": 1, "prompt": 100, "completion": 10, "cache_hit": 60}


def test_deepseek_only_options_are_not_sent_elsewhere():
    fake = FakeCompletions()
    tr = Translator({"api_key": "sk-test", "model": "m", "base_url": "https://api.example.com/v1"})
    tr.client = SimpleNamespace(chat=SimpleNamespace(completions=fake), close=lambda: None)
    tr.translate_all(cues(10), "en", "de")          # includes the analysis request (thinking off)
    assert not any("extra_body" in kw for kw in fake.kwargs)
    tr2 = make(FakeCompletions())
    tr2.translate_all(cues(10), "en", "de")
    assert any("extra_body" in kw for kw in tr2.client.chat.completions.kwargs)


@pytest.mark.parametrize("old,new,ok", [
    ("機会", "機械", True),                                          # homophone fix
    ("I put it over their", "I put it over there", True),
    ("彼女は昨日しんぶんを読んだ", "彼女は昨日新聞を読んだ", True),       # kana → kanji
    ("I will definitely come tomorrow night", "I will definitely not come tomorrow night", False),
    ("I really love this movie and will watch it", "I really hate this movie and will watch it", False),
    ("我們明天早上八點在學校門口見面吧", "我們明天晚上九點在公司門口見面吧", False),
    ("我有三個蘋果", "我有四個蘋果", False),                           # numbers never change
    ("Hello", "Hello", False),
])
def test_proofreading_guard(old, new, ok):
    assert _guard_fix(old, new) is ok


@pytest.mark.parametrize("raw,expected", [
    ("一台電腦", "一台電腦"), ("舞台上的台北", "舞台上的台北"),         # 台 is Traditional too
    ("这个视频的软件很好", "這個影片的軟體很好"), ("你好.", "你好"), ("等等...", "等等…"),
])
def test_postprocess_taiwan(raw, expected):
    pytest.importorskip("opencc")
    assert postprocess(raw, "zh-TW") == expected
