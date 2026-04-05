import type { AttackGraphNodeType, SessionGraph, SessionGraphNode } from "../types/graphs";

export type AttackNodeDisplayEmphasis = "goal" | "critical" | "result" | "supporting";

export type AttackNodeDetailItem = {
  label: string;
  value: string;
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
    readString(node.data.intent) ??
    readString(node.data.tool_name) ??
    readString(node.data.tool)
  );
}

function getObservationFinding(node: SessionGraphNode): string | null {
  return (
    readString(node.data.finding) ??
    readString(node.data.summary) ??
    readString(node.data.observation) ??
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
    case "action":
    case "exploit":
      return truncateText(getCommandIntent(node), 52) ?? fallback;
    case "observation":
      return truncateText(getObservationFinding(node), 52) ?? fallback;
    case "hypothesis":
      return truncateText(readString(node.data.summary) ?? node.label, 52) ?? fallback;
    case "outcome":
      return truncateText(getOutcomeConclusion(node), 52) ?? fallback;
    default:
      return fallback;
  }
}

export function displayExcerpt(node: SessionGraphNode): string | null {
  const summary = readString(node.data.summary);
  const tool = readString(node.data.tool_name) ?? readString(node.data.tool);
  const intent = readString(node.data.intent);

  switch (getNodeType(node)) {
    case "goal":
    case "hypothesis":
    case "outcome":
      return null;
    case "action":
    case "exploit": {
      const excerpt = tool && intent ? `${tool} · ${intent}` : tool ?? intent ?? summary;
      return truncateText(excerpt, 54);
    }
    case "observation": {
      const finding = readString(node.data.finding) ?? summary;
      if (!finding) {
        return null;
      }

      const title = displayTitle(node);
      return truncateText(finding !== title ? finding : null, 54);
    }
    default:
      return truncateText(summary, 54);
  }
}

export function displayImportance(node: SessionGraphNode): AttackNodeDisplayEmphasis {
  switch (getNodeType(node)) {
    case "goal":
      return "goal";
    case "exploit":
    case "vulnerability":
    case "surface":
      return "critical";
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
    case "action":
    case "exploit":
      return truncateText(readString(node.data.intent) ?? readString(node.data.summary), 120);
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
