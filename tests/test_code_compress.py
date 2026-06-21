from headroom_ja import compress, detect
from headroom_ja.text_compress import compress_code

_PY = '''\
import os
from typing import List


def add(a: int, b: int) -> int:
    """Add two numbers."""
    total = a + b
    for _ in range(10):
        total += 0
    return total


class Service:
    name = "svc"

    def run(self, x):
        result = x * 2
        print(result)
        return result
'''


def test_keeps_signatures_drops_bodies():
    out, total, kept = compress_code(_PY)
    # imports + signatures preserved
    assert "import os" in out
    assert "from typing import List" in out
    assert "def add(a: int, b: int) -> int:" in out
    assert "class Service:" in out
    assert "def run(self, x):" in out
    # body statements removed
    assert "total = a + b" not in out
    assert "print(result)" not in out
    assert "..." in out
    assert kept < total


def test_router_detects_code():
    assert detect(_PY) == "code"


def test_compress_routes_code():
    big = _PY + "\n\n" + "\n".join(
        f"def f{i}(x):\n    y = x + {i}\n    return y\n" for i in range(40))
    r = compress(big)
    assert r.content_type == "code"
    assert r.compressed_tokens < r.original_tokens
    assert "def f0(x):" in r.text
    assert "y = x + 0" not in r.text  # body dropped


def test_plain_text_not_code():
    assert detect("これはただの日本語の文章です。\n二行目。\n三行目。") == "text"


def test_docstring_mode_first_line():
    out, _, _ = compress_code(_PY, docstring_mode="first_line")
    assert "Add two numbers." in out   # docstring hint kept
    assert "total = a + b" not in out  # body still dropped
    assert "..." in out


def test_docstring_mode_remove_is_default():
    out, _, _ = compress_code(_PY)      # default = remove
    assert "Add two numbers." not in out
    assert "def add(a: int, b: int) -> int:" in out


def test_docstring_mode_full():
    src = ('def f(x):\n    """Line one.\n    Line two.\n    """\n'
           '    a = 1\n    return a\n')
    out, _, _ = compress_code(src, docstring_mode="full")
    assert "Line one." in out and "Line two." in out
    assert "a = 1" not in out
