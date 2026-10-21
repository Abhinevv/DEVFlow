export type ProjectStatus =
  | "draft" | "planning" | "awaiting_approval" | "plan_rejected" | "queued" | "running"
  | "paused" | "blocked" | "completed" | "partial" | "failed" | "cancelled";

export interface User { id: number; email: string; display_name: string; role: string }

export interface PlanTask {
  id: string; title: string; description: string; depends_on: string[];
  expected_outputs: string[]; acceptance_criteria: string[];
}

export interface Plan {
  summary: string;
  functional_requirements: string[];
  non_functional_requirements: string[];
  assumptions: string[];
  constraints: string[];
  clarifications_needed: string[];
  acceptance_criteria: string[];
  components: { name: string; responsibility: string; interfaces: string[] }[];
  tech_stack: string[];
  directory_structure: string[];
  tasks: PlanTask[];
}

export interface Metrics {
  status: string; completed: boolean; tasks_total: number; tasks_completed: number;
  test_pass_rate: number | null; final_tests: { passed: number; failed: number; errors: number; total: number } | null;
  test_runs: number; retry_count: number; recovery_eligible: number; recovered: number; recovery_rate: number | null;
  duration_seconds: number; cost_usd: number; input_tokens: number; output_tokens: number; model_calls: number;
  human_interventions: number; safety_violations: number;
}

export interface Project {
  id: number; name: string; template: string; mode: "multi_agent" | "single_agent"; status: ProjectStatus;
  status_reason: string | null; control: string | null; plan_version: number; review_round: number;
  created_at: string; updated_at: string; started_at: string | null; finished_at: string | null; active_seconds: number;
}

export interface ProjectDetail extends Project {
  requirements: string; constraints: Record<string, unknown>; plan: Plan | null;
  config_snapshot: Record<string, unknown>; has_package: boolean; running: boolean; metrics: Metrics;
}

export interface Task {
  id: number; key: string; title: string; description: string; depends_on: string[]; expected_outputs: string[];
  acceptance_criteria: string[]; status: "pending" | "running" | "completed" | "failed" | "blocked";
  attempts: number; debug_attempts: number; evidence: Record<string, unknown>;
  started_at: string | null; finished_at: string | null;
}

export interface AgentRun {
  id: number; task_id: number | null; role: string; status: string; input_summary: string;
  output: Record<string, unknown> | null; error: string | null; model: string | null;
  input_tokens: number; output_tokens: number; cost_usd: number; started_at: string; duration_ms: number;
}

export interface TestFailure { nodeid: string; kind: string; message: string; details: string }

export interface TestRun {
  id: number; task_id: number | null; kind: string; command: string; exit_code: number; passed: number; failed: number;
  errors: number; skipped: number; total: number; success: boolean; duration_ms: number; summary: string;
  failures: TestFailure[]; checks: { static_issues?: Issue[]; security_issues?: Issue[] }; log: string;
  executor: string; isolated: boolean; created_at: string;
}

export interface Issue { source: string; severity: string; code: string; file: string; line: number; message: string }

export interface Finding {
  id: number; round: number; source: string; severity: string; category: string; file: string; line: number;
  description: string; recommendation: string; actionable: boolean; status: string;
}

export interface WorkflowEvent { id: number; type: string; agent: string | null; message: string; data: Record<string, unknown>; created_at: string }

export interface Approval { id: number; kind: string; decision: string; comment: string | null; plan_version: number | null; created_at: string }

export interface Artifact {
  id: number; task_id: number | null; kind: string; path: string; action: string; sha256: string | null;
  size: number; commit: string | null; note: string | null; created_at: string;
}

export interface Commit { sha: string; author: string; date: string; message: string; stats: string }

export interface Template {
  id: string; name: string; version: string; description: string; language: string;
  approved_dependencies: string[]; conventions: string[];
}

export interface SystemInfo {
  version: string; llm_provider: string; llm_model: string; llm_effort: string; executor: string;
  isolated_execution: boolean; limits: { max_debug_attempts: number; max_review_rounds: number; max_cost_usd: number; max_workflow_seconds: number };
}

export interface AggregateMetrics {
  projects: number; finished: number; task_completion_rate: number | null; mean_test_pass_rate: number | null;
  recovery_rate: number | null; mean_duration_seconds: number | null; mean_retry_count: number | null;
  total_cost_usd: number; cost_per_successful_task: number | null; human_intervention_rate: number | null;
  safety_violations: number; by_status: Record<string, number>;
}

export interface DeliveryReport {
  project: { id: number; name: string; status: string; status_reason: string | null; mode: string; template: string };
  config: Record<string, unknown>;
  summary: string;
  acceptance_criteria: string[];
  assumptions: string[];
  tasks: { id: string; title: string; status: string; debug_attempts: number }[];
  final_tests: (Omit<TestRun, "id" | "task_id" | "kind" | "command" | "exit_code" | "total" | "duration_ms" | "log" | "created_at">) | null;
  open_findings: Omit<Finding, "id" | "actionable" | "status">[];
  resolved_findings: Omit<Finding, "id" | "actionable" | "status">[];
  limitations: string[];
  markdown: string;
  package: string | null;
}
