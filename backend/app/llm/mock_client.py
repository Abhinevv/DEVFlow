"""Deterministic scripted provider for demos, tests and offline development.

It does not interpret requirements: every project gets the same "Notes API" plan. Scenarios
(selected with the project constraint `mock_scenario`) exercise specific workflow paths:

- default:    T2's first implementation has a bug, the debugger fixes it, the reviewer requests
              one input-validation revision, then accepts.
- clean:      no injected bug and no review revision.
- unfixable:  the debugger never fixes the bug, so retries are exhausted and the workflow blocks.
- unsafe:     the developer also tries to write outside the workspace (rejected by the sandbox).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from app.agents.schemas import DebuggerOutput, DeveloperOutput, FileChange, Plan, ReviewReport
from app.llm import mock_scenario as s
from app.llm.base import LLMClient, LLMError, LLMResult, Usage


def _f(path: str, content: str, action: str = "create") -> FileChange:
    return FileChange(path=path, action=action, content=content)


PLAN = {
    "summary": "Mock provider plan: a FastAPI Notes service with SQLite persistence, CRUD endpoints and search. "
    "(The mock provider does not interpret requirements; configure the Anthropic provider for real planning.)",
    "functional_requirements": [
        "Create a note with a title and optional body",
        "List all notes and filter them with a case-insensitive text search",
        "Fetch, update and delete a single note by id",
        "Return 404 for operations on notes that do not exist",
    ],
    "non_functional_requirements": [
        "All behaviour covered by automated pytest tests",
        "Tests use an isolated in-memory database",
    ],
    "assumptions": ["Single-user service without authentication", "SQLite is sufficient for persistence"],
    "constraints": ["Only approved dependencies: fastapi, pydantic, sqlalchemy, httpx, pytest, uvicorn"],
    "clarifications_needed": [],
    "acceptance_criteria": [
        "POST /notes returns 201 with the created note",
        "GET /notes?q= filters by title or body, case-insensitively",
        "DELETE /notes/{id} returns 204, or 404 when the note does not exist",
        "The full test suite passes",
    ],
    "components": [
        {"name": "database", "responsibility": "Engine, sessions and declarative base", "interfaces": ["get_db()", "Base", "engine"]},
        {"name": "models", "responsibility": "Note ORM model", "interfaces": ["Note"]},
        {"name": "api", "responsibility": "HTTP endpoints", "interfaces": ["/health", "/notes", "/notes/{id}"]},
    ],
    "tech_stack": ["Python 3.12", "FastAPI", "SQLAlchemy 2", "Pydantic 2", "pytest"],
    "directory_structure": [
        "app/__init__.py", "app/database.py", "app/models.py", "app/schemas.py", "app/main.py",
        "tests/conftest.py", "tests/test_models.py", "tests/test_api.py", "tests/test_search.py", "README.md",
    ],
    "tasks": [
        {
            "id": "T1", "title": "Database layer and Note model",
            "description": "Create the SQLAlchemy engine/session setup and the Note model with test fixtures.",
            "depends_on": [], "expected_outputs": ["app/database.py", "app/models.py", "tests/conftest.py", "tests/test_models.py"],
            "acceptance_criteria": ["A Note can be persisted with default body and timestamp"],
        },
        {
            "id": "T2", "title": "Notes CRUD API",
            "description": "Implement schemas and CRUD endpoints with 404 handling, plus API tests.",
            "depends_on": ["T1"], "expected_outputs": ["app/schemas.py", "app/main.py", "tests/conftest.py", "tests/test_api.py"],
            "acceptance_criteria": ["CRUD endpoints behave as specified", "Missing notes return 404 on get, update and delete"],
        },
        {
            "id": "T3", "title": "Search and documentation",
            "description": "Add case-insensitive ?q= search to GET /notes and write the README.",
            "depends_on": ["T2"], "expected_outputs": ["app/main.py", "tests/test_search.py", "README.md"],
            "acceptance_criteria": ["Search matches title or body case-insensitively", "README documents setup and endpoints"],
        },
    ],
}


class MockClient(LLMClient):
    provider = "mock"
    model = "mock-scripted"

    def __init__(self, delay_seconds: float = 0.0):
        self.delay_seconds = delay_seconds

    def generate(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        output_model: type[BaseModel],
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        meta = meta or {}
        started = time.monotonic()
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        scenario = meta.get("mock_scenario", "default")
        handler = getattr(self, f"_{role}", None)
        if handler is None:
            raise LLMError(f"mock provider has no script for role {role!r}")
        output = output_model.model_validate(handler(meta, scenario))
        text = output.model_dump_json()
        usage = Usage(input_tokens=(len(system) + len(prompt)) // 4, output_tokens=len(text) // 4)
        return LLMResult(output=output, usage=usage, model=self.model, latency_ms=int((time.monotonic() - started) * 1000), cost_usd=0.0)

    # --- roles -------------------------------------------------------------------------

    def _planner(self, meta: dict, scenario: str) -> dict:
        plan = Plan.model_validate(PLAN).model_dump()
        if meta.get("feedback"):
            plan["assumptions"] = plan["assumptions"] + [f"Revision requested: {meta['feedback']}"]
        return plan

    def _developer(self, meta: dict, scenario: str) -> dict:
        if meta.get("mode") == "revise":
            return DeveloperOutput(
                summary="Added length validation to note titles and bodies, with tests.",
                files=[_f("app/schemas.py", s.SCHEMAS_VALIDATED_PY, "modify"), _f("tests/test_validation.py", s.TEST_VALIDATION_PY)],
                notes=[],
            ).model_dump()

        key = meta.get("task_key")
        buggy = scenario in ("default", "unfixable", "unsafe")
        if key == "T1":
            files = [
                _f("app/database.py", s.DATABASE_PY),
                _f("app/models.py", s.MODELS_PY),
                _f("tests/conftest.py", s.CONFTEST_T1),
                _f("tests/test_models.py", s.TEST_MODELS_PY),
            ]
            if scenario == "unsafe":
                files.append(_f("../outside_workspace.py", "print('escaped')\n"))
            return DeveloperOutput(summary="Database layer, Note model and fixtures.", files=files, notes=[]).model_dump()
        if key == "T2":
            return DeveloperOutput(
                summary="Note schemas, CRUD endpoints and API tests.",
                files=[
                    _f("app/schemas.py", s.SCHEMAS_PY),
                    _f("app/main.py", s.MAIN_T2_BUGGY if buggy else s.MAIN_T2_FIXED),
                    _f("tests/conftest.py", s.CONFTEST_T2, "modify"),
                    _f("tests/test_api.py", s.TEST_API_PY),
                ],
                notes=[],
            ).model_dump()
        if key == "T3":
            return DeveloperOutput(
                summary="Case-insensitive search and README.",
                files=[_f("app/main.py", s.MAIN_T3, "modify"), _f("tests/test_search.py", s.TEST_SEARCH_PY), _f("README.md", s.README_MD)],
                notes=[],
            ).model_dump()
        raise LLMError(f"mock developer has no script for task {key!r}")

    def _debugger(self, meta: dict, scenario: str) -> dict:
        if scenario == "unfixable":
            return DebuggerOutput(
                root_cause="Unclear; the failure could not be localised.",
                explanation="Re-emitting the current implementation unchanged.",
                confidence="low",
                files=[_f("app/main.py", s.MAIN_T2_BUGGY, "modify")],
                validation_steps=["Re-run tests/test_api.py"],
            ).model_dump()
        return DebuggerOutput(
            root_cause="delete_note silently returns 204 when the note does not exist instead of raising 404.",
            explanation="Use the shared _get_or_404 helper in delete_note so missing notes produce 404, matching get and update.",
            confidence="high",
            files=[_f("app/main.py", s.MAIN_T2_FIXED, "modify")],
            validation_steps=["Re-run tests/test_api.py::test_delete_missing_note_returns_404", "Run the full suite for regressions"],
        ).model_dump()

    def _reviewer(self, meta: dict, scenario: str) -> dict:
        coverage = [
            {"requirement": r, "status": "met", "evidence": "Covered by passing tests"} for r in PLAN["functional_requirements"]
        ]
        if meta.get("review_round", 0) == 0 and scenario == "default":
            return ReviewReport(
                summary="Implementation meets the functional requirements; input validation is missing.",
                requirements_coverage=coverage,
                findings=[{
                    "severity": "medium", "category": "security", "file": "app/schemas.py", "line": 8,
                    "description": "NoteCreate.title accepts empty and unbounded strings, so clients can store blank or very large titles.",
                    "recommendation": "Constrain title length (1-200) and body length, and add tests for rejected input.",
                    "actionable": True,
                }],
                recommendation="revise",
            ).model_dump()
        return ReviewReport(
            summary="Implementation meets the requirements with adequate tests.",
            requirements_coverage=coverage,
            findings=[{
                "severity": "info", "category": "maintainability", "file": "app/main.py", "line": 0,
                "description": "Pagination is not implemented for GET /notes.",
                "recommendation": "Add limit/offset parameters if note counts grow.",
                "actionable": False,
            }],
            recommendation="accept",
        ).model_dump()

    def _single_agent(self, meta: dict, scenario: str) -> dict:
        return DeveloperOutput(
            summary="Complete Notes API generated in one pass.",
            files=[
                _f("app/database.py", s.DATABASE_PY),
                _f("app/models.py", s.MODELS_PY),
                _f("app/schemas.py", s.SCHEMAS_PY),
                _f("app/main.py", s.MAIN_SINGLE_AGENT),
                _f("tests/conftest.py", s.CONFTEST_T2),
                _f("tests/test_api.py", s.TEST_API_PY),
                _f("tests/test_search.py", s.TEST_SEARCH_PY),
                _f("README.md", s.README_MD),
            ],
            notes=[],
        ).model_dump()
