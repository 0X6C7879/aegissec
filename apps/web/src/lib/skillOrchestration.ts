import type {
  SkillOrchestrationExecution,
  SkillOrchestrationPlan,
  SkillOrchestrationRole,
  SkillOrchestrationSkill,
  SkillOrchestrationStep,
  SkillStageTransition,
} from "../types/skills";

export type SkillNodeAttribution = {
  skillLabels: string[];
  nodeLabels: string[];
};

export type SkillOrchestrationSnapshot = {
  selectedSkills: SkillOrchestrationSkill[];
  preparedSelectedSkills: SkillOrchestrationSkill[];
  plan: SkillOrchestrationPlan | null;
  execution: SkillOrchestrationExecution | null;
  stageTransition: SkillStageTransition | null;
  replannedContext: Record<string, unknown> | null;
  workerResults: SkillOrchestrationStep[];
  nodeResults: SkillOrchestrationStep[];
};

const ORCHESTRATION_KEYS = [
  "selected_skills",
  "prepared_selected_skills",
  "skill_orchestration_plan",
  "skill_orchestration_execution",
  "skill_stage_transition",
  "replanned_skill_context",
] as const;

const ORCHESTRATION_RECORD_PATHS = [
  [],
  ["result"],
  ["payload"],
  ["data"],
  ["result", "payload"],
  ["result", "data"],
] as const;

