import type {
  AttackGraphNodeType,
  SessionGraph,
  SessionGraphEdge,
  SessionGraphNode,
} from "../types/graphs";

export type AttackNodeDisplayEmphasis = "goal" | "critical" | "result" | "supporting";

export type AttackNodeDetailItem = {
  label: string;
  value: string;
};

export type AttackNodeDetailSection = {
  title: string;
  items: AttackNodeDetailItem[];
};

export type AttackPathContext = {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
};

type GraphIndex = {
  nodeMap: Map<string, SessionGraphNode>;
  outgoing: Map<string, SessionGraphEdge[]>;
  incoming: Map<string, SessionGraphEdge[]>;
};

const OUTCOME_STATUS = new Set(["completed", "done", "confirmed", "success"]);
const BLOCKED_STATUS = new Set(["blocked", "failed", "error"]);
const ACTIVE_STATUS = new Set(["in_progress", "running"]);

export function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
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
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === "object" && !Array.isArray(item),
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

  return `${value.slice(0, maxLength - 3).trimEnd()}...`;
}

export function formatAttackNodeType(nodeType: string): string {
  switch (nodeType) {
    case "root":
    case "goal":
      return "目标";
    case "task":
      return "任务";
    case "action":
      return "动作";
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
      return "已阻断";
    case "failed":
    case "error":
      return "失败";
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
      return "推进";
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
      return "分支";
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

function getNodeStatus(node: SessionGraphNode): string | null {
  return readString(node.data.status);
}

function getObservationSummary(node: SessionGraphNode): string | null {
  return (
    readString(node.data.best_observation_summary) ??
    readString(node.data.observation_summary) ??
    readString(node.data.response_excerpt) ??
    truncateText(readString(node.data.stdout), 80)
  );
}

function getActionSummary(node: SessionGraphNode): string | null {
  return (
    readString(node.data.summary) ??
    readString(node.data.primary_command) ??
    readString(node.data.command) ??
    readString(node.data.request_summary) ??
    readString(node.data.tool_name) ??
    readString(node.data.tool)
  );
}

function getOutcomeSummary(node: SessionGraphNode): string | null {
  return (
    readString(node.data.conclusion) ??
    readString(node.data.summary) ??
    readString(node.data.content) ??
    readString(node.data.last_error)
  );
}

function hasFindings(node: SessionGraphNode): boolean {
  return readRecordArray(node.data.related_findings).length > 0;
}

function hasHypotheses(node: SessionGraphNode): boolean {
  return readRecordArray(node.data.related_hypotheses).length > 0;
}

function readMilestoneReasons(node: SessionGraphNode): Set<string> {
  return new Set(readStringArray(node.data.milestone_reasons).map((item) => item.toLowerCase()));
}

function isActiveNode(node: SessionGraphNode): boolean {
  const status = getNodeStatus(node);
  return readBoolean(node.data.current) || readBoolean(node.data.active) || ACTIVE_STATUS.has(status ?? "");
}

function hasOutcomeSupport(node: SessionGraphNode, index: GraphIndex): boolean {
  const outgoing = index.outgoing.get(node.id) ?? [];
  return outgoing.some((edge) => index.nodeMap.get(edge.target)?.node_type === "outcome");
}

function hasMeaningfulExecutionData(node: SessionGraphNode): boolean {
  return Boolean(getObservationSummary(node) ?? getActionSummary(node));
}

function normalizeFocusHint(value: string | null): string | null {
  if (!value) {
    return null;
  }

  const normalized = value.trim().toLowerCase().replace(/\s+/g, " ");
  return normalized.length > 0 ? normalized : null;
}

function pushNormalizedHint(target: string[], value: string | null): void {
  const normalized = normalizeFocusHint(value);
  if (!normalized) {
    return;
  }

  pushUnique(target, normalized);
}

function collectGraphFocusHints(graph: SessionGraph): string[] {
  const hints: string[] = [];

  for (const node of graph.nodes) {
    if (node.node_type === "root" || node.node_type === "goal") {
      pushNormalizedHint(hints, readString(node.data.best_path_summary));
      continue;
    }

    if (node.node_type === "task") {
      pushNormalizedHint(hints, readString(node.data.current_action_summary));
      pushNormalizedHint(hints, readString(node.data.key_observation_summary));
    }
  }

  return hints;
}

function collectTaskFocusHints(taskNode: SessionGraphNode | null): string[] {
  if (!taskNode || taskNode.node_type !== "task") {
    return [];
  }

  const hints: string[] = [];
  pushNormalizedHint(hints, readString(taskNode.data.current_action_summary));
  pushNormalizedHint(hints, readString(taskNode.data.key_observation_summary));
  return hints;
}

function matchesFocusHint(node: SessionGraphNode, focusHints: string[]): boolean {
  if (focusHints.length === 0) {
    return false;
  }

  const candidates = [
    normalizeFocusHint(node.label),
    normalizeFocusHint(getActionSummary(node)),
    normalizeFocusHint(getObservationSummary(node)),
  ].filter((value): value is string => Boolean(value));

  return candidates.some((candidate) =>
    focusHints.some(
      (hint) =>
        hint.length >= 4 && (candidate === hint || candidate.includes(hint) || hint.includes(candidate)),
    ),
  );
}

function buildGraphIndex(graph: SessionGraph): GraphIndex {
  const nodeMap = new Map(graph.nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, SessionGraphEdge[]>();
  const incoming = new Map<string, SessionGraphEdge[]>();

  for (const edge of graph.edges) {
    const nextOutgoing = outgoing.get(edge.source) ?? [];
    nextOutgoing.push(edge);
    outgoing.set(edge.source, nextOutgoing);

    const nextIncoming = incoming.get(edge.target) ?? [];
    nextIncoming.push(edge);
    incoming.set(edge.target, nextIncoming);
  }

  return { nodeMap, outgoing, incoming };
}

function getNodeTimestamp(node: SessionGraphNode): number {
  const candidates = [
    readString(node.data.last_seen_at),
    readString(node.data.completed_at),
    readString(node.data.updated_at),
    readString(node.data.ended_at),
    readString(node.data.started_at),
    readString(node.data.created_at),
  ];

  for (const value of candidates) {
    if (!value) {
      continue;
    }
    const parsed = new Date(value).getTime();
    if (Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  }

  return readNumber(node.data.sequence) ?? 0;
}

function getBestPathPriority(
  node: SessionGraphNode,
  index: GraphIndex,
  focusHints: string[],
): number {
  const status = getNodeStatus(node);
  const reasons = readMilestoneReasons(node);
  let priority = 0;

  if (matchesFocusHint(node, focusHints)) {
    priority += 700;
  }
  if (isActiveNode(node)) {
    priority += 600;
  } else if (BLOCKED_STATUS.has(status ?? "")) {
    priority += 500;
  } else if (reasons.has("outcome") || hasOutcomeSupport(node, index)) {
    priority += 400;
  } else if (hasFindings(node)) {
    priority += 300;
  } else if (hasHypotheses(node)) {
    priority += 200;
  } else if (hasMeaningfulExecutionData(node)) {
    priority += 100;
  }

  return priority;
}

function chooseBestNode(
  nodes: SessionGraphNode[],
  index: GraphIndex,
  focusHints: string[] = [],
): SessionGraphNode | null {
  if (nodes.length === 0) {
    return null;
  }

  const ordered = [...nodes].sort((left, right) => {
    const priorityDiff =
      getBestPathPriority(right, index, focusHints) - getBestPathPriority(left, index, focusHints);
    if (priorityDiff !== 0) {
      return priorityDiff;
    }

    const collaborationDiff =
      (readNumber(right.data.collaboration_value) ?? 0) -
      (readNumber(left.data.collaboration_value) ?? 0);
    if (collaborationDiff !== 0) {
      return collaborationDiff;
    }

    const attemptsDiff =
      (readNumber(right.data.attempts_count) ?? 0) - (readNumber(left.data.attempts_count) ?? 0);
    if (attemptsDiff !== 0) {
      return attemptsDiff;
    }

    const sequenceDiff = (readNumber(right.data.sequence) ?? 0) - (readNumber(left.data.sequence) ?? 0);
    if (sequenceDiff !== 0) {
      return sequenceDiff;
    }

    const timestampDiff = getNodeTimestamp(right) - getNodeTimestamp(left);
    if (timestampDiff !== 0) {
      return timestampDiff;
    }

    return right.id.localeCompare(left.id);
  });

  return ordered[0] ?? null;
}

function chooseBestOutcomeSupportAction(
  graph: SessionGraph,
  index: GraphIndex,
  focusHints: string[] = [],
): SessionGraphNode | null {
  const outcomeNodes = graph.nodes.filter((node) => node.node_type === "outcome");
  const completedOutcome = outcomeNodes.find((node) => OUTCOME_STATUS.has(getNodeStatus(node) ?? ""));
  const targetOutcome = completedOutcome ?? outcomeNodes[0];
  if (!targetOutcome) {
    return null;
  }

  const incoming = index.incoming.get(targetOutcome.id) ?? [];
  const incomingNodes = incoming.reduce<SessionGraphNode[]>((result, edge) => {
    const node = index.nodeMap.get(edge.source);
    if (node?.node_type === "action") {
      result.push(node);
    }
    return result;
  }, []);

  return chooseBestNode(incomingNodes, index, focusHints);
}

function chooseBestMilestoneAction(
  graph: SessionGraph,
  index: GraphIndex,
  focusHints: string[] = [],
): SessionGraphNode | null {
  const actionNodes = graph.nodes.filter((node) => node.node_type === "action");
  return chooseBestNode(actionNodes, index, focusHints);
}

function chooseDefaultFocusNode(graph: SessionGraph, latestNodeId: string | null, index: GraphIndex): SessionGraphNode | null {
  const focusHints = collectGraphFocusHints(graph);
  const bestMilestoneAction = chooseBestMilestoneAction(graph, index, focusHints);
  if (bestMilestoneAction) {
    return bestMilestoneAction;
  }

  if (latestNodeId) {
    const latestNode = index.nodeMap.get(latestNodeId);
    if (latestNode && latestNode.node_type === "action") {
      return latestNode;
    }
  }

  return chooseBestOutcomeSupportAction(graph, index, focusHints);
}

function pushUnique(target: string[], value: string): void {
  if (!target.includes(value)) {
    target.push(value);
  }
}

function resolveActionTaskId(actionId: string, index: GraphIndex): string | null {
  const actionNode = index.nodeMap.get(actionId);
  const explicitTaskId = actionNode ? readString(actionNode.data.task_id) : null;
  if (explicitTaskId) {
    return explicitTaskId;
  }

  const incoming = index.incoming.get(actionId) ?? [];
  const taskEdge = incoming.find((edge) => index.nodeMap.get(edge.source)?.node_type === "task");
  return taskEdge?.source ?? null;
}

function actionBelongsToTask(node: SessionGraphNode, taskId: string, index: GraphIndex): boolean {
  if (node.node_type !== "action") {
    return false;
  }

  return resolveActionTaskId(node.id, index) === taskId;
}

function chooseBestIncomingTaskEdge(taskId: string, index: GraphIndex): SessionGraphEdge | null {
  const incoming = index.incoming.get(taskId) ?? [];
  const candidates = incoming.filter((edge) => {
    const sourceNode = index.nodeMap.get(edge.source);
    return sourceNode?.node_type === "task" || sourceNode?.node_type === "root";
  });
  if (candidates.length === 0) {
    return null;
  }

  return [...candidates].sort((left, right) => {
    const leftNode = index.nodeMap.get(left.source);
    const rightNode = index.nodeMap.get(right.source);
    const leftPriority = leftNode?.node_type === "root" ? 100 : getNodeTimestamp(leftNode ?? index.nodeMap.get(taskId)!);
    const rightPriority = rightNode?.node_type === "root" ? 100 : getNodeTimestamp(rightNode ?? index.nodeMap.get(taskId)!);
    return rightPriority - leftPriority;
  })[0]!;
}

function buildTaskDependencyChain(taskId: string, index: GraphIndex): string[] {
  const chain: string[] = [];
  let currentTaskId: string | null = taskId;
  const visited = new Set<string>();

  while (currentTaskId && !visited.has(currentTaskId)) {
    visited.add(currentTaskId);
    chain.unshift(currentTaskId);
    const edge = chooseBestIncomingTaskEdge(currentTaskId, index);
    if (!edge) {
      break;
    }
    currentTaskId = edge.source;
  }

  return chain;
}

function chooseBestPrecedingAction(actionId: string, taskId: string | null, index: GraphIndex): SessionGraphEdge | null {
  const incoming = index.incoming.get(actionId) ?? [];
  const candidates = incoming.filter((edge) => {
    if (edge.relation !== "precedes") {
      return false;
    }
    const sourceNode = index.nodeMap.get(edge.source);
    if (!sourceNode || sourceNode.node_type !== "action") {
      return false;
    }
    if (!taskId) {
      return true;
    }
    return readString(sourceNode.data.task_id) === taskId;
  });

  if (candidates.length === 0) {
    return null;
  }

  return [...candidates].sort((left, right) => {
    const leftNode = index.nodeMap.get(left.source);
    const rightNode = index.nodeMap.get(right.source);
    return getNodeTimestamp(rightNode!) - getNodeTimestamp(leftNode!);
  })[0]!;
}

function buildActionAncestorChain(actionId: string, taskId: string | null, index: GraphIndex): string[] {
  const chain = [actionId];
  let currentActionId = actionId;
  const visited = new Set<string>([actionId]);

  while (true) {
    const edge = chooseBestPrecedingAction(currentActionId, taskId, index);
    if (!edge || visited.has(edge.source)) {
      break;
    }
    visited.add(edge.source);
    chain.unshift(edge.source);
    currentActionId = edge.source;
  }

  return chain;
}

function chooseBestFollowingAction(actionId: string, taskId: string | null, index: GraphIndex): SessionGraphEdge | null {
  const outgoing = index.outgoing.get(actionId) ?? [];
  const candidates = outgoing.filter((edge) => {
    if (edge.relation !== "precedes") {
      return false;
    }
    const targetNode = index.nodeMap.get(edge.target);
    if (!targetNode || targetNode.node_type !== "action") {
      return false;
    }
    if (!taskId) {
      return true;
    }
    return readString(targetNode.data.task_id) === taskId;
  });

  if (candidates.length === 0) {
    return null;
  }

  return [...candidates].sort((left, right) => {
    const leftNode = index.nodeMap.get(left.target);
    const rightNode = index.nodeMap.get(right.target);
    return getNodeTimestamp(rightNode!) - getNodeTimestamp(leftNode!);
  })[0]!;
}

function buildActionOutcomeTail(actionId: string, taskId: string | null, index: GraphIndex): string[] {
  const tail: string[] = [];
  let currentActionId = actionId;
  const visited = new Set<string>([actionId]);

  while (true) {
    const outgoing = index.outgoing.get(currentActionId) ?? [];
    const directOutcome = outgoing.find((edge) => index.nodeMap.get(edge.target)?.node_type === "outcome");
    if (directOutcome) {
      pushUnique(tail, directOutcome.target);
      return tail;
    }

    const nextEdge = chooseBestFollowingAction(currentActionId, taskId, index);
    if (!nextEdge || visited.has(nextEdge.target)) {
      return tail;
    }
    visited.add(nextEdge.target);
    pushUnique(tail, nextEdge.target);
    currentActionId = nextEdge.target;
  }
}

function addChainToContext(chain: string[], context: AttackPathContext, index: GraphIndex): void {
  for (const nodeId of chain) {
    context.nodeIds.add(nodeId);
  }

  for (let i = 0; i < chain.length - 1; i += 1) {
    const source = chain[i];
    const target = chain[i + 1];
    const edge = (index.outgoing.get(source) ?? []).find((candidate) => candidate.target === target);
    if (edge) {
      context.edgeIds.add(edge.id);
    }
  }
}

function buildContextFromAction(actionId: string, index: GraphIndex): AttackPathContext {
  const context: AttackPathContext = { nodeIds: new Set<string>(), edgeIds: new Set<string>() };
  const actionNode = index.nodeMap.get(actionId);
  if (!actionNode) {
    return context;
  }

  const taskId = resolveActionTaskId(actionId, index);
  if (taskId && index.nodeMap.has(taskId)) {
    addChainToContext(buildTaskDependencyChain(taskId, index), context, index);
  } else {
    const rootNode = [...index.nodeMap.values()].find((node) => node.node_type === "root");
    if (rootNode) {
      context.nodeIds.add(rootNode.id);
    }
  }

  const actionChain = buildActionAncestorChain(actionId, taskId, index);
  if (taskId && actionChain.length > 0) {
    const edge = (index.outgoing.get(taskId) ?? []).find((candidate) => candidate.target === actionChain[0]);
    if (edge) {
      context.edgeIds.add(edge.id);
    }
    context.nodeIds.add(taskId);
  }
  addChainToContext(actionChain, context, index);

  const tail = buildActionOutcomeTail(actionId, taskId, index);
  if (tail.length > 0) {
    addChainToContext([actionId, ...tail], context, index);
  }

  context.nodeIds.add(actionId);
  return context;
}

export function chooseRepresentativeMilestoneChain(graph: SessionGraph, taskId: string): string[] {
  const index = buildGraphIndex(graph);
  const taskChain = buildTaskDependencyChain(taskId, index);
  const taskNode = index.nodeMap.get(taskId) ?? null;
  const actions = graph.nodes.filter(
    (node) => actionBelongsToTask(node, taskId, index),
  );
  const focusAction = chooseBestNode(actions, index, collectTaskFocusHints(taskNode));
  if (!focusAction) {
    return taskChain;
  }

  const actionChain = buildActionAncestorChain(focusAction.id, taskId, index);
  const tail = buildActionOutcomeTail(focusAction.id, taskId, index);
  const chain = [...taskChain, ...actionChain, ...tail];

  return chain.filter((value, indexValue) => chain.indexOf(value) === indexValue);
}

export function getBestExecutionPathContext(
  graph: SessionGraph,
  selectedNodeId: string | null,
  latestNodeId: string | null,
): AttackPathContext {
  const index = buildGraphIndex(graph);
  const emptyContext: AttackPathContext = { nodeIds: new Set<string>(), edgeIds: new Set<string>() };

  const selectedNode = selectedNodeId ? index.nodeMap.get(selectedNodeId) ?? null : null;
  if (selectedNode?.node_type === "task") {
    const chain = chooseRepresentativeMilestoneChain(graph, selectedNode.id);
    addChainToContext(chain, emptyContext, index);
    return emptyContext;
  }
  if (selectedNode?.node_type === "action") {
    return buildContextFromAction(selectedNode.id, index);
  }
  if (selectedNode?.node_type === "outcome") {
    const supportAction = chooseBestOutcomeSupportAction(graph, index, collectGraphFocusHints(graph));
    if (supportAction) {
      const context = buildContextFromAction(supportAction.id, index);
      context.nodeIds.add(selectedNode.id);
      return context;
    }
  }
  if (selectedNode?.node_type === "root") {
    const focusNode = chooseDefaultFocusNode(graph, latestNodeId, index);
    if (focusNode) {
      return buildContextFromAction(focusNode.id, index);
    }
  }

  const defaultFocusNode = chooseDefaultFocusNode(graph, latestNodeId, index);
  if (defaultFocusNode) {
    return buildContextFromAction(defaultFocusNode.id, index);
  }

  if (selectedNode) {
    emptyContext.nodeIds.add(selectedNode.id);
  }
  return emptyContext;
}

export function displayTitle(node: SessionGraphNode): string {
  const fallback = truncateText(node.label, 52) ?? node.label;

  switch (getNodeType(node)) {
    case "goal":
    case "root":
      return truncateText(readString(node.data.goal) ?? readString(node.data.title) ?? fallback, 52) ?? fallback;
    case "task":
      return (
        truncateText(
          readString(node.data.title) ??
            readString(node.data.task_name) ??
            readString(node.data.current_action_summary) ??
            fallback,
          52,
        ) ?? fallback
      );
    case "action":
      return truncateText(getActionSummary(node), 52) ?? fallback;
    case "outcome":
      return truncateText(getOutcomeSummary(node) ?? fallback, 52) ?? fallback;
    default:
      return fallback;
  }
}

export function displayExcerpt(node: SessionGraphNode): string | null {
  if (node.node_type === "root") {
    return truncateText(readString(node.data.best_path_summary) ?? readString(node.data.goal), 72);
  }
  if (node.node_type === "task") {
    return truncateText(
      readString(node.data.key_observation_summary) ??
        readString(node.data.current_action_summary) ??
        readString(node.data.summary),
      72,
    );
  }
  if (node.node_type === "outcome") {
    return truncateText(getOutcomeSummary(node), 72);
  }

  const excerpt = getObservationSummary(node);
  if (!excerpt) {
    return null;
  }

  const title = displayTitle(node);
  return excerpt === title ? null : truncateText(excerpt, 72);
}

export function displayImportance(node: SessionGraphNode): AttackNodeDisplayEmphasis {
  const status = getNodeStatus(node);
  if (node.node_type === "root" || node.node_type === "goal") {
    return "goal";
  }
  if (node.node_type === "outcome") {
    return "result";
  }
  if (
    node.node_type === "action" &&
    (
      isActiveNode(node) ||
      BLOCKED_STATUS.has(status ?? "") ||
      hasFindings(node) ||
      hasHypotheses(node) ||
      (readNumber(node.data.collaboration_value) ?? 0) >= 60
    )
  ) {
    return "critical";
  }
  return "supporting";
}

export function buildAttackNodeOverviewSummary(node: SessionGraphNode): string | null {
  if (node.node_type === "root") {
    return truncateText(readString(node.data.goal) ?? readString(node.data.best_path_summary), 140);
  }
  if (node.node_type === "task") {
    return truncateText(
      readString(node.data.summary) ??
        readString(node.data.current_action_summary) ??
        readString(node.data.key_observation_summary),
      140,
    );
  }
  if (node.node_type === "action") {
    return truncateText(getObservationSummary(node) ?? getActionSummary(node), 140);
  }
  if (node.node_type === "outcome") {
    return truncateText(getOutcomeSummary(node), 140);
  }
  return truncateText(readString(node.data.summary), 140);
}

export function isCommandLikeAction(node: SessionGraphNode): boolean {
  if (node.node_type !== "action") {
    return false;
  }

  const toolName = (readString(node.data.tool_name) ?? readString(node.data.tool) ?? "").toLowerCase();
  return (
    Boolean(readString(node.data.command)) ||
    /shell|bash|zsh|powershell|kali|execute|command|terminal|ctx_execute/.test(toolName)
  );
}

function buildListValue(items: string[]): string | null {
  if (items.length === 0) {
    return null;
  }

  return items.join(" / ");
}

function buildFindingValue(finding: Record<string, unknown>): string | null {
  return (
    readString(finding.title) ??
    readString(finding.label) ??
    readString(finding.summary) ??
    readString(finding.kind)
  );
}

function buildHypothesisValue(hypothesis: Record<string, unknown>): string | null {
  const summary = readString(hypothesis.summary) ?? readString(hypothesis.result) ?? readString(hypothesis.kind);
  const status = readString(hypothesis.status);
  if (!summary && !status) {
    return null;
  }

  return [summary, status].filter(Boolean).join(" / ");
}

export function buildAttackNodeDetailSections(node: SessionGraphNode): AttackNodeDetailSection[] {
  if (node.node_type === "root") {
    return [
      {
        title: "Goal",
        items: [
          { label: "目标", value: readString(node.data.goal) ?? node.label },
          ...(readString(node.data.current_stage)
            ? [{ label: "当前阶段", value: readString(node.data.current_stage)! }]
            : []),
          ...(readString(node.data.best_path_summary)
            ? [{ label: "当前工作链", value: readString(node.data.best_path_summary)! }]
            : []),
        ],
      },
    ].filter((section) => section.items.length > 0);
  }

  if (node.node_type === "task") {
    return [
      {
        title: "Focus",
        items: [
          { label: "子目标", value: readString(node.data.title) ?? readString(node.data.task_name) ?? node.label },
          ...(readString(node.data.summary)
            ? [{ label: "为什么做", value: readString(node.data.summary)! }]
            : []),
          ...(readString(node.data.key_observation_summary)
            ? [{ label: "关键发现", value: readString(node.data.key_observation_summary)! }]
            : []),
          ...(readString(node.data.blocker)
            ? [{ label: "阻断点", value: readString(node.data.blocker)! }]
            : []),
          ...(readString(node.data.next_step)
            ? [{ label: "下一步", value: readString(node.data.next_step)! }]
            : []),
        ],
      },
    ].filter((section) => section.items.length > 0);
  }

  if (node.node_type === "outcome") {
    const supportingActions = readStringArray(node.data.supporting_actions);
    return [
      {
        title: "Outcome",
        items: [
          { label: "结论", value: getOutcomeSummary(node) ?? node.label },
          ...(supportingActions.length > 0
            ? [{ label: "关键动作", value: buildListValue(supportingActions)! }]
            : []),
        ],
      },
    ].filter((section) => section.items.length > 0);
  }

  if (isCommandLikeAction(node)) {
    return [
      {
        title: "Command",
        items: [{ label: "完整命令", value: readString(node.data.command) ?? readString(node.data.primary_command) ?? node.label }],
      },
      {
        title: "Observation",
        items: getObservationSummary(node)
          ? [{ label: "观测摘要", value: getObservationSummary(node)! }]
          : [],
      },
    ].filter((section) => section.items.length > 0);
  }

  const findings = readRecordArray(node.data.related_findings)
    .map(buildFindingValue)
    .filter((value): value is string => Boolean(value));
  const hypotheses = readRecordArray(node.data.related_hypotheses)
    .map(buildHypothesisValue)
    .filter((value): value is string => Boolean(value));

  return [
    {
      title: "Why",
      items: [
        ...(readString(node.data.thought) ? [{ label: "Why", value: readString(node.data.thought)! }] : []),
        ...(readString(node.data.summary) ? [{ label: "Action summary", value: readString(node.data.summary)! }] : []),
      ],
    },
    {
      title: "Observation",
      items: [
        ...(getObservationSummary(node) ? [{ label: "Observation", value: getObservationSummary(node)! }] : []),
      ],
    },
    {
      title: "Interpretation",
      items: [
        ...findings.map((value, index) => ({ label: `发现 ${index + 1}`, value })),
        ...hypotheses.map((value, index) => ({ label: `假设 ${index + 1}`, value })),
        ...(readString(node.data.blocked_reason)
          ? [{ label: "Interpretation", value: readString(node.data.blocked_reason)! }]
          : []),
      ],
    },
  ].filter((section) => section.items.length > 0);
}

export function getAttackGraphAutoFocusNodeId(
  graph: SessionGraph,
  latestNodeId: string | null,
): string | null {
  const index = buildGraphIndex(graph);
  const focusNode = chooseDefaultFocusNode(graph, latestNodeId, index);
  if (focusNode) {
    return focusNode.id;
  }

  const currentTaskNode = graph.nodes.find(
    (node) => node.node_type === "task" && (readBoolean(node.data.current) || readBoolean(node.data.active)),
  );
  if (currentTaskNode) {
    return currentTaskNode.id;
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
      const collaboration = readNumber(node.data.collaboration_value) ?? 0;

      return `${node.id}:${status}:${current}:${updatedAt}:${collaboration}`;
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
