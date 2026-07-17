import pytest
from joki.display import _numbered

def test_numbered_single_line():
    assert _numbered("hello") == "1: hello"

def test_numbered_multiple_lines():
    text = "line1\nline2\nline3"
    expected = "1: line1\n2: line2\n3: line3"
    assert _numbered(text) == expected

def test_numbered_empty():
    assert _numbered("") == "1: "
