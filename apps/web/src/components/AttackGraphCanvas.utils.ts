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
    case "goal":
      return "目标";
    case "task":
      return "任务";
    case "surface":
      return "攻击面";
    case "observation":
      return "观测";
    case "hypothesis":
      return "假设";
    case "action":
      return "动作";
    case "vulnerability":
      return "漏洞";
    case "exploit":
      return "验证";
    case "pivot":
      return "横向路径";
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

function getObservationFinding(node: SessionGraphNode): string | null {
  return (
    readString(node.data.observation_summary) ??
    readString(node.data.finding) ??
    readString(node.data.response_excerpt) ??
    readString(node.data.summary) ??
    readString(node.data.observation) ??
    readString(node.data.result) ??
    readString(node.data.result_text) ??
    readString(node.data.evidence) ??
    readString(node.data.stdout)
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
      return fallback;
    case "task":
      return fallback;
    case "action":
    case "exploit":
      return truncateText(getCommandIntent(node), 52) ?? fallback;
    case "hypothesis":
      return truncateText(readString(node.data.summary) ?? node.label, 52) ?? fallback;
    case "outcome":
      return truncateText(getOutcomeConclusion(node), 52) ?? fallback;
    default:
      return fallback;
  }
}

export function displayExcerpt(node: SessionGraphNode): string | null {
  void node;
  return null;
}

export function displayImportance(node: SessionGraphNode): AttackNodeDisplayEmphasis {
  switch (getNodeType(node)) {
    case "goal":
      return "goal";
    case "task":
      return readBoolean(node.data.current) ? "critical" : "supporting";
    case "exploit":
    case "vulnerability":
    case "surface":
      return "critical";
    case "action":
      return readBoolean(node.data.current) || readString(node.data.status) === "in_progress"
        ? "critical"
        : "supporting";
    case "outcome":
      return "result";
    default:
      return "supporting";
  }
}

export function buildAttackNodeOverviewSummary(node: SessionGraphNode): string | null {
  switch (getNodeType(node)) {
    case "goal":
      return truncateText(readString(node.data.target) ?? readString(node.data.summary), 120);
    case "task":
    case "action":
    case "exploit":
      return truncateText(
        readString(node.data.observation_summary) ??
          readString(node.data.request_summary) ??
          readString(node.data.intent) ??
          readString(node.data.summary),
        120,
      );
    case "observation":
      return truncateText(getObservationFinding(node), 120);
    case "hypothesis":
      return truncateText(readString(node.data.summary), 120);
    case "outcome":
      return truncateText(getOutcomeConclusion(node), 120);
    default:
      return truncateText(readString(node.data.summary), 120);
  }
}