const ATTRIBUTION_RECORD_PATHS = [
  [],
  ["arguments"],
  ["result"],
  ["result", "arguments"],
  ["execution"],
  ["result", "execution"],
  ["payload"],
  ["result", "payload"],
  ["data"],
  ["result", "data"],
  ["source"],
  ["result", "source"],
  ["attribution"],
  ["result", "attribution"],
  ["metadata"],
  ["result", "metadata"],
  ["skill"],
  ["result", "skill"],
  ["node"],
  ["result", "node"],
  ["node_result"],
  ["result", "node_result"],
  ["worker_result"],
  ["result", "worker_result"],
  ["current_node"],
  ["result", "current_node"],
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function hasOwnKey(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function readString(value: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }

  return null;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function readArrayRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter(isRecord);
}

function readPathRecord(
  value: Record<string, unknown>,
  path: readonly string[],
): Record<string, unknown> | null {
  if (path.length === 0) {
    return value;
  }

  let current: unknown = value;
  for (const key of path) {
    if (!isRecord(current) || !hasOwnKey(current, key)) {
      return null;
    }
    current = current[key];
  }

  return readRecord(current);
}

function readSkillEntryName(entry: Record<string, unknown>): string {
  return (
    readString(entry, ["directory_name", "name", "title", "id", "skill_id"]) ?? "unknown"
  );
}

function hasOrchestrationSignal(record: Record<string, unknown>): boolean {
  if (ORCHESTRATION_KEYS.some((key) => hasOwnKey(record, key))) {
    return true;
  }

  const nestedExecution = readRecord(record["skill_orchestration_execution"]);
  if (nestedExecution) {
    if (hasOwnKey(nestedExecution, "worker_results") || hasOwnKey(nestedExecution, "node_results")) {
      return true;
    }
  }

  return false;
}

function normalizeSkillEntries(entries: Record<string, unknown>[]): SkillOrchestrationSkill[] {
  return entries.map((entry) => ({ ...entry }));
}

function normalizeStepEntries(entries: Record<string, unknown>[]): SkillOrchestrationStep[] {
  return entries.map((entry) => ({ ...entry }));
}

function readOrchestrationExecution(
  container: Record<string, unknown>,
): SkillOrchestrationExecution | null {
  const explicitExecution = readRecord(container["skill_orchestration_execution"]);
  if (explicitExecution) {
    return {
      ...explicitExecution,
      worker_results: normalizeStepEntries(readArrayRecords(explicitExecution["worker_results"])),
      node_results: normalizeStepEntries(readArrayRecords(explicitExecution["node_results"])),
    };
  }

  return null;
}

function readStageTransition(
  container: Record<string, unknown>,
  execution: SkillOrchestrationExecution | null,
): SkillStageTransition | null {
  const directTransition = readRecord(container["skill_stage_transition"]);
  if (directTransition) {
    return { ...directTransition };
  }

  const executionTransition = execution ? readRecord(execution.stage_transition) : null;
  return executionTransition ? { ...executionTransition } : null;
}

function readReplannedContext(container: Record<string, unknown>): Record<string, unknown> | null {
  const replanned = readRecord(container["replanned_skill_context"]);
  return replanned ? { ...replanned } : null;
}

function findOrchestrationContainer(value: unknown): Record<string, unknown> | null {
  const root = readRecord(value);
  if (!root) {
    return null;
  }

  for (const path of ORCHESTRATION_RECORD_PATHS) {
    const candidate = readPathRecord(root, path);
    if (candidate && hasOrchestrationSignal(candidate)) {
      return candidate;
    }
  }

  return null;
}

export function extractSkillOrchestrationSnapshot(value: unknown): SkillOrchestrationSnapshot | null {
  const container = findOrchestrationContainer(value);
  if (!container) {
    return null;
  }

  const selectedSkills = normalizeSkillEntries(readArrayRecords(container["selected_skills"]));
  const preparedSelectedSkills = normalizeSkillEntries(
    readArrayRecords(container["prepared_selected_skills"]),
  );
  const plan = readRecord(container["skill_orchestration_plan"]);
  const execution = readOrchestrationExecution(container);
  const workerResults = normalizeStepEntries(
    readArrayRecords(container["worker_results"]),
  );
  const nodeResults = normalizeStepEntries(readArrayRecords(container["node_results"]));

  const resolvedWorkerResults =
    workerResults.length > 0 ? workerResults : (execution?.worker_results ?? []);
  const resolvedNodeResults =
    nodeResults.length > 0 ? nodeResults : (execution?.node_results ?? []);

  return {
    selectedSkills,
    preparedSelectedSkills,
    plan: plan ? ({ ...plan } as SkillOrchestrationPlan) : null,
    execution,
    stageTransition: readStageTransition(container, execution),
    replannedContext: readReplannedContext(container),
    workerResults: resolvedWorkerResults,
    nodeResults: resolvedNodeResults,
  };
}

export function extractSkillOrchestrationSnapshotFromMetadata(
  metadata: Record<string, unknown> | undefined,
): SkillOrchestrationSnapshot | null {
  return extractSkillOrchestrationSnapshot(metadata);
}

export function formatOrchestrationRole(role: string | null | undefined): string {
  const normalized = (role ?? "").trim().toLowerCase() as SkillOrchestrationRole;
  switch (normalized) {
    case "primary":
      return "Primary";
    case "supporting":
      return "Supporting";
    case "reference":
      return "Reference";
    case "worker":
      return "Worker";
    case "reducer":
      return "Reducer";
    case "verifier":
      return "Verifier";
    default:
      return role && role.trim().length > 0 ? role : "Unknown";
  }
}

export function readSkillDisplayName(skill: Record<string, unknown> | SkillOrchestrationSkill): string {
  const entry = skill as Record<string, unknown>;
  return readSkillEntryName(entry);
}

function readSkillNameFromUnknown(value: unknown): string | null {
  if (typeof value === "string" && value.trim().length > 0) {
    return value.trim();
  }

  if (isRecord(value)) {
    return readString(value, ["directory_name", "name", "title", "id", "skill_id"]);
  }

  return null;
}

function readSkillNamesFromUnknown(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => readSkillNameFromUnknown(item))
      .filter((item): item is string => item !== null);
  }

  const single = readSkillNameFromUnknown(value);
  return single ? [single] : [];
}

function readNodeLabel(record: Record<string, unknown>): string | null {
  const role = readString(record, ["role"]);
  const nodeKind = readString(record, ["node_kind", "node_type"]);
  const stepId = readString(record, ["step_id", "node_id"]);
  const stageName = readString(record, ["stage_name"]);
  const nodeName = readString(record, ["node_name", "node_label", "name"]);

  if (!role && !nodeKind && !stepId && !stageName && !nodeName) {
    return null;
  }

  const parts: string[] = [];
  if (role) {
    parts.push(formatOrchestrationRole(role));
  }
  if (nodeKind) {
    parts.push(nodeKind);
  }
  if (stepId) {
    parts.push(stepId);
  }
  if (stageName) {
    parts.push(`stage:${stageName}`);
  }
  if (nodeName && !parts.includes(nodeName)) {
    parts.push(nodeName);
  }
  return parts.join(" · ");
}

