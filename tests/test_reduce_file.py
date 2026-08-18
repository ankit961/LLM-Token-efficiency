"""B2.1 — file-read residency reducer tests. Structure preserved, budget respected, exact recovery."""
from contextruntime.reducers.base import tokens
from contextruntime.reducers.library import _is_structural, reduce_file

_PYFILE = (
    '"""Module docstring."""\n'
    "import os\n"
    "from x import y\n"
    "\n"
    "MAX = 10\n"
    "\n"
    "@decorator\n"
    "def foo(a, b):\n"
    "    x = a + b\n"          # body — droppable
    "    for i in range(x):\n"
    "        x += helper(i)\n"
    "    return x\n"
    "\n"
    "class Bar(Base):\n"
    "    def method(self):\n"
    "        return self.compute() * 2\n"
) + "\n".join(f"    filler_{i} = {i}" for i in range(400))     # a big droppable body


def test_structural_detection_including_lineno_prefix():
    assert _is_structural("import os") and _is_structural("from a import b")
    assert _is_structural("def foo():") and _is_structural("class Bar:") and _is_structural("@dec")
    assert _is_structural("   12→def foo():")           # Claude Read line-number prefix
    assert _is_structural("MAX_SIZE = 10")                    # top-level constant
    assert not _is_structural("    x = a + b")               # indented body assignment
    assert not _is_structural("        return x")


def test_reduces_big_file_keeps_skeleton_and_handle():
    r = reduce_file(_PYFILE, {}, budget_tokens=256)
    assert r.reduced_tokens < tokens(_PYFILE)                 # beneficial on a big file
    assert "full output:" in r.reduced_text and r.handle      # exact recovery handle always present
    # signatures survive; a filler body line does not
    assert "def foo(a, b):" in r.reduced_text and "class Bar(Base):" in r.reduced_text
    assert "filler_200 = 200" not in r.reduced_text
    assert "…" in r.reduced_text                         # elision marker for dropped bodies


def test_budget_respected():
    r = reduce_file(_PYFILE, {}, budget_tokens=128)
    assert r.reduced_tokens <= 128 * 1.3                      # ~budget (small overhead tolerated)
    r2 = reduce_file(_PYFILE, {}, budget_tokens=512)
    assert r2.reduced_tokens >= r.reduced_tokens              # a larger budget keeps more


def test_small_file_not_beneficial_left_to_caller():
    tiny = "import os\nprint(os.getcwd())\n"
    r = reduce_file(tiny, {}, budget_tokens=512)
    # a tiny file's skeleton+handle is NOT smaller than raw ⇒ caller's beneficial guard passes it
    # through (same contract as reduce_search); reduce_file itself never fabricates a saving.
    assert r.reduced_tokens >= tokens(tiny)


def test_empty_file():
    r = reduce_file("", {})
    assert r.handle and r.reducer == "file"
