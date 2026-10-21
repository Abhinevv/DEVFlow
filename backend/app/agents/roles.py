"""The Planner, Developer, Debugger and Reviewer agents (spec section 10)."""

from __future__ import annotations

import json

from app.agents.base import Agent
from app.agents.schemas import DebuggerOutput, DeveloperOutput, Plan, ReviewReport
from app.templates import Template
from app.tools.workspace import render_snapshot


def _template_block(template: Template) -> str:
    conventions = "\n".join(f"- {c}" for c in template.conventions)
    return (
        f"<template id=\"{template.id}\" version=\"{template.version}\">\n{template.description}\n"
        f"Approved dependencies (nothing else is installed): {', '.join(template.approved_dependencies)}\n"
        f"Conventions:\n{conventions}\n</template>"
    )


def _plan_block(plan: dict) -> str:
    keep = {k: plan[k] for k in ("summary", "functional_requirements", "non_functional_requirements", "assumptions",
                                 "constraints", "acceptance_criteria", "components", "directory_structure") if k in plan}
    return f"<approved_plan>\n{json.dumps(keep, indent=2)}\n</approved_plan>"


class PlannerAgent(Agent):
    role = "planner"
    output_model = Plan
    system_prompt = (
        "You are the Planner in DevFlow AI, a multi-agent software engineering system. You turn a natural-language "
        "software request into a specification and a dependency-ordered task plan that a Developer agent will implement "
        "one task at a time, with a Tester running pytest after each task.\n\n"
        "Work within the given project template and its approved dependencies. Extract functional and non-functional "
        "requirements, constraints and acceptance criteria. Where the request is ambiguous, choose a reasonable default "
        "and record it under assumptions; list a question under clarifications_needed only when the answer would "
        "materially change the design.\n\n"
        "Make tasks small and independently testable: each should leave the project in a state where the whole test "
        "suite passes, and should include the tests for what it builds. Use ids T1, T2, ... and declare dependencies "
        "explicitly; the graph must be acyclic. Keep the plan to between 2 and 8 tasks. expected_outputs and "
        "directory_structure use workspace-relative POSIX paths."
    )

    def plan(self, *, name: str, requirements: str, template: Template, constraints: dict, previous_plan: dict | None,
             feedback: str | None, meta: dict) -> Plan:
        parts = [
            _template_block(template),
            f"<project_name>{name}</project_name>",
            f"<requirements>\n{requirements}\n</requirements>",
        ]
        user_constraints = {k: v for k, v in constraints.items() if not k.startswith("mock_")}
        if user_constraints:
            parts.append(f"<constraints>\n{json.dumps(user_constraints, indent=2)}\n</constraints>")
        if previous_plan and feedback:
            parts.append(f"<previous_plan>\n{json.dumps(previous_plan, indent=2)}\n</previous_plan>")
            parts.append(f"<revision_feedback>\n{feedback}\n</revision_feedback>\nRevise the plan to address this feedback.")
        else:
            parts.append("Produce the specification and task plan.")
        return self.call("\n\n".join(parts), summary=f"Plan project '{name}'", meta={**meta, "feedback": feedback})


class DeveloperAgent(Agent):
    role = "developer"
    output_model = DeveloperOutput
    system_prompt = (
        "You are the Developer in DevFlow AI. You implement one planned task at a time inside a project workspace. "
        "Return complete file contents for every file you create or modify; do not return diffs or partial files. "
        "Change only what the task requires, keep interfaces consistent with existing files, and write pytest tests for "
        "the behaviour you add. After your change the entire existing test suite must still pass.\n\n"
        "Use only the template's approved dependencies and the standard library. Paths must be workspace-relative POSIX "
        "paths inside the project; anything else is rejected by the sandbox. Tests run without network access on a "
        "read-only copy of the project, so they must not write files except to temporary directories."
    )

    def implement(self, *, plan: dict, task: dict, template: Template, files: dict[str, str], completed: list[str],
                  feedback: list[str], meta: dict, task_id: int) -> DeveloperOutput:
        parts = [
            _template_block(template),
            _plan_block(plan),
            f"<completed_tasks>\n{chr(10).join(completed) or '(none)'}\n</completed_tasks>",
            f"<workspace>\n{render_snapshot(files)}\n</workspace>",
            f"<task>\n{json.dumps(task, indent=2)}\n</task>",
        ]
        if feedback:
            parts.append("<feedback_from_previous_attempt>\n" + "\n".join(f"- {f}" for f in feedback) + "\n</feedback_from_previous_attempt>")
        parts.append(f"Implement task {task['id']}.")
        return self.call("\n\n".join(parts), summary=f"Implement {task['id']}: {task['title']}", task_id=task_id, meta=meta)

    def revise(self, *, plan: dict, template: Template, files: dict[str, str], findings: list[dict], meta: dict) -> DeveloperOutput:
        parts = [
            _template_block(template),
            _plan_block(plan),
            f"<workspace>\n{render_snapshot(files)}\n</workspace>",
            f"<review_findings>\n{json.dumps(findings, indent=2)}\n</review_findings>",
            "Address these code-review findings with the smallest correct change, and add tests that demonstrate each fix.",
        ]
        return self.call("\n\n".join(parts), summary=f"Address {len(findings)} review finding(s)", meta={**meta, "mode": "revise"})


