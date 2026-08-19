from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_dev_researcher.domain.runs import DEFAULT_OUTPUT_MODE, OutputMode, ResearchRequest


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


class TestResearchRequestOutputMode:
    """output_mode 契约：默认 medium，显式值 short/medium/long。"""

    def test_default_is_medium(self) -> None:
        request = _make_request("q")
        assert request.output_mode == DEFAULT_OUTPUT_MODE
        assert request.output_mode == OutputMode.MEDIUM

    def test_explicit_short(self) -> None:
        request = ResearchRequest(question="q", output_mode="short")
        assert request.output_mode == OutputMode.SHORT

    def test_explicit_long_roundtrips_via_json(self) -> None:
        request = ResearchRequest(question="q", output_mode=OutputMode.LONG)
        dumped = request.model_dump(mode="json")
        reparsed = ResearchRequest.model_validate(dumped)
        assert reparsed.output_mode == OutputMode.LONG

    def test_legacy_json_without_output_mode_defaults_to_medium(self) -> None:
        request = ResearchRequest.model_validate({"question": "q"})
        assert request.output_mode == OutputMode.MEDIUM

    def test_invalid_output_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResearchRequest(question="q", output_mode="turbo")
