from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.storage.chunk_locator import CharToLineIndex


def test_line_of_basic():
    text = "line1\nline2\nline3"
    index = CharToLineIndex.build(text)
    # line_starts = [0, 6, 12]
    assert index.line_of(0) == 1
    assert index.line_of(5) == 1  # offset 5 是 '\n'，属于第 1 行
    assert index.line_of(6) == 2  # offset 6 是 line2 起点
    assert index.line_of(11) == 2  # '\n' 结束第 2 行
    assert index.line_of(12) == 3


def test_line_range_across_lines():
    text = "aaaa\nbbbb\ncccc"
    index = CharToLineIndex.build(text)
    # line_starts = [0, 5, 10]
    # 'bbbb' 从 offset 5 开始，长度 4 → 行 2
    start, end = index.line_range(5, 9)
    assert (start, end) == (2, 2)
    # 跨越两行：offset 3-8 → 行 1 到 2
    start, end = index.line_range(3, 8)
    assert (start, end) == (1, 2)


def test_line_of_out_of_bounds():
    text = "abc\ndef"
    index = CharToLineIndex.build(text)
    assert index.line_of(100) == 2


def test_empty_text():
    index = CharToLineIndex.build("")
    assert index.line_of(0) == 1
    assert index.line_range(0, 0) == (1, 1)