class DebuggerAgent(Agent):
    role = "debugger"
    output_model = DebuggerOutput
    system_prompt = (
        "You are the Debugger in DevFlow AI. You receive real test output from a sandboxed pytest run and the current "
        "project files. Identify the most likely root cause and return the minimal set of complete-file replacements "
        "that fixes it. Fix the implementation, not the test, unless the test clearly contradicts the task's acceptance "
        "criteria; say so in the explanation if you change a test. Do not weaken or delete assertions to make them pass. "
        "Your change will be revalidated independently by rerunning the failed tests and then the full suite. Use only "
        "approved dependencies and workspace-relative POSIX paths."
    )

    def diagnose(self, *, task: dict | None, template: Template, files: dict[str, str], report: dict, previous: list[dict],
                 meta: dict, task_id: int | None) -> DebuggerOutput:
        parts = [
            _template_block(template),
            f"<task>\n{json.dumps(task, indent=2) if task else '(review revision)'}\n</task>",
            f"<workspace>\n{render_snapshot(files)}\n</workspace>",
            f"<test_report>\n{json.dumps(report, indent=2)}\n</test_report>",
        ]
        if previous:
            parts.append("<previous_debug_attempts>\nThese did not fix the failure; try a different hypothesis.\n"
                         f"{json.dumps(previous, indent=2)}\n</previous_debug_attempts>")
        parts.append("Diagnose the failure and return the fix.")
        label = task["id"] if task else "revision"
        return self.call("\n\n".join(parts), summary=f"Debug failing tests for {label}", task_id=task_id, meta=meta)


class ReviewerAgent(Agent):
    role = "reviewer"
    output_model = ReviewReport
    system_prompt = (
        "You are the Reviewer in DevFlow AI. You assess a finished implementation against its approved requirements. "
        "Test results and static-analysis output given to you are ground truth from real execution; do not contradict "
        "them. Check requirement coverage, correctness, test adequacy, maintainability and common security issues "
        "(injection, missing input validation, secrets, unsafe deserialisation, authorisation gaps).\n\n"
        "Report concrete findings with file and line where possible. Severity: critical/high for defects or "
        "vulnerabilities that make the project unfit to deliver, medium for real but contained problems, low/info for "
        "polish. Mark a finding actionable only if a developer can fix it now without a product decision. Recommend "
        "'accept' when no critical/high/medium actionable findings remain, 'revise' when actionable fixes are needed, and "
        "'human_review' when a decision outside the code is required."
    )

    def review(self, *, plan: dict, template: Template, files: dict[str, str], test_summary: dict, analysis: list[dict],
               previous_findings: list[dict], meta: dict) -> ReviewReport:
        parts = [
            _template_block(template),
            _plan_block(plan),
            f"<workspace>\n{render_snapshot(files)}\n</workspace>",
            f"<test_results>\n{json.dumps(test_summary, indent=2)}\n</test_results>",
            f"<static_analysis>\n{json.dumps(analysis, indent=2) if analysis else '(no issues)'}\n</static_analysis>",
        ]
        if previous_findings:
            parts.append(f"<previous_review_findings>\nThe developer attempted to address these.\n{json.dumps(previous_findings, indent=2)}\n</previous_review_findings>")
        parts.append("Review the implementation.")
        return self.call("\n\n".join(parts), summary=f"Review implementation (round {meta.get('review_round', 0) + 1})", meta=meta)


class SingleAgent(Agent):
    """Baseline for comparative evaluation (spec 17.6): one call, no decomposition, debugging or review."""

    role = "single_agent"
    output_model = DeveloperOutput
    system_prompt = (
        "You are a software engineer. Implement the complete project described below in one response, including pytest "
        "tests. Return complete file contents. Use only the template's approved dependencies and workspace-relative "
        "POSIX paths."
    )

    def build(self, *, name: str, requirements: str, template: Template, files: dict[str, str], meta: dict) -> DeveloperOutput:
        prompt = "\n\n".join([
            _template_block(template),
            f"<project_name>{name}</project_name>",
            f"<requirements>\n{requirements}\n</requirements>",
            f"<workspace>\n{render_snapshot(files)}\n</workspace>",
            "Implement the whole project.",
        ])
        return self.call(prompt, summary=f"Single-agent build of '{name}'", meta=meta)
