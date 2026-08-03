from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CharToLineIndex:
    """将字符串的字符偏移映射到行号（1-based）的索引。"""

    line_starts: tuple[int, ...]

    @classmethod
    def build(cls, text: str) -> "CharToLineIndex":
        starts: list[int] = [0]
        for idx, ch in enumerate(text):
            if ch == "\n":
                starts.append(idx + 1)
        return cls(tuple(starts))

    def line_of(self, char_offset: int) -> int:
        """返回 char_offset 所在的行号（1-based）；越界时返回最后一行。"""
        starts = self.line_starts
        if not starts:
            return 1
        # 二分查找最后一个 line_start <= offset 的位置。
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= char_offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def line_range(self, start_char: int, end_char: int) -> tuple[int, int]:
        """返回 [start_char, end_char) 对应的行号范围 [line_start, line_end]。"""
        line_start = self.line_of(start_char)
        line_end = self.line_of(max(end_char - 1, start_char))
        return line_start, line_end
