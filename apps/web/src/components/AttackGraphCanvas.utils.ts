import type { AttackGraphNodeType, SessionGraph, SessionGraphNode } from "../types/graphs";

export type AttackNodeDisplayEmphasis = "goal" | "critical" | "result" | "supporting";

export type AttackNodeDetailItem = {
  label: string;
  value: string;
};

export type AttackNodeDetailSection = {
  title: string;
  items: AttackNodeDetailItem[];
};

export function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function readBoolean(value: unknown): boolean {
  return value === true;
}

export function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

export function readRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter(
    (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item),
  );
}

export function safeJsonSummary(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }

  if (typeof value === "string") {
    return value;
  }

  if (typeof value !== "object") {
    return String(value);
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return null;
  }
}

export function truncateText(value: string | null, maxLength: number): string | null {
  if (!value) {
    return null;
  }

  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, maxLength - 1).trimEnd()}…`;
}

export function formatAttackNodeType(nodeType: string): string {
  switch (nodeType) {
    case "root":
      return "目标";
    case "goal":
      return "目标";
    case "task":
      return "任务";
    case "action":
      return "执行";
    case "outcome":
      return "结果";
    default:
      return nodeType;
  }
}

export function formatAttackNodeStatus(status: string | null): string {
  switch (status) {
    case "completed":
    case "done":
      return "已完成";
    case "in_progress":
    case "running":
      return "进行中";
    case "blocked":
      return "已阻塞";
    case "failed":
    case "error":
      return "异常";
    case "ready":
      return "就绪";
    case "pending":
      return "待执行";
    default:
      return status ?? "未标记";
  }
}

export function getAttackNodeStatusTone(status: string | null): string {
  switch (status) {
    case "completed":
    case "done":
      return "tone-success";
    case "in_progress":
    case "running":
      return "tone-connected";
    case "blocked":
    case "needs_approval":
      return "tone-warning";
    case "failed":
    case "error":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

export function getAttackRelationLabel(relation: string): string {
  switch (relation) {
    case "depends_on":
      return "依赖";
    case "precedes":
      return "前置";
    case "supports":
      return "支持";
    case "contradicts":
      return "矛盾";
    case "validates":
      return "验证";
    case "causes":
      return "导致";
    case "attempts":
      return "尝试";
    case "enables":
      return "使能";
    case "branches_from":
      return "分支自";
    case "blocks":
      return "阻断";
    case "discovers":
      return "发现";
    case "confirms":
      return "确认";
    default:
      return relation;
  }
}

function getNodeType(node: SessionGraphNode): AttackGraphNodeType {
  return node.node_type as AttackGraphNodeType;
}

function getCommandIntent(node: SessionGraphNode): string | null {
  return (
    readString(node.data.command) ??
    readString(node.data.request_summary) ??
    readString(node.data.intent) ??
    readString(node.data.tool_name) ??
    readString(node.data.tool)
  );
}

function getOutcomeConclusion(node: SessionGraphNode): string | null {
  return (
    readString(node.data.conclusion) ??
    readString(node.data.summary) ??
    readString(node.data.result_text) ??
    readString(node.data.stdout)
  );
}

export function displayTitle(node: SessionGraphNode): string {
  const fallback = truncateText(node.label, 52) ?? node.label;

  switch (getNodeType(node)) {
    case "goal":
    case "root":
      return truncateText(readString(node.data.goal) ?? readString(node.data.title) ?? fallback, 52) ?? fallback;
    case "task":
      return (
        truncateText(readString(node.data.title) ?? readString(node.data.task_name) ?? fallback, 52) ?? fallback
      );
    case "action":
      return truncateText(getCommandIntent(node), 52) ?? fallback;
    case "outcome":
      return truncateText(readString(node.data.title) ?? getOutcomeConclusion(node) ?? fallback, 52) ?? fallback;
    default:
      return fallback;
  }
}

export function displayExcerpt(node: SessionGraphNode): string | null {
  const excerpt =
    readString(node.data.observation_summary) ??
    readString(node.data.response_excerpt) ??
    truncateText(readString(node.data.stdout), 72);

  if (!excerpt) {
    return null;
  }

  const title = displayTitle(node);
  return excerpt === title ? null : truncateText(excerpt, 72);
}

export function displayImportance(node: SessionGraphNode): AttackNodeDisplayEmphasis {
  const status = readString(node.data.status);
  if (node.node_type === "root" || node.node_type === "goal") {
    return "goal";
  }
  if (node.node_type === "outcome") {
    return "result";
  }
  if (
    node.node_type === "action" &&
    (readBoolean(node.data.current) ||
      readBoolean(node.data.active) ||
      status === "in_progress" ||
      status === "blocked" ||
      status === "failed")
  ) {
    return "critical";
  }
  return "supporting";
}

export function buildAttackNodeOverviewSummary(node: SessionGraphNode): string | null {
  if (node.node_type === "root" || node.node_type === "goal") {
    return truncateText(readString(node.data.goal) ?? readString(node.data.summary), 120);
  }
  if (node.node_type === "task") {
    return truncateText(
      readString(node.data.summary) ?? readString(node.data.description) ?? readString(node.data.thought),
      120,
    );
  }
  if (node.node_type === "action") {
    return truncateText(
      readString(node.data.observation_summary) ??
        readString(node.data.response_excerpt) ??
        readString(node.data.summary) ??
        readString(node.data.stdout),
      120,
    );
  }
  if (node.node_type === "outcome") {
    return truncateText(getOutcomeConclusion(node), 120);
  }
  return truncateText(readString(node.data.summary), 120);
}

export function buildAttackNodeHighValueDetails(node: SessionGraphNode): AttackNodeDetailItem[] {
  const tool = readString(node.data.tool_name) ?? readString(node.data.tool);
  const command = readString(node.data.command);
  const intent = readString(node.data.intent);
  const summary = readString(node.data.summary);
  const result = readString(node.data.result_text) ?? readString(node.data.stdout);
  const items: AttackNodeDetailItem[] = [];

  switch (node.node_type) {
    case "root":
    case "goal": {
      const target = readString(node.data.goal) ?? readString(node.data.target) ?? summary;
      if (target) {
        items.push({ label: "目标", value: truncateText(target, 180) ?? target });
      }
      break;
    }
    case "action":
    case "task":
      if (tool) {
        items.push({ label: "工具", value: tool });
      }
      if (command) {
        items.push({ label: "命令", value: truncateText(command, 180) ?? command });
      }
      if (intent && intent !== command) {
        items.push({ label: "意图", value: truncateText(intent, 180) ?? intent });
      }
      if (summary && summary !== intent) {
        items.push({ label: "结果", value: truncateText(summary, 180) ?? summary });
      }
      break;
    case "outcome": {
      const conclusion = readString(node.data.conclusion) ?? summary;
      if (conclusion) {
        items.push({ label: "结论", value: truncateText(conclusion, 180) ?? conclusion });
      }
      if (result && result !== conclusion) {
        items.push({ label: "结果", value: truncateText(result, 220) ?? result });
      }
      break;
    }
    default:
      if (summary) {
        items.push({ label: "摘要", value: truncateText(summary, 180) ?? summary });
      }
      break;
  }

  return items;
}

function buildRawTextItem(label: string, value: unknown): AttackNodeDetailItem | null {
  const text = safeJsonSummary(value);
  if (!text) {
    return null;
  }

  return {
    label,
    value: truncateText(text, 400) ?? text,
  };
}

function buildFindingValue(finding: Record<string, unknown>): string | null {
  const title = readString(finding.title) ?? readString(finding.label) ?? readString(finding.kind);
  const summary =
    readString(finding.summary) ??
    readString(finding.supports) ??
    readString(finding.validates) ??
    readString(finding.causes) ??
    readString(finding.contradicts);
  const confidence = readString(finding.confidence);
  const detail = [summary, confidence ? `置信度 ${confidence}` : null].filter(Boolean).join(" · ");

  if (!title && !detail) {
    return null;
  }

  return [title, detail].filter(Boolean).join(" — ");
}

function buildHypothesisValue(hypothesis: Record<string, unknown>): string | null {
  const summary = readString(hypothesis.summary) ?? readString(hypothesis.result) ?? readString(hypothesis.kind);
  const status = readString(hypothesis.status);
  if (!summary && !status) {
    return null;
  }

  return [summary, status ? `状态 ${status}` : null].filter(Boolean).join(" · ");
}

export function buildAttackNodeDetailSections(node: SessionGraphNode): AttackNodeDetailSection[] {
  const nodeId = readString(node.data.action_id) ?? readString(node.data.task_id) ?? node.id;
  const runId = readString(node.data.run_id);
  const sessionId = readString(node.data.session_id);
  const currentStage = readString(node.data.current_stage) ?? readString(node.data.stage_key);
  const updatedAt = readString(node.data.updated_at);
  const completedAt = readString(node.data.completed_at) ?? readString(node.data.ended_at);
  const basic: AttackNodeDetailItem[] = [
    { label: "类型", value: formatAttackNodeType(node.node_type) },
    { label: "ID", value: nodeId },
    { label: "状态", value: formatAttackNodeStatus(readString(node.data.status)) },
  ];
  const taskId = readString(node.data.task_id);
  const taskName = readString(node.data.task_name);
  const traceId = readString(node.data.trace_id);
  const sequence = readNumber(node.data.sequence);
  const toolName = readString(node.data.tool_name) ?? readString(node.data.tool);
  const exitCode = safeJsonSummary(node.data.exit_code);

  if ((node.node_type === "root" || node.node_type === "outcome") && runId) {
    basic.push({ label: "Run ID", value: runId });
  }
  if ((node.node_type === "root" || node.node_type === "outcome") && sessionId) {
    basic.push({ label: "Session ID", value: sessionId });
  }
  if (currentStage) {
    basic.push({ label: "阶段", value: currentStage });
  }

  if (taskId && node.node_type !== "root" && node.node_type !== "outcome") {
    basic.push({ label: "Task ID", value: taskId });
  }
  if (taskName && taskName !== node.label && node.node_type !== "root" && node.node_type !== "outcome") {
    basic.push({ label: "任务名", value: taskName });
  }
  if (traceId && node.node_type === "action") {
    basic.push({ label: "Trace ID", value: traceId });
  }
  if (sequence !== null && node.node_type !== "root") {
    basic.push({ label: "顺序", value: String(sequence) });
  }
  if (toolName && node.node_type === "action") {
    basic.push({ label: "工具", value: toolName });
  }
  if (exitCode && node.node_type === "action") {
    basic.push({ label: "退出码", value: exitCode });
  }
  if (updatedAt) {
    basic.push({ label: "更新时间", value: updatedAt });
  }
  if (completedAt) {
    basic.push({ label: "完成时间", value: completedAt });
  }

  const why: AttackNodeDetailItem[] = [];
  const whySummary = readString(node.data.summary);
  const whyDescription = readString(node.data.description);
  const whyThought = readString(node.data.thought);
  const whyIntent = readString(node.data.intent);
  if (whySummary && node.node_type !== "outcome") {
    why.push({ label: "摘要", value: whySummary });
  }
  if (whyDescription && whyDescription !== whySummary && node.node_type !== "root" && node.node_type !== "outcome") {
    why.push({ label: "说明", value: whyDescription });
  }
  if (whyIntent && whyIntent !== whySummary && node.node_type === "action") {
    why.push({ label: "意图", value: whyIntent });
  }
  if (whyThought && node.node_type !== "root") {
    why.push({ label: "思路", value: whyThought });
  }

  const action: AttackNodeDetailItem[] = [];
  const command = readString(node.data.command);
  const requestSummary = readString(node.data.request_summary);
  const argumentsValue = buildRawTextItem("参数", node.data.arguments);
  if (toolName && node.node_type === "action") {
    action.push({ label: "工具", value: toolName });
  }
  if (command && node.node_type === "action") {
    action.push({ label: "命令", value: command });
  }
  if (requestSummary && requestSummary !== command && node.node_type === "action") {
    action.push({ label: "请求摘要", value: requestSummary });
  }
  if (argumentsValue && node.node_type === "action") {
    action.push(argumentsValue);
  }

  const observation: AttackNodeDetailItem[] = [];
  const observationSummary = readString(node.data.observation_summary);
  const observationValue = readString(node.data.observation);
  const result = safeJsonSummary(node.data.result);
  const responseExcerpt = readString(node.data.response_excerpt);
  const stdout = readString(node.data.stdout);
  const stderr = readString(node.data.stderr);
  const sourceGraphs = readStringArray(node.data.source_graphs);
  if (observationSummary && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "观测摘要", value: observationSummary });
  }
  if (observationValue && observationValue !== observationSummary && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "观测", value: observationValue });
  }
  if (responseExcerpt && responseExcerpt !== observationSummary && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "响应摘录", value: responseExcerpt });
  }
  if (result && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "结果", value: truncateText(result, 400) ?? result });
  }
  if (stdout && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "stdout", value: stdout });
  }
  if (stderr && node.node_type !== "root" && node.node_type !== "task") {
    observation.push({ label: "stderr", value: stderr });
  }

  const blockedReason = readString(node.data.blocked_reason);

  const interpretation: AttackNodeDetailItem[] = [];
  for (const [index, finding] of readRecordArray(node.data.related_findings).entries()) {
    const value = buildFindingValue(finding);
    if (value) {
      interpretation.push({ label: `发现 ${index + 1}`, value });
    }
  }
  for (const [index, hypothesis] of readRecordArray(node.data.related_hypotheses).entries()) {
    const value = buildHypothesisValue(hypothesis);
    if (value) {
      interpretation.push({ label: `假设 ${index + 1}`, value });
    }
  }
  if (blockedReason) {
    interpretation.push({ label: "阻塞原因", value: blockedReason });
  }
  if (sourceGraphs.length > 0) {
    interpretation.push({ label: "来源图层", value: sourceGraphs.join(" / ") });
  }

  const raw: AttackNodeDetailItem[] = [];
  const sourceMessageId = readString(node.data.source_message_id);
  const branchId = readString(node.data.branch_id);
  const generationId = readString(node.data.generation_id);
  void sourceMessageId;
  void branchId;
  void generationId;
  const payload = buildRawTextItem("payload", node.data);
  if (payload) {
    raw.push(payload);
  }
  const provenance = buildRawTextItem("provenance", node.data.provenance);
  if (provenance) {
    raw.push(provenance);
  }
  const relationContext = buildRawTextItem("relation_context", node.data.relation_context);
  if (relationContext) {
    raw.push(relationContext);
  }

  return [
    { title: "Basic", items: basic },
    { title: "Why", items: why },
    { title: "Action", items: action },
    { title: "Observation", items: observation },
    { title: "Interpretation", items: interpretation },
    { title: "Raw", items: raw },
  ];
}

export function getAttackGraphAutoFocusNodeId(
  graph: SessionGraph,
  latestNodeId: string | null,
): string | null {
  const activeNode = graph.nodes.find(
    (node) =>
      node.node_type === "action" && (readBoolean(node.data.current) || readBoolean(node.data.active)),
  );

  if (activeNode) {
    return activeNode.id;
  }

  const latestActionNode = [...graph.nodes]
    .filter((node) => node.node_type === "action")
    .sort((left, right) => {
      const leftTimestamp =
        new Date(
          readString(left.data.updated_at) ??
            readString(left.data.completed_at) ??
            readString(left.data.ended_at) ??
            "1970-01-01T00:00:00.000Z",
        ).getTime() || 0;
      const rightTimestamp =
        new Date(
          readString(right.data.updated_at) ??
            readString(right.data.completed_at) ??
            readString(right.data.ended_at) ??
            "1970-01-01T00:00:00.000Z",
        ).getTime() || 0;

      return rightTimestamp - leftTimestamp;
    })[0];

  if (latestActionNode) {
    return latestActionNode.id;
  }

  const currentTaskNode = graph.nodes.find(
    (node) => node.node_type === "task" && (readBoolean(node.data.current) || readBoolean(node.data.active)),
  );

  if (currentTaskNode) {
    return currentTaskNode.id;
  }

  const latestExecutionNode = [...graph.nodes]
    .filter((node) => node.node_type === "task" || node.node_type === "action")
    .sort((left, right) => {
      const leftTimestamp =
        new Date(
          readString(left.data.updated_at) ??
            readString(left.data.completed_at) ??
            readString(left.data.ended_at) ??
            "1970-01-01T00:00:00.000Z",
        ).getTime() || 0;
      const rightTimestamp =
        new Date(
          readString(right.data.updated_at) ??
            readString(right.data.completed_at) ??
            readString(right.data.ended_at) ??
            "1970-01-01T00:00:00.000Z",
        ).getTime() || 0;

      return rightTimestamp - leftTimestamp;
    })[0];

  if (latestExecutionNode) {
    return latestExecutionNode.id;
  }

  if (latestNodeId && graph.nodes.some((node) => node.id === latestNodeId)) {
    return latestNodeId;
  }

  const outcomeNode = graph.nodes.find((node) => node.node_type === "outcome");
  if (outcomeNode) {
    return outcomeNode.id;
  }

  const rootNode = graph.nodes.find((node) => node.node_type === "root");
  if (rootNode) {
    return rootNode.id;
  }

  return graph.nodes[0]?.id ?? null;
}

export function buildAttackGraphAutoFocusSignature(
  graph: SessionGraph,
  focusNodeId: string | null,
): string {
  const nodeSignature = graph.nodes
    .map((node) => {
      const status = readString(node.data.status) ?? "";
      const current = readBoolean(node.data.current) || readBoolean(node.data.active) ? "1" : "0";
      const updatedAt = readString(node.data.updated_at) ?? readString(node.data.ended_at) ?? "";

      return `${node.id}:${status}:${current}:${updatedAt}`;
    })
    .join("|");
  const edgeSignature = graph.edges.map((edge) => edge.id).join("|");

  return `${graph.workflow_run_id}:${focusNodeId ?? "none"}:${nodeSignature}:${edgeSignature}`;
}

export function shouldAutoFocusAttackGraph(options: {
  hasUserInteracted: boolean;
  nextSignature: string;
  previousSignature: string | null;
}): boolean {
  const { hasUserInteracted, nextSignature, previousSignature } = options;

  if (hasUserInteracted || nextSignature.length === 0) {
    return false;
  }

  return nextSignature !== previousSignature;
}