function readNodeLabelFromUnknown(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => readNodeLabelFromUnknown(item));
  }

  if (typeof value === "string" && value.trim().length > 0) {
    return [value.trim()];
  }

  if (isRecord(value)) {
    const label = readNodeLabel(value);
    if (label) {
      return [label];
    }

    const fallback = readString(value, ["node", "node_name", "node_label", "node_id"]);
    return fallback ? [fallback] : [];
  }

  return [];
}

function collectAttributionRecords(
  metadata: Record<string, unknown> | undefined,
): Record<string, unknown>[] {
  if (!metadata) {
    return [];
  }

  const records: Record<string, unknown>[] = [];
  const seen = new Set<Record<string, unknown>>();

  for (const path of ATTRIBUTION_RECORD_PATHS) {
    const candidate = readPathRecord(metadata, path);
    if (!candidate || seen.has(candidate)) {
      continue;
    }

    seen.add(candidate);
    records.push(candidate);
  }

  return records;
}

export function readSkillNodeAttributionFromMetadata(
  metadata: Record<string, unknown> | undefined,
): SkillNodeAttribution {
  const skillLabels: string[] = [];
  const nodeLabels: string[] = [];
  const seenSkillLabels = new Set<string>();
  const seenNodeLabels = new Set<string>();

  const pushSkill = (value: string | null) => {
    if (!value || seenSkillLabels.has(value)) {
      return;
    }
    seenSkillLabels.add(value);
    skillLabels.push(value);
  };

  const pushNode = (value: string | null) => {
    if (!value || seenNodeLabels.has(value)) {
      return;
    }
    seenNodeLabels.add(value);
    nodeLabels.push(value);
  };

  for (const record of collectAttributionRecords(metadata)) {
    for (const label of [
      ...readSkillNamesFromUnknown(record["skill"]),
      ...readSkillNamesFromUnknown(record["source_skill"]),
      ...readSkillNamesFromUnknown(record["sourceSkill"]),
      ...readSkillNamesFromUnknown(record["source_skill_id"]),
      ...readSkillNamesFromUnknown(record["source_skill_name"]),
      ...readSkillNamesFromUnknown(record["skill_name_or_id"]),
      ...readSkillNamesFromUnknown(record["skill_id"]),
      ...readSkillNamesFromUnknown(record["skill_name"]),
      ...readSkillNamesFromUnknown(record["directory_name"]),
      ...readSkillNamesFromUnknown(record["selected_skill_ids"]),
      ...readSkillNamesFromUnknown(record["source_skills"]),
      ...readSkillNamesFromUnknown(record["skills"]),
    ]) {
      pushSkill(label);
    }

    for (const label of [
      ...readNodeLabelFromUnknown(record["node"]),
      ...readNodeLabelFromUnknown(record["source_node"]),
      ...readNodeLabelFromUnknown(record["sourceNode"]),
      ...readNodeLabelFromUnknown(record["source_nodes"]),
      ...readNodeLabelFromUnknown(record["nodes"]),
      ...readNodeLabelFromUnknown(record["current_node"]),
      ...readNodeLabelFromUnknown(record["node_result"]),
      ...readNodeLabelFromUnknown(record["worker_result"]),
    ]) {
      pushNode(label);
    }

    const selectedSkillEntries = [
      ...readArrayRecords(record["selected_skills"]),
      ...readArrayRecords(record["prepared_selected_skills"]),
      ...readArrayRecords(record["worker_results"]),
      ...readArrayRecords(record["node_results"]),
    ];

    for (const entry of selectedSkillEntries) {
      pushSkill(readSkillEntryName(entry));
      pushNode(readNodeLabel(entry));
    }

    pushNode(readNodeLabel(record));
  }

  return { skillLabels, nodeLabels };
}
