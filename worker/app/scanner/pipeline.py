"""Two-stage LLM scanning pipeline with concurrent file processing."""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import ProcessingStatus, Severity, Stage1Result
from app.models.scan_file import ScanFile
from app.scanner.llm_client import LLMParseError, call_llm_json
from app.scanner.prompts import (
    MAX_FILE_CHARS,
    MAX_STAGE2_CONTEXT_CHARS,
    MAX_STAGE2_CONTEXT_SNIPPETS,
    MAX_STAGE2_INVESTIGATION_ITERATIONS,
    MAX_STAGE2_REQUESTS_PER_ITERATION,
    MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST,
    MAX_STAGE2_SNIPPET_WINDOW,
    STAGE1_SYSTEM_PROMPT,
    STAGE1_USER_TEMPLATE,
    STAGE2_SYSTEM_PROMPT,
    STAGE2_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


# ─── Data containers for pipeline results ────────────────────────────────

class FindingResult:
    """Intermediate representation of a single finding from stage 2."""

    __slots__ = (
        "file_path",
        "vulnerability_type",
        "severity",
        "line_number",
        "description",
        "explanation",
        "code_snippet",
    )

    def __init__(
        self,
        file_path: str,
        vulnerability_type: str,
        severity: str,
        line_number: int,
        description: str,
        explanation: str,
        code_snippet: str | None = None,
    ):
        self.file_path = file_path
        self.vulnerability_type = vulnerability_type
        self.severity = severity
        self.line_number = line_number
        self.description = description
        self.explanation = explanation
        self.code_snippet = code_snippet


@dataclass(slots=True)
class Stage2Outcome:
    """Normalised result of the iterative stage 2 investigation."""

    verdict: str
    findings: list[FindingResult]
    summary: str = ""


@dataclass(slots=True)
class ContextSnippet:
    """Additional repo context gathered during stage 2 investigation."""

    label: str
    content: str


# ─── File reading helper ────────────────────────────────────────────────

def _read_file(clone_path: Path, rel_path: str) -> str | None:
    """Read a file's contents, returning None if unreadable."""
    full = clone_path / rel_path
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n# ... [truncated for analysis]\n"
        return content
    except Exception as exc:
        logger.warning("Cannot read %s: %s", rel_path, exc)
        return None


def _add_line_numbers(content: str) -> str:
    """Prepend line numbers to each line of file content."""
    lines = content.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i:{width}d} | {line}" for i, line in enumerate(lines, 1))


# ─── Stage 1 ─────────────────────────────────────────────────────────────

def _run_stage1(file_path: str, content: str) -> Stage1Result:
    """Classify a single file as suspicious or not_suspicious."""
    numbered = _add_line_numbers(content)
    user_prompt = STAGE1_USER_TEMPLATE.format(file_path=file_path, file_content=numbered)
    try:
        result = call_llm_json(STAGE1_SYSTEM_PROMPT, user_prompt)
        classification = result.get("classification", "").lower()
        if classification == "suspicious":
            return Stage1Result.suspicious
        elif classification == "not_suspicious":
            return Stage1Result.not_suspicious
        else:
            logger.warning(
                "Unexpected stage1 classification '%s' for %s, treating as suspicious",
                classification,
                file_path,
            )
            return Stage1Result.suspicious
    except LLMParseError:
        logger.warning("Stage 1 parse failure for %s, treating as failed", file_path)
        return Stage1Result.failed


# ─── Stage 2 ─────────────────────────────────────────────────────────────

_VALID_SEVERITIES = {s.value for s in Severity}
_FINAL_VERDICTS = {"definitive_issue", "definitive_no_issue", "iteration_cap_reached"}
_REQUEST_KINDS = {"symbol_definition", "symbol_usage", "file"}


