"""Unit tests for ANSI escape stripping (strip_ansi)."""

from infra_mcp.ssh import strip_ansi


def test_strips_color_codes():
    assert strip_ansi("\x1b[36mhello\x1b[0m") == "hello"


def test_strips_multiple_and_compound_codes():
    assert strip_ansi("\x1b[1;31mERR\x1b[0m \x1b[32mOK\x1b[0m") == "ERR OK"


def test_plain_text_unchanged():
    assert strip_ansi("no codes here\nline2") == "no codes here\nline2"


def test_empty_string():
    assert strip_ansi("") == ""
