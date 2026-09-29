import pytest

from app import fonts

TTF_CSS = """
@font-face { font-family: 'Noto Sans TC'; font-style: normal; font-weight: 400;
  src: url(https://fonts.gstatic.com/s/notosanstc/a400.ttf) format('truetype'); }
@font-face { font-family: 'Noto Sans TC'; font-style: normal; font-weight: 700;
  src: url(https://fonts.gstatic.com/s/notosanstc/a700.ttf) format('truetype'); }
@font-face { font-family: 'Manrope'; font-style: normal; font-weight: 400;
  src: url(http://insecure.example/m.ttf) format('truetype'); }
"""
WEB_CSS = """
@font-face { font-family: 'Noto Sans TC'; font-weight: 400; src: url(x.woff2); unicode-range: U+4E00-4E10, U+4E11; }
@font-face { font-family: 'Noto Sans TC'; font-weight: 400; src: url(y.woff2); unicode-range: U+0-FF, U+30??; }
"""
TTF_BYTES = b"\x00\x01\x00\x00" + b"\x00" * 64


@pytest.fixture
def fake_net(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "FONT_DIR", tmp_path)
    monkeypatch.setattr(fonts, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(fonts, "_index", None)
    monkeypatch.setattr(fonts, "_failed_at", 0.0)
    calls = []

    def get(url, ua, timeout=30):
        calls.append(url)
        if "css2" in url:
            return (TTF_CSS if ua == fonts.UA_TTF else WEB_CSS).encode()
        return TTF_BYTES
    monkeypatch.setattr(fonts, "_get", get)
    return calls


def test_merge_ranges():
    assert fonts._merge_ranges(["U+4E00-4E10, U+4E11", "U+0-FF, U+30??"]) == "U+0-FF, U+3000-30FF, U+4E00-4E11"
    assert fonts._merge_ranges(["U+0-FF", "U+140-150"], slack=64) == "U+0-150"


def test_index_css_and_download(fake_net, tmp_path):
    css = fonts.css()
    assert "font-family:'Noto Sans TC'" in css and "/fonts/file/NotoSansTC-700.ttf" in css
    assert "insecure" not in css and "Manrope" not in css          # non-https sources are dropped
    assert "unicode-range:U+0-FF, U+3000-30FF, U+4E00-4E11" in css
    p = fonts.ensure_file("NotoSansTC-400.ttf")
    assert p.read_bytes() == TTF_BYTES
    n = len(fake_net)
    fonts.ensure_file("NotoSansTC-400.ttf")                          # cached: no new request
    assert len(fake_net) == n
    assert fonts.ensure_file("../../evil.ttf") is None               # only indexed names


def test_rejects_non_font_data(fake_net, monkeypatch):
    fonts.css()
    monkeypatch.setattr(fonts, "_get", lambda url, ua, timeout=30: b"<html>error</html>")
    with pytest.raises(RuntimeError):
        fonts.ensure_file("NotoSansTC-700.ttf")
    assert not (fonts.FONT_DIR / "NotoSansTC-700.ttf").exists()


def test_offline_gives_empty_css(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(fonts, "_index", None)
    monkeypatch.setattr(fonts, "_failed_at", 0.0)

    def offline(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(fonts, "_get", offline)
    assert "offline" in fonts.css()


def test_embedding_picks_regular_and_bold(fake_net):
    assert fonts.pick("Noto Serif TC", 400) == "NotoSerifTC-500.ttf"
    assert fonts.pick("Noto Serif TC", 700) == "NotoSerifTC-700.ttf"
    files = fonts.for_embedding(["Noto Sans TC", "Noto Sans TC", "Unknown"])
    assert [p.name for p in files] == ["NotoSansTC-400.ttf", "NotoSansTC-700.ttf"]


def test_font_routes(client, fake_net):
    r = client.get("/fonts/fonts.css")                                # no token needed
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    r = client.get("/fonts/file/NotoSansTC-400.ttf")
    assert r.status_code == 200 and r.content == TTF_BYTES
    assert client.get("/fonts/file/Nope.ttf").status_code == 404
