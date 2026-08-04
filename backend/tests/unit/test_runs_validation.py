from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_dev_researcher.domain.runs import ResearchRequest


def _make_request(question: str) -> ResearchRequest:
    return ResearchRequest(question=question)


class TestResearchRequestQuestionValidation:
    """ResearchRequest.question 校验：仅限 min_length=10 与 strip 校验（无 max_length）。"""

    def test_long_question_over_4000_chars_is_accepted(self) -> None:
        """超过 4000 字符的研究问题应可正常提交（issue 5：解除字数限制）。"""
        question = "研究问题" + "很长的内容" * 1000  # 5000+ 字符
        assert len(question) > 4000
        request = _make_request(question)
        assert request.question == question

    def test_min_length_10_is_still_enforced(self) -> None:
        """不足 10 字符的问题仍应被拒绝。"""
        with pytest.raises(ValidationError) as exc_info:
            _make_request("太短了")
        errors = exc_info.value.errors()
        assert any("at least 10 characters" in str(err.get("msg", "")) for err in errors)

    def test_blank_question_is_rejected(self) -> None:
        """空白问题仍应被拒绝（strip 后不足 10 字符）。"""
        with pytest.raises(ValidationError):
            _make_request("          ")

    def test_strip_validation_still_applies(self) -> None:
        """问题首尾空白仍会被去除（strip 校验保留）。"""
        question = "  这是一个用于验证 strip 行为的足够长的研究问题内容  "
        request = _make_request(question)
        assert request.question == question.strip()
        assert not request.question.startswith(" ")
        assert not request.question.endswith(" ")

    def test_whitespace_padded_short_question_is_rejected_after_strip(self) -> None:
        """原始长度超过 10 但 strip 后不足 10 字符的问题仍应被拒绝。"""
        question = "  一二三四五六七  "  # 原始 11 字符，strip 后仅 7 字符
        assert len(question) >= 10
        with pytest.raises(ValidationError) as exc_info:
            _make_request(question)
        errors = exc_info.value.errors()
        assert any("at least 10 characters" in str(err.get("msg", "")) for err in errors)

    def test_exactly_10_chars_is_accepted(self) -> None:
        """恰好 10 字符（strip 后）的问题应通过。"""
        question = "一二三四五六七八九十"  # 10 个字符
        assert len(question) == 10
        request = _make_request(question)
        assert request.question == question