export function buildAttackNodeHighValueDetails(node: SessionGraphNode): AttackNodeDetailItem[] {
  const tool = readString(node.data.tool_name) ?? readString(node.data.tool);
  const command = readString(node.data.command);
  const intent = readString(node.data.intent);
  const summary = readString(node.data.summary);
  const result = readString(node.data.result_text) ?? readString(node.data.stdout);
  const evidence = readString(node.data.evidence);
  const items: AttackNodeDetailItem[] = [];

  switch (getNodeType(node)) {
    case "goal": {
      const target = readString(node.data.target) ?? summary;
      if (target) {
        items.push({ label: "目标", value: truncateText(target, 180) ?? target });
      }
      break;
    }
    case "action":
    case "task":
    case "exploit":
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
    case "observation": {
      const finding = readString(node.data.finding) ?? summary;
      if (finding) {
        items.push({ label: "发现", value: truncateText(finding, 180) ?? finding });
      }
      if (evidence && evidence !== finding) {
        items.push({ label: "证据", value: truncateText(evidence, 220) ?? evidence });
      }
      break;
    }
    case "hypothesis":
      if (summary) {
        items.push({ label: "假设", value: truncateText(summary, 180) ?? summary });
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
  const basic: AttackNodeDetailItem[] = [
    { label: "类型", value: formatAttackNodeType(node.node_type) },
    { label: "状态", value: formatAttackNodeStatus(readString(node.data.status)) },
  ];
  const taskId = readString(node.data.task_id);
  const taskName = readString(node.data.task_name);
  const traceId = readString(node.data.trace_id);
  const sequence = readNumber(node.data.sequence);
  const toolName = readString(node.data.tool_name) ?? readString(node.data.tool);
  const exitCode = safeJsonSummary(node.data.exit_code);

  if (taskId) {
    basic.push({ label: "Task ID", value: taskId });
  }
  if (taskName && taskName !== node.label) {
    basic.push({ label: "任务名", value: taskName });
  }
  if (traceId) {
    basic.push({ label: "Trace ID", value: traceId });
  }
  if (sequence !== null) {
    basic.push({ label: "顺序", value: String(sequence) });
  }
  if (toolName) {
    basic.push({ label: "工具", value: toolName });
  }
  if (exitCode) {
    basic.push({ label: "退出码", value: exitCode });
  }

  const why: AttackNodeDetailItem[] = [];
  const whySummary = readString(node.data.summary);
  const whyDescription = readString(node.data.description);
  const whyThought = readString(node.data.thought);
  const whyIntent = readString(node.data.intent);
  if (whySummary) {
    why.push({ label: "摘要", value: whySummary });
  }
  if (whyDescription && whyDescription !== whySummary) {
    why.push({ label: "说明", value: whyDescription });
  }
  if (whyIntent && whyIntent !== whySummary) {
    why.push({ label: "意图", value: whyIntent });
  }
  if (whyThought) {
    why.push({ label: "思路", value: whyThought });
  }

  const action: AttackNodeDetailItem[] = [];
  const command = readString(node.data.command);
  const requestSummary = readString(node.data.request_summary);
  const argumentsValue = buildRawTextItem("参数", node.data.arguments);
  if (toolName) {
    action.push({ label: "工具", value: toolName });
  }
  if (command) {
    action.push({ label: "命令", value: command });
  }
  if (requestSummary && requestSummary !== command) {
    action.push({ label: "请求摘要", value: requestSummary });
  }
  if (argumentsValue) {
    action.push(argumentsValue);
  }

  const observation: AttackNodeDetailItem[] = [];
  const observationSummary = readString(node.data.observation_summary);
  const observationValue = readString(node.data.observation);
  const result = safeJsonSummary(node.data.result);
  const responseExcerpt = readString(node.data.response_excerpt);
  const stdout = readString(node.data.stdout);
  const stderr = readString(node.data.stderr);
  if (observationSummary) {
    observation.push({ label: "观测摘要", value: observationSummary });
  }
  if (observationValue && observationValue !== observationSummary) {
    observation.push({ label: "观测", value: observationValue });
  }
  if (responseExcerpt && responseExcerpt !== observationSummary) {
    observation.push({ label: "响应摘录", value: responseExcerpt });
  }
  if (result) {
    observation.push({ label: "结果", value: truncateText(result, 400) ?? result });
  }
  if (stdout) {
    observation.push({ label: "stdout", value: stdout });
  }
  if (stderr) {
    observation.push({ label: "stderr", value: stderr });
  }

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

  const raw: AttackNodeDetailItem[] = [];
  const sourceMessageId = readString(node.data.source_message_id);
  const branchId = readString(node.data.branch_id);
  const generationId = readString(node.data.generation_id);
  const sourceGraphs = readStringArray(node.data.source_graphs);
  void sourceMessageId;
  void branchId;
  void generationId;
  if (sourceGraphs.length > 0) {
    raw.push({ label: "source_graphs", value: sourceGraphs.join(" / ") });
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
    (node) => readBoolean(node.data.current) || readBoolean(node.data.active),
  );

  if (activeNode) {
    return activeNode.id;
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
