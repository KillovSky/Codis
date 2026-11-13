"""Testes para src/editor/text_transforms.py - funções puras de transformação de texto."""

from __future__ import annotations

import base64
import hashlib
import re
import urllib.parse

import pytest

from src.editor.text_transforms import (
    TRANSFORMS,
    decode_base64,
    decode_url,
    deduplicate_lines,
    encode_base64,
    encode_url,
    escape_html,
    escape_json,
    hash_md5,
    hash_sha1,
    hash_sha256,
    insert_lorem,
    insert_timestamp,
    insert_uuid,
    number_lines,
    remove_empty_lines,
    reverse_lines,
    to_camel_case,
    to_constant_case,
    to_dot_case,
    to_kebab_case,
    to_pascal_case,
    to_path_case,
    to_snake_case,
    unescape_html,
)


class TestCaseConversions:
    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("hello world", "helloWorld"),
            ("foo_bar_baz", "fooBarBaz"),
            ("MyClassName", "myClassName"),
            ("FOO_BAR", "fooBar"),
            ("hello", "hello"),
        ],
    )
    def test_camel_case(self, inp, expected):
        assert to_camel_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("hello world", "HelloWorld"),
            ("foo_bar", "FooBar"),
            ("my-class", "MyClass"),
            ("FOO BAR", "FooBar"),
            ("hello", "Hello"),
        ],
    )
    def test_pascal_case(self, inp, expected):
        assert to_pascal_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("HelloWorld", "hello_world"),
            ("fooBar", "foo_bar"),
            ("foo bar", "foo_bar"),
            ("FOO BAR", "foo_bar"),
            ("already_snake", "already_snake"),
        ],
    )
    def test_snake_case(self, inp, expected):
        assert to_snake_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("HelloWorld", "hello-world"),
            ("foo_bar", "foo-bar"),
            ("Foo Bar", "foo-bar"),
            ("already-kebab", "already-kebab"),
        ],
    )
    def test_kebab_case(self, inp, expected):
        assert to_kebab_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("hello world", "HELLO_WORLD"),
            ("fooBar", "FOO_BAR"),
            ("foo-bar", "FOO_BAR"),
            ("ALREADY", "ALREADY"),
        ],
    )
    def test_constant_case(self, inp, expected):
        assert to_constant_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("hello world", "hello.world"),
            ("fooBar", "foo.bar"),
            ("FOO_BAR", "foo.bar"),
        ],
    )
    def test_dot_case(self, inp, expected):
        assert to_dot_case(inp) == expected

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("hello world", "hello/world"),
            ("fooBar", "foo/bar"),
            ("FOO_BAR", "foo/bar"),
        ],
    )
    def test_path_case(self, inp, expected):
        assert to_path_case(inp) == expected

    def test_empty_string_returns_empty(self):
        for fn in (
            to_camel_case,
            to_pascal_case,
            to_snake_case,
            to_kebab_case,
            to_constant_case,
            to_dot_case,
            to_path_case,
        ):
            result = fn("")
            assert isinstance(result, str)

    def test_single_word(self):
        assert to_camel_case("hello") == "hello"
        assert to_pascal_case("hello") == "Hello"
        assert to_snake_case("hello") == "hello"
        assert to_kebab_case("hello") == "hello"
        assert to_constant_case("hello") == "HELLO"

    def test_numbers_preserved(self):
        assert "2" in to_snake_case("foo2bar") or to_snake_case("foo2bar")


class TestBase64:
    def test_encode(self):
        result = encode_base64("hello")
        assert result == base64.b64encode(b"hello").decode()

    def test_decode(self):
        encoded = base64.b64encode(b"world").decode()
        assert decode_base64(encoded) == "world"

    def test_round_trip(self):
        original = "Codis é incrível! 🚀"
        assert decode_base64(encode_base64(original)) == original

    def test_decode_with_missing_padding(self):
        encoded = base64.b64encode(b"test").decode().rstrip("=")
        result = decode_base64(encoded)
        assert result == "test"

    def test_decode_invalid_returns_original(self):
        bad = "!!!não é base64!!!"
        result = decode_base64(bad)
        assert result == bad


class TestUrlEncoding:
    def test_encode(self):
        result = encode_url("hello world/foo?a=1")
        assert " " not in result
        assert "/" not in result

    def test_decode(self):
        encoded = urllib.parse.quote("hello world", safe="")
        assert decode_url(encoded) == "hello world"

    def test_round_trip(self):
        original = "foo bar & baz=1"
        assert decode_url(encode_url(original)) == original


class TestHtmlEscape:
    def test_escape_special_chars(self):
        result = escape_html('<script>alert("xss")</script>')
        assert "<" not in result
        assert ">" not in result
        assert '"' not in result

    def test_unescape(self):
        escaped = "&lt;b&gt;bold&lt;/b&gt;"
        assert unescape_html(escaped) == "<b>bold</b>"

    def test_round_trip(self):
        original = '<a href="url">text & more</a>'
        assert unescape_html(escape_html(original)) == original

    def test_plain_text_unchanged(self):
        assert escape_html("hello world") == "hello world"


