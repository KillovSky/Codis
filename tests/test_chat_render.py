import time

from src.ui.chat_render import blend, day_label, ellipsize, normalize_url, safe_text, tokenize


def test_tokenize_code_bold_urls():
    segs = tokenize("veja `x = 1` e **forte** em https://a.io/p?q=1, ok www.b.com.")
    assert ("x = 1", ("code",)) in segs and ("forte", ("bold",)) in segs
    urls = [t for t, tags in segs if "url" in tags]
    assert urls == ["https://a.io/p?q=1", "www.b.com"]
    assert "".join(t for t, _ in segs) == "veja x = 1 e forte em https://a.io/p?q=1, ok www.b.com."


def test_tokenize_fenced_block_and_irc_format_stripped():
    segs = tokenize("antes\n```py\nprint(1)\n```\ndepois \x02negrito\x02 \x0304,01cor\x0f")
    assert any(tags == ("codeblock",) and "print(1)" in t for t, tags in segs)
    assert all("\x02" not in t and "\x03" not in t for t, _ in segs)


def test_normalize_url_blocks_dangerous_schemes():
    assert normalize_url("www.x.com") == "https://www.x.com"
    assert normalize_url("javascript:alert(1)") is None or normalize_url("javascript:alert(1)").startswith("https://")
    assert normalize_url("http://ok.io") == "http://ok.io"


def test_misc():
    assert safe_text("a\x00b\U0001F600c") == "ab\ufffdc"
    assert blend("#000000", "#ffffff", 0.5) == "#808080"
    assert day_label(time.time()) == "Hoje" and day_label(time.time() - 86400) == "Ontem"
    assert ellipsize("abcdefghij", 5) == "abcd\u2026"
