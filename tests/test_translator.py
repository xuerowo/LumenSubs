import json
import re
import threading
import time
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.translator import TranslateError, Translator, _id_map, _parse_json, postprocess


class FakeCompletions:
    """Stands in for client.chat.completions: translates '[n] text' → 'T:text'."""

    def __init__(self, drop=(), error=None, delay=0.0, finish=None):
        self.drop, self.error, self.delay, self.finish = set(drop), error, delay, finish
        self.calls = 0
        self.lock = threading.Lock()

    def create(self, **kw):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        msgs = kw["messages"]
        if msgs[0]["role"] != "system":           # analysis request
            content = json.dumps({"synopsis": "x"})
        else:
            ask = msgs[-1]["content"].split("Translate these cues now.")[-1]
            got = {m.group(1): "T:" + m.group(2) for m in re.finditer(r"^\[(\d+)\] (.*)$", ask, re.M)
                   if int(m.group(1)) not in self.drop}
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
    assert fake.calls > 1


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