def _validate_finding(raw: dict[str, Any], file_path: str) -> FindingResult | None:
    """Validate and normalise a single finding dict from stage 2 output."""
    try:
        severity = str(raw.get("severity", "")).lower()
        if severity not in _VALID_SEVERITIES:
            severity = "medium"

        line_number = int(raw.get("line_number", 0))
        if line_number < 1:
            line_number = 1

        return FindingResult(
            file_path=file_path,
            vulnerability_type=str(raw.get("vulnerability_type", "Unknown")),
            severity=severity,
            line_number=line_number,
            description=str(raw.get("description", "")),
            explanation=str(raw.get("explanation", "")),
            code_snippet=raw.get("code_snippet"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid finding in %s: %s", file_path, exc)
        return None


def _list_python_files(clone_path: Path) -> list[str]:
    """Return a sorted list of repository-relative Python files."""
    return sorted(
        str(path.relative_to(clone_path))
        for path in clone_path.rglob("*.py")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    )


def _format_repo_index(paths: list[str], max_items: int = 200) -> str:
    """Format a repo file index for the LLM prompt."""
    if not paths:
        return "- <no Python files found>"

    display = paths[:max_items]
    lines = [f"- {path}" for path in display]
    if len(paths) > max_items:
        lines.append(f"- ... ({len(paths) - max_items} more Python files omitted)")
    return "\n".join(lines)


def _make_snippet(file_path: str, lines: list[str], start: int, end: int, label: str) -> ContextSnippet:
    numbered = "\n".join(
        f"{line_no:4d} | {lines[line_no - 1]}" for line_no in range(start, end + 1)
    )
    return ContextSnippet(
        label=label,
        content=f"File: {file_path}\nLines: {start}-{end}\n```\n{numbered}\n```",
    )


def _search_symbol_definitions(clone_path: Path, symbol: str) -> list[ContextSnippet]:
    if not symbol:
        return []

    pattern = re.compile(
        rf"^\s*(?:async\s+def|def|class)\s+{re.escape(symbol)}\b|^\s*{re.escape(symbol)}\s*=",
        re.MULTILINE,
    )
    snippets: list[ContextSnippet] = []
    for file_path in _list_python_files(clone_path):
        content = _read_file(clone_path, file_path)
        if not content:
            continue
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            if pattern.search(line):
                start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
                end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
                snippets.append(
                    _make_snippet(
                        file_path,
                        lines,
                        start,
                        end,
                        f"Definition of symbol '{symbol}'",
                    )
                )
                break
        if len(snippets) >= MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST:
            break
    return snippets


def _search_symbol_usages(clone_path: Path, symbol: str) -> list[ContextSnippet]:
    if not symbol:
        return []

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    snippets: list[ContextSnippet] = []
    for file_path in _list_python_files(clone_path):
        content = _read_file(clone_path, file_path)
        if not content:
            continue
        lines = content.splitlines()
        matches = [idx for idx, line in enumerate(lines, 1) if pattern.search(line)]
        if not matches:
            continue
        idx = matches[0]
        start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
        end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
        snippets.append(
            _make_snippet(
                file_path,
                lines,
                start,
                end,
                f"Usage of symbol '{symbol}'",
            )
        )
        if len(snippets) >= MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST:
            break
    return snippets


def _load_file_context(clone_path: Path, file_path: str) -> list[ContextSnippet]:
    content = _read_file(clone_path, file_path)
    if content is None:
        return []
    return [
        ContextSnippet(
            label=f"Requested file '{file_path}'",
            content=f"File: {file_path}\n```\n{_add_line_numbers(content)}\n```",
        )
    ]


def _normalise_requests(raw_requests: Any) -> list[dict[str, str]]:
    if not isinstance(raw_requests, list):
        return []

    normalised: list[dict[str, str]] = []
    for raw in raw_requests[:MAX_STAGE2_REQUESTS_PER_ITERATION]:
        if not isinstance(raw, dict):
            continue

        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in _REQUEST_KINDS:
            continue

        request = {
            "kind": kind,
            "symbol": str(raw.get("symbol", "")).strip(),
            "file_path": str(raw.get("file_path", "")).strip(),
            "why": str(raw.get("why", "")).strip(),
        }
        normalised.append(request)

    return normalised


def _resolve_stage2_requests(clone_path: Path, requests: list[dict[str, str]]) -> list[ContextSnippet]:
    snippets: list[ContextSnippet] = []
    seen: set[tuple[str, str, str]] = set()

    for request in requests:
        kind = request["kind"]
        if kind == "symbol_definition":
            resolved = _search_symbol_definitions(clone_path, request["symbol"])
        elif kind == "symbol_usage":
            resolved = _search_symbol_usages(clone_path, request["symbol"])
        else:
            resolved = _load_file_context(clone_path, request["file_path"])

        for snippet in resolved:
            key = (snippet.label, snippet.content[:200], request["why"])
            if key in seen:
                continue
            seen.add(key)
            snippets.append(snippet)
            if len(snippets) >= MAX_STAGE2_CONTEXT_SNIPPETS:
                return snippets

    return snippets


def _format_history(history: list[str]) -> str:
    if not history:
        return "- No previous iterations."
    return "\n".join(f"- {entry}" for entry in history)


def _format_supplemental_context(snippets: list[ContextSnippet]) -> str:
    if not snippets:
        return "- No additional repository context gathered yet."

    blocks: list[str] = []
    total_chars = 0
    for snippet in snippets[:MAX_STAGE2_CONTEXT_SNIPPETS]:
        block = f"[{snippet.label}]\n{snippet.content}"
        if total_chars + len(block) > MAX_STAGE2_CONTEXT_CHARS:
            blocks.append("[Context truncated due to size limits]")
            break
        blocks.append(block)
        total_chars += len(block)
    return "\n\n".join(blocks)


def _parse_stage2_outcome(raw: dict[str, Any], file_path: str) -> Stage2Outcome:
    status = str(raw.get("status", "")).strip().lower()
    summary = str(raw.get("summary", "")).strip()
    verdict = str(raw.get("final_verdict", "")).strip().lower()

    if status == "continue":
        return Stage2Outcome(verdict="continue", findings=[], summary=summary)

    if status != "final" or verdict not in _FINAL_VERDICTS:
        logger.warning("Unexpected stage2 control response for %s: %s", file_path, raw)
        return Stage2Outcome(verdict="iteration_cap_reached", findings=[], summary=summary)

    raw_findings = raw.get("findings", [])
    findings: list[FindingResult] = []
    if verdict == "definitive_issue" and isinstance(raw_findings, list):
        for raw_finding in raw_findings:
            if isinstance(raw_finding, dict):
                finding = _validate_finding(raw_finding, file_path)
                if finding is not None:
                    findings.append(finding)

    return Stage2Outcome(verdict=verdict, findings=findings, summary=summary)


def _run_stage2(clone_path: Path, file_path: str, content: str) -> Stage2Outcome:
    """Run iterative repo-aware security analysis on a suspicious file."""
    numbered = _add_line_numbers(content)
    repo_index = _format_repo_index(_list_python_files(clone_path))
    gathered_context: list[ContextSnippet] = []
    history: list[str] = []

    for iteration in range(1, MAX_STAGE2_INVESTIGATION_ITERATIONS + 1):
        must_finalize = iteration == MAX_STAGE2_INVESTIGATION_ITERATIONS
        user_prompt = STAGE2_USER_TEMPLATE.format(
            iteration=iteration,
            max_iterations=MAX_STAGE2_INVESTIGATION_ITERATIONS,
            must_finalize="yes" if must_finalize else "no",
            file_path=file_path,
            file_content=numbered,
            repo_index=repo_index,
            history=_format_history(history),
            supplemental_context=_format_supplemental_context(gathered_context),
        )
        result = call_llm_json(STAGE2_SYSTEM_PROMPT, user_prompt)
        outcome = _parse_stage2_outcome(result, file_path)

        if outcome.verdict != "continue":
            return outcome

        requests = _normalise_requests(result.get("requests", []))
        if not requests:
            history.append(f"Iteration {iteration}: model requested no additional context and remained uncertain.")
            continue

        resolved = _resolve_stage2_requests(clone_path, requests)
        history.append(
            f"Iteration {iteration}: {result.get('summary', 'No summary')} | requests={requests} | "
            f"resolved_snippets={len(resolved)}"
        )
        if resolved:
            gathered_context.extend(resolved)
            gathered_context = gathered_context[:MAX_STAGE2_CONTEXT_SNIPPETS]

    return Stage2Outcome(
        verdict="iteration_cap_reached",
        findings=[],
        summary="Investigation hit the iteration cap before a definitive conclusion was reached.",
    )


# ─── Per-file pipeline ──────────────────────────────────────────────────

def _process_file(
    clone_path: Path,
    scan_file: ScanFile,
) -> tuple[ScanFile, list[FindingResult]]:
    """Process a single file through stage 1 and (if suspicious) stage 2."""
    findings: list[FindingResult] = []

    content = _read_file(clone_path, scan_file.file_path)
    if content is None:
        scan_file.stage1_result = Stage1Result.failed
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = "Could not read file"
        return scan_file, findings

    stripped = content.strip()
    if not stripped or len(stripped) < 10:
        scan_file.stage1_result = Stage1Result.not_suspicious
        scan_file.processing_status = ProcessingStatus.skipped
        return scan_file, findings

    stage1 = _run_stage1(scan_file.file_path, content)
    scan_file.stage1_result = stage1

    if stage1 == Stage1Result.not_suspicious:
        scan_file.processing_status = ProcessingStatus.complete
        return scan_file, findings

    if stage1 == Stage1Result.failed:
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = "Stage 1 classification failed after retries"
        return scan_file, findings

    scan_file.stage2_attempted = True
    try:
        outcome = _run_stage2(clone_path, scan_file.file_path, content)
        findings = outcome.findings
        scan_file.processing_status = ProcessingStatus.complete
        if outcome.verdict == "iteration_cap_reached":
            summary = outcome.summary or "Stage 2 reached its investigation limit without certainty"
            scan_file.error_message = f"Stage 2 uncertainty: {summary}"
        else:
            scan_file.error_message = None
    except LLMParseError as exc:
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = f"Stage 2 failed: {exc}"

    return scan_file, findings


# ─── Async wrappers for concurrency ─────────────────────────────────────

async def _process_file_async(
    clone_path: Path,
    scan_file: ScanFile,
    semaphore: asyncio.Semaphore,
) -> tuple[ScanFile, list[FindingResult]]:
    """Run _process_file in a thread, gated by concurrency semaphore."""
    async with semaphore:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _process_file, clone_path, scan_file)


async def _run_pipeline_async(
    clone_path: Path,
    scan_files: list[ScanFile],
) -> list[FindingResult]:
    """Run the two-stage pipeline on all files with bounded concurrency."""
    semaphore = asyncio.Semaphore(settings.max_concurrent_files)
    tasks = [_process_file_async(clone_path, sf, semaphore) for sf in scan_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_findings: list[FindingResult] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Unexpected error processing %s: %s",
                scan_files[i].file_path,
                result,
            )
            scan_files[i].processing_status = ProcessingStatus.failed
            scan_files[i].error_message = f"Unexpected error: {result}"
        else:
            _, findings = result
            all_findings.extend(findings)

    return all_findings


# ─── Public entry point ─────────────────────────────────────────────────

def run_scan_pipeline(
    db: Session,
    scan_id: uuid.UUID,
    clone_path: Path,
    scan_files: list[ScanFile],
) -> list[FindingResult]:
    """Execute the full two-stage LLM scanning pipeline."""
    if not scan_files:
        logger.info("Scan %s: no files to process", scan_id)
        return []

    logger.info("Scan %s: starting LLM pipeline on %d files", scan_id, len(scan_files))
    all_findings = asyncio.run(_run_pipeline_async(clone_path, scan_files))
    db.flush()

    suspicious_count = sum(1 for sf in scan_files if sf.stage1_result == Stage1Result.suspicious)
    failed_count = sum(1 for sf in scan_files if sf.processing_status == ProcessingStatus.failed)
    logger.info(
        "Scan %s pipeline complete: %d files, %d suspicious, %d findings, %d file failures",
        scan_id,
        len(scan_files),
        suspicious_count,
        len(all_findings),
        failed_count,
    )

    return all_findings
