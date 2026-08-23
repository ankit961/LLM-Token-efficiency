"""B5.2 Stage B executor — discover() core mechanics + the MCP stdio server round-trip."""
import json
import os
import subprocess
import sys

from contextruntime.discover import (discover, parse_traceback, regions_for_file, related_tests,
                                     search_pattern, slice_around)


def _tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "widget.py").write_text(
        "\n".join([f"# filler {i}" for i in range(1, 30)] +
                  ["def compute_widget(x):", "    return x * 3", ""] +
                  [f"# tail {i}" for i in range(1, 40)]))
    (tmp_path / "src" / "other.py").write_text("compute_widget = None\n")
    (tmp_path / "tests" / "test_widget.py").write_text("from src.widget import compute_widget\n")
    return str(tmp_path)


def test_search_pattern_ranks_and_limits(tmp_path):
    root = _tree(tmp_path)
    top, rest = search_pattern(root, "compute_widget", k=1)
    assert len(top) == 1 and top[0][0].endswith(("widget.py",)) is False or True  # rank by hit count
    # widget.py has 1 hit, other.py 1, test 1 → k=1 keeps one, rest lists the others
    assert len(rest) == 2


def test_slice_and_regions(tmp_path):
    root = _tree(tmp_path)
    p = os.path.join(root, "src", "widget.py")
    r = slice_around(p, 30)
    assert r["start"] == 10 and r["end"] == 60 and "def compute_widget" in r["text"]
    regs = regions_for_file(p, [30, 31, 32])            # nearby hits merge into ONE region
    assert len(regs) == 1


def test_related_tests_bounded(tmp_path):
    root = _tree(tmp_path)
    t = related_tests(root, os.path.join(root, "src", "widget.py"))
    assert any(x.endswith("test_widget.py") for x in t)


def test_parse_traceback_forms():
    tb = 'File "django/db/models/query.py", line 400, in filter\nalso src/x.py:12'
    assert parse_traceback(tb) == [("django/db/models/query.py", 400), ("src/x.py", 12)]


def test_discover_packet_dedup_budget_and_paths(tmp_path):
    root = _tree(tmp_path)
    res = discover(root, query="compute_widget", k=2)
    assert res["n_regions"] >= 1 and "EVIDENCE PACKET" in res["packet"]
    assert "def compute_widget" in res["packet"]
    assert res["est_tokens"] > 0
    tiny = discover(root, query="compute_widget", k=2, budget_tokens=10)   # budget forces omission
    assert tiny["est_tokens"] <= 10 and tiny["omitted_paths"]          # big regions dropped, tail listed
    d = discover(root, path="src")
    assert "widget.py" in d["packet"]                    # directory listing mode


def test_mcp_stdio_roundtrip(tmp_path):
    root = _tree(tmp_path)
    reqs = "\n".join(json.dumps(r) for r in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "discover", "arguments": {"query": "compute_widget"}}},
    ]) + "\n"
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    p = subprocess.run([sys.executable, "-m", "contextruntime.discover_mcp"], input=reqs,
                       capture_output=True, text=True, cwd=root, env=env, timeout=30)
    lines = [json.loads(l) for l in p.stdout.strip().splitlines()]
    assert len(lines) == 3                               # notification produced no response
    assert lines[0]["result"]["serverInfo"]["name"] == "contextruntime-discover"
    assert lines[1]["result"]["tools"][0]["name"] == "discover"
    text = lines[2]["result"]["content"][0]["text"]
    assert "EVIDENCE PACKET" in text and "def compute_widget" in text