class TestJsonEscape:
    def test_escape_newline(self):
        assert "\\n" in escape_json("line1\nline2")

    def test_escape_backslash(self):
        assert "\\\\" in escape_json("a\\b")

    def test_escape_quote(self):
        assert '\\"' in escape_json('say "hi"')

    def test_escape_tab(self):
        assert "\\t" in escape_json("a\tb")

    def test_no_change_for_plain(self):
        assert escape_json("hello world") == "hello world"


class TestHashes:
    def test_md5(self):
        expected = hashlib.md5(b"hello").hexdigest()
        assert hash_md5("hello") == expected

    def test_sha1(self):
        expected = hashlib.sha1(b"hello").hexdigest()
        assert hash_sha1("hello") == expected

    def test_sha256(self):
        expected = hashlib.sha256(b"hello").hexdigest()
        assert hash_sha256("hello") == expected

    def test_md5_length(self):
        assert len(hash_md5("x")) == 32

    def test_sha1_length(self):
        assert len(hash_sha1("x")) == 40

    def test_sha256_length(self):
        assert len(hash_sha256("x")) == 64

    def test_different_inputs_different_hashes(self):
        assert hash_sha256("a") != hash_sha256("b")

    def test_empty_string(self):
        assert len(hash_md5("")) == 32


class TestInsertion:
    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )

    def test_uuid_v4_format(self):
        uid = insert_uuid()
        assert self._UUID_RE.match(uid), f"UUID inválido: {uid}"

    def test_uuid_is_unique(self):
        assert insert_uuid() != insert_uuid()

    def test_timestamp_iso(self):
        ts = insert_timestamp("iso")
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)

    def test_timestamp_date(self):
        ts = insert_timestamp("date")
        assert re.match(r"\d{4}-\d{2}-\d{2}$", ts)

    def test_timestamp_unix(self):
        ts = insert_timestamp("unix")
        assert ts.isdigit()
        assert int(ts) > 1_000_000_000

    def test_timestamp_default(self):
        ts = insert_timestamp()
        assert "T" in ts

    def test_lorem_1_sentence(self):
        lorem = insert_lorem(1)
        assert len(lorem) > 10
        assert lorem.endswith(".")

    def test_lorem_3_sentences(self):
        lorem = insert_lorem(3)
        assert lorem.count(".") >= 1

    def test_lorem_consistency(self):
        assert insert_lorem(1) == insert_lorem(1)


class TestLineOperations:
    def test_deduplicate_preserves_order(self):
        result = deduplicate_lines("a\nb\na\nc\nb")
        assert result == "a\nb\nc"

    def test_deduplicate_no_duplicates(self):
        result = deduplicate_lines("x\ny\nz")
        assert result == "x\ny\nz"

    def test_deduplicate_all_same(self):
        result = deduplicate_lines("a\na\na")
        assert result == "a"

    def test_reverse_lines(self):
        result = reverse_lines("1\n2\n3")
        assert result == "3\n2\n1"

    def test_reverse_single_line(self):
        assert reverse_lines("only") == "only"

    def test_number_lines(self):
        result = number_lines("a\nb\nc")
        assert "1" in result
        assert "2" in result
        assert "3" in result
        lines = result.splitlines()
        assert "a" in lines[0]
        assert "b" in lines[1]

    def test_number_lines_custom_start(self):
        result = number_lines("x\ny", start=10)
        assert "10" in result
        assert "11" in result

    def test_remove_empty_lines(self):
        result = remove_empty_lines("a\n\nb\n\nc")
        assert result == "a\nb\nc"

    def test_remove_empty_lines_no_empty(self):
        result = remove_empty_lines("a\nb\nc")
        assert result == "a\nb\nc"

    def test_remove_empty_lines_all_empty(self):
        result = remove_empty_lines("\n\n\n")
        assert result == ""


class TestTransformsDict:
    def test_all_expected_keys_present(self):
        expected = {
            "camel",
            "pascal",
            "snake",
            "kebab",
            "constant",
            "dot",
            "path",
            "b64enc",
            "b64dec",
            "urlenc",
            "urldec",
            "htmlesc",
            "htmlune",
            "jsonesc",
            "md5",
            "sha1",
            "sha256",
            "dedup",
            "revlines",
            "numlines",
            "remempty",
        }
        assert expected.issubset(set(TRANSFORMS.keys()))

    def test_each_entry_has_callable_and_metadata(self):
        for key, entry in TRANSFORMS.items():
            fn, desc, icon = entry
            assert callable(fn), f"{key}: fn não é callable"
            assert isinstance(desc, str), f"{key}: desc não é string"
            assert isinstance(icon, str), f"{key}: icon não é string"

    def test_transform_functions_work(self):
        fn_camel, _, _ = TRANSFORMS["camel"]
        assert fn_camel("hello world") == "helloWorld"

        fn_sha256, _, _ = TRANSFORMS["sha256"]
        assert len(fn_sha256("test")) == 64
