from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_dev_researcher.domain.runs import ResearchRequest


def _make_request(question: str) -> ResearchRequest:
    return ResearchRequest(question=question)


class TestResearchRequestQuestionValidation:
    """ResearchRequest.question strips input and only rejects blank questions."""

    def test_long_question_over_4000_chars_is_accepted(self) -> None:
        question = "research question " + ("long content " * 1000) + "x"
        assert len(question) > 4000
        request = _make_request(question)
        assert request.question == question

    def test_short_non_blank_question_is_accepted(self) -> None:
        request = _make_request("short")
        assert request.question == "short"

    def test_blank_question_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_request("          ")

    def test_empty_question_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_request("")

    def test_strip_validation_still_applies(self) -> None:
        question = "  a  "
        request = _make_request(question)
        assert request.question == "a"

    def test_whitespace_padded_short_question_is_accepted_after_strip(self) -> None:
        question = "  short  "
        request = _make_request(question)
        assert request.question == "short"

    def test_single_char_question_is_accepted(self) -> None:
        request = _make_request("a")
        assert request.question == "a"
