"""Tests for the two-stage LLM scanning pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.enums import ProcessingStatus, Stage1Result
from app.models.scan_file import ScanFile
from app.scanner.llm_client import LLMParseError, _extract_json
from app.scanner.pipeline import (
    FindingResult,
    Stage2Outcome,
    _process_file,
    _run_stage1,
    _run_stage2,
    _validate_finding,
)

# ─── _extract_json tests ────────────────────────────────────────────────


class TestExtractJson:
    def test_plain_json(self):
        raw = '{"classification": "suspicious", "reason": "uses eval"}'
        result = _extract_json(raw)
        assert result["classification"] == "suspicious"

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"classification": "not_suspicious", "reason": "constants"}\n```'
        result = _extract_json(raw)
        assert result["classification"] == "not_suspicious"

    def test_json_with_whitespace(self):
        raw = '  \n  {"key": "value"}\n  '
        result = _extract_json(raw)
        assert result["key"] == "value"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")


# ─── _validate_finding tests ────────────────────────────────────────────


class TestValidateFinding:
    def test_valid_finding(self):
        raw = {
            "file_path": "app.py",
            "vulnerability_type": "SQL Injection",
            "severity": "high",
            "line_number": 42,
            "description": "User input in query",
            "explanation": "Attacker can inject SQL",
            "code_snippet": "cursor.execute(f'SELECT * FROM {user_input}')",
        }
        f = _validate_finding(raw, "app.py", {"app.py"})
        assert f is not None
        assert f.file_path == "app.py"
        assert f.vulnerability_type == "SQL Injection"
        assert f.severity == "high"
        assert f.line_number == 42

    def test_invalid_severity_defaults_to_medium(self):
        raw = {
            "vulnerability_type": "XSS",
            "severity": "super_critical",
            "line_number": 10,
            "description": "desc",
            "explanation": "exp",
        }
        f = _validate_finding(raw, "app.py", {"app.py"})
        assert f is not None
        assert f.severity == "medium"

    def test_zero_line_number_defaults_to_1(self):
        raw = {
            "vulnerability_type": "XSS",
            "severity": "low",
            "line_number": 0,
            "description": "desc",
            "explanation": "exp",
        }
        f = _validate_finding(raw, "app.py", {"app.py"})
        assert f is not None
        assert f.line_number == 1


# ─── Stage 1 tests ──────────────────────────────────────────────────────


class TestRunStage1:
    @patch("app.scanner.pipeline.call_llm_json")
    def test_suspicious(self, mock_call):
        mock_call.return_value = {"classification": "suspicious", "reason": "uses eval"}
        result = _run_stage1("app.py", "eval(input())")
        assert result == Stage1Result.suspicious

    @patch("app.scanner.pipeline.call_llm_json")
    def test_not_suspicious(self, mock_call):
        mock_call.return_value = {"classification": "not_suspicious", "reason": "constants"}
        result = _run_stage1("constants.py", "X = 1")
        assert result == Stage1Result.not_suspicious

    @patch("app.scanner.pipeline.call_llm_json")
    def test_parse_failure(self, mock_call):
        mock_call.side_effect = LLMParseError("bad json")
        result = _run_stage1("app.py", "code")
        assert result == Stage1Result.failed


# ─── Stage 2 tests ──────────────────────────────────────────────────────


class TestRunStage2:
    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_returns_findings(self, mock_call, mock_list):
        mock_list.return_value = ["app.py", "db.py"]
        mock_call.return_value = {
            "status": "final",
            "summary": "Confirmed SQL injection sink.",
            "hypothesis": "user input reaches execute",
            "requests": [],
            "final_verdict": "definitive_issue",
            "findings": [
                {
                    "file_path": "db.py",
                    "vulnerability_type": "SQL Injection",
                    "severity": "high",
                    "line_number": 10,
                    "description": "desc",
                    "explanation": "exp",
                    "code_snippet": "code",
                }
            ],
        }
        outcome = _run_stage2(Path("/tmp/clone"), "app.py", "code")
        assert outcome.verdict == "definitive_issue"
        assert len(outcome.findings) == 1
        assert outcome.findings[0].file_path == "db.py"
        assert outcome.findings[0].vulnerability_type == "SQL Injection"

    @patch("app.scanner.pipeline._resolve_stage2_requests")
    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_iterates_for_more_context(self, mock_call, mock_list, mock_resolve):
        mock_list.return_value = ["app.py", "helpers.py"]
        mock_resolve.return_value = []
        mock_call.side_effect = [
            {
                "status": "continue",
                "summary": "Need helper definition.",
                "hypothesis": "maybe sanitized",
                "requests": [
                    {
                        "kind": "symbol_definition",
                        "symbol": "sanitize",
                        "file_path": "",
                        "why": "Need to inspect sanitizer implementation",
                    }
                ],
                "final_verdict": "iteration_cap_reached",
                "findings": [],
            },
            {
                "status": "final",
                "summary": "Sanitizer escapes dangerous characters.",
                "hypothesis": "safe",
                "requests": [],
                "final_verdict": "definitive_no_issue",
                "findings": [],
            },
        ]

        outcome = _run_stage2(Path("/tmp/clone"), "app.py", "code")

        assert outcome.verdict == "definitive_no_issue"
        assert outcome.findings == []
        assert mock_resolve.called
        assert mock_call.call_count == 2

    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_parse_failure_raises(self, mock_call, mock_list):
        mock_list.return_value = ["app.py"]
        mock_call.side_effect = LLMParseError("bad")
        with pytest.raises(LLMParseError):
            _run_stage2(Path("/tmp/clone"), "app.py", "code")

    def test_unknown_finding_file_path_falls_back_to_original_file(self):
        raw = {
            "file_path": "missing.py",
            "vulnerability_type": "XSS",
            "severity": "high",
            "line_number": 33,
            "description": "desc",
            "explanation": "exp",
        }

        finding = _validate_finding(raw, "app.py", {"app.py", "helpers.py"})

        assert finding is not None
        assert finding.file_path == "app.py"
        assert finding.line_number == 33


# ─── _process_file integration tests ────────────────────────────────────


class TestProcessFile:
    def _make_scan_file(self, file_path: str = "app.py") -> MagicMock:
        """Create a mock ScanFile-like object for testing."""
        sf = MagicMock(spec=ScanFile)
        sf.file_path = file_path
        sf.stage1_result = None
        sf.stage2_attempted = False
        sf.processing_status = None
        sf.error_message = None
        return sf

    @patch("app.scanner.pipeline._run_stage2")
    @patch("app.scanner.pipeline._run_stage1")
    @patch("app.scanner.pipeline._read_file")
    def test_suspicious_file_gets_stage2(self, mock_read, mock_s1, mock_s2):
        mock_read.return_value = "import os\nos.system(input())"
        mock_s1.return_value = Stage1Result.suspicious
        mock_s2.return_value = Stage2Outcome(
            verdict="definitive_issue",
            findings=[FindingResult("app.py", "Command Injection", "critical", 2, "d", "e", "c")],
            summary="Confirmed dangerous sink.",
        )

        sf = self._make_scan_file()
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.stage1_result == Stage1Result.suspicious
        assert result_sf.stage2_attempted is True
        assert result_sf.processing_status == ProcessingStatus.complete
        assert len(findings) == 1

    @patch("app.scanner.pipeline._run_stage1")
    @patch("app.scanner.pipeline._read_file")
    def test_not_suspicious_skips_stage2(self, mock_read, mock_s1):
        mock_read.return_value = "X = 42\nY = 100\nZ = 200"
        mock_s1.return_value = Stage1Result.not_suspicious

        sf = self._make_scan_file("constants.py")
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.stage1_result == Stage1Result.not_suspicious
        assert result_sf.processing_status == ProcessingStatus.complete
        assert result_sf.stage2_attempted is False
        assert findings == []

    @patch("app.scanner.pipeline._read_file")
    def test_unreadable_file_fails(self, mock_read):
        mock_read.return_value = None

        sf = self._make_scan_file()
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.processing_status == ProcessingStatus.failed
        assert findings == []

    @patch("app.scanner.pipeline._read_file")
    def test_empty_file_skipped(self, mock_read):
        mock_read.return_value = ""

        sf = self._make_scan_file("__init__.py")
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.stage1_result == Stage1Result.not_suspicious
        assert result_sf.processing_status == ProcessingStatus.skipped

    @patch("app.scanner.pipeline._run_stage2")
    @patch("app.scanner.pipeline._run_stage1")
    @patch("app.scanner.pipeline._read_file")
    def test_stage2_failure_marks_file_failed(self, mock_read, mock_s1, mock_s2):
        mock_read.return_value = "import os\nos.system(input())"
        mock_s1.return_value = Stage1Result.suspicious
        mock_s2.side_effect = LLMParseError("bad json after retries")

        sf = self._make_scan_file()
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.processing_status == ProcessingStatus.failed
        assert result_sf.stage2_attempted is True
        assert "Stage 2 failed" in result_sf.error_message
        assert findings == []

    @patch("app.scanner.pipeline._run_stage2")
    @patch("app.scanner.pipeline._run_stage1")
    @patch("app.scanner.pipeline._read_file")
    def test_iteration_cap_records_uncertainty(self, mock_read, mock_s1, mock_s2):
        mock_read.return_value = "import os\nos.system(input())"
        mock_s1.return_value = Stage1Result.suspicious
        mock_s2.return_value = Stage2Outcome(
            verdict="iteration_cap_reached",
            findings=[],
            summary="Could not prove whether wrapper sanitizes input.",
        )

        sf = self._make_scan_file()
        result_sf, findings = _process_file(Path("/tmp/clone"), sf)

        assert result_sf.processing_status == ProcessingStatus.complete
        assert result_sf.stage2_attempted is True
        assert "Stage 2 uncertainty" in result_sf.error_message
        assert findings == []
