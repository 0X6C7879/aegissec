import { useEffect, useMemo } from "react";
import { formatDateTime } from "../lib/format";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import { AttackGraphCanvas } from "./AttackGraphCanvas";

type AttackGraphWorkbenchProps = {
  graph: SessionGraph | undefined;
  selectedNodeId: string | null;
  actionBusyId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  onEditNode: (node: SessionGraphNode) => Promise<void>;
  onRegenerateNode: (node: SessionGraphNode) => Promise<void>;
  onForkNode: (node: SessionGraphNode) => Promise<void>;
  onRollbackNode: (node: SessionGraphNode) => Promise<void>;
};

type TimelineItem = {
  id: string;
  label: string;
  value: string;
};

type DetailItem = {
  label: string;
  value: string;
};

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function safeJsonSummary(value: unknown): string | null {
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

function formatNodeStatusLabel(status: string | null): string {
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

function getNodeStatusTone(status: string | null): string {
  switch (status) {
    case "completed":
    case "done":
      return "tone-success";
    case "in_progress":
    case "running":
      return "tone-connected";
    case "blocked":
      return "tone-warning";
    case "failed":
    case "error":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

function formatAttackNodeType(nodeType: string): string {
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

function getRelationLabel(relation: string): string {
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

function getNodeStatus(node: SessionGraphNode): string | null {
  return readString(node.data.status);
}

function buildTimelineItems(node: SessionGraphNode | null): TimelineItem[] {
  if (!node) {
    return [];
  }

  const items: TimelineItem[] = [];
  const createdAt = readString(node.data.created_at);
  const startedAt = readString(node.data.started_at);
  const endedAt = readString(node.data.ended_at);
  const updatedAt = readString(node.data.updated_at);
  const sequence = readNumber(node.data.sequence);

  if (sequence !== null) {
    items.push({ id: `${node.id}-sequence`, label: "顺序", value: String(sequence) });
  }
  if (createdAt) {
    items.push({ id: `${node.id}-created`, label: "创建", value: formatDateTime(createdAt) });
  }
  if (startedAt) {
    items.push({ id: `${node.id}-started`, label: "开始", value: formatDateTime(startedAt) });
  }
  if (updatedAt) {
    items.push({ id: `${node.id}-updated`, label: "更新", value: formatDateTime(updatedAt) });
  }
  if (endedAt) {
    items.push({ id: `${node.id}-ended`, label: "结束", value: formatDateTime(endedAt) });
  }

  return items;
}

function getLatestNodeId(nodes: SessionGraphNode[]): string | null {
  if (nodes.length === 0) {
    return null;
  }

  let bestNode: SessionGraphNode | null = null;
  let bestTimestamp = -1;
  let bestSequence = -1;

  for (const node of nodes) {
    const timestamp = [
      readString(node.data.ended_at),
      readString(node.data.updated_at),
      readString(node.data.started_at),
      readString(node.data.created_at),
    ]
      .map((value) => (value ? new Date(value).getTime() : -1))
      .reduce((currentMax, candidate) => (candidate > currentMax ? candidate : currentMax), -1);
    const sequence = readNumber(node.data.sequence) ?? -1;

    if (
      bestNode === null ||
      timestamp > bestTimestamp ||
      (timestamp === bestTimestamp && sequence > bestSequence)
    ) {
      bestNode = node;
      bestTimestamp = timestamp;
      bestSequence = sequence;
    }
  }

  return bestNode?.id ?? nodes[nodes.length - 1]?.id ?? null;
}

function buildRelationContext(edges: SessionGraphEdge[], nodeId: string): string {
  const incoming = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => getRelationLabel(edge.relation));
  const outgoing = edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => getRelationLabel(edge.relation));

  const parts: string[] = [];
  if (incoming.length > 0) {
    parts.push(`进入：${incoming.join("、")}`);
  }
  if (outgoing.length > 0) {
    parts.push(`发出：${outgoing.join("、")}`);
  }

  return parts.length > 0 ? parts.join(" · ") : "暂无关联上下文";
}

function NodeFieldList({ node }: { node: SessionGraphNode }) {
  const ignoredKeys = new Set([
    "summary",
    "status",
    "source_message_id",
    "branch_id",
    "generation_id",
    "source_graphs",
    "provenance",
    "relation_context",
    "current",
  ]);
  const entries = Object.entries(node.data).filter(([key, value]) => {
    if (ignoredKeys.has(key)) {
      return false;
    }

    return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
  });

  if (entries.length === 0) {
    return <p className="management-empty-copy">当前节点没有额外标量字段。</p>;
  }

  return (
    <dl className="session-graph-data-list">
      {entries.map(([key, value]) => (
        <div key={`${node.id}-${key}`}>
          <dt>{key}</dt>
          <dd>{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function truncateText(value: string | null, maxLength: number): string | null {
  if (!value) {
    return null;
  }

  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, maxLength - 1).trimEnd()}…`;
}

function buildOverviewSummary(node: SessionGraphNode): string | null {
  const summary = readString(node.data.summary);
  const command = readString(node.data.command) ?? readString(node.data.tool_name) ?? readString(node.data.tool);
  const stdout = readString(node.data.stdout) ?? readString(node.data.result_text) ?? readString(node.data.observation);

  switch (node.node_type) {
    case "goal":
      return null;
    case "action":
    case "exploit":
      return truncateText(command ?? summary, 120);
    case "observation":
      return truncateText(stdout ?? summary, 120);
    case "hypothesis":
      return truncateText(summary, 120);
    case "outcome":
      return truncateText(summary ?? stdout, 120);
    default:
      return truncateText(summary, 120);
  }
}

function buildHighValueDetails(
  node: SessionGraphNode,
): DetailItem[] {
  const tool = readString(node.data.tool_name) ?? readString(node.data.tool);
  const command = readString(node.data.command);
  const result =
    readString(node.data.stdout) ??
    readString(node.data.result_text) ??
    readString(node.data.observation) ??
    readString(node.data.evidence);
  const summary = readString(node.data.summary);

  const items: DetailItem[] = [];

  switch (node.node_type) {
    case "action":
    case "exploit":
      if (command) {
        items.push({ label: "命令", value: truncateText(command, 160) ?? command });
      }
      if (tool) {
        items.push({ label: "工具", value: tool });
      }
      if (summary && summary !== command) {
        items.push({ label: "意图", value: truncateText(summary, 160) ?? summary });
      }
      break;
    case "observation":
      if (summary) {
        items.push({ label: "发现", value: truncateText(summary, 160) ?? summary });
      }
      if (result && result !== summary) {
        items.push({ label: "证据", value: truncateText(result, 200) ?? result });
      }
      break;
    case "hypothesis":
      if (summary) {
        items.push({ label: "假设", value: truncateText(summary, 160) ?? summary });
      }
      break;
    case "outcome":
      if (summary) {
        items.push({ label: "结果", value: truncateText(summary, 160) ?? summary });
      }
      if (result && result !== summary) {
        items.push({ label: "输出", value: truncateText(result, 200) ?? result });
      }
      break;
    default:
      if (summary) {
        items.push({ label: "摘要", value: truncateText(summary, 160) ?? summary });
      }
      break;
  }

  return items;
}

export function AttackGraphWorkbench({
  graph,
  selectedNodeId,
  actionBusyId,
  onSelectNode,
  onEditNode,
  onRegenerateNode,
  onForkNode,
  onRollbackNode,
}: AttackGraphWorkbenchProps) {
  const selectedNode = useMemo(
    () => graph?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graph?.nodes, selectedNodeId],
  );
  const selectedEdges = useMemo(
    () =>
      selectedNode
        ? (graph?.edges ?? []).filter(
            (edge) => edge.source === selectedNode.id || edge.target === selectedNode.id,
          )
        : [],
    [graph?.edges, selectedNode],
  );
  const timeline = useMemo(() => buildTimelineItems(selectedNode), [selectedNode]);
  const latestNodeId = useMemo(() => getLatestNodeId(graph?.nodes ?? []), [graph?.nodes]);

  const graphData: SessionGraph = graph ?? {
    session_id: "",
    workflow_run_id: "",
    graph_type: "attack",
    current_stage: null,
    nodes: [],
    edges: [],
  };

  const canvasGraph: SessionGraph = {
    ...graphData,
    nodes: graphData.nodes,
    edges: graphData.edges,
  };

  const canvasOverlay =
    graphData.nodes.length === 0
      ? {
          title: "等待攻击路径生成",
          copy: "当前还没有可展示的攻击节点",
        }
      : null;

  const sourceMessageId = selectedNode ? readString(selectedNode.data.source_message_id) : null;
  const branchId = selectedNode ? readString(selectedNode.data.branch_id) : null;
  const generationId = selectedNode ? readString(selectedNode.data.generation_id) : null;
  const isEditable = Boolean(sourceMessageId);
  const relationContext = selectedNode
    ? (readString(selectedNode.data.relation_context) ??
      buildRelationContext(selectedEdges, selectedNode.id))
    : null;
  const provenanceText = selectedNode
    ? readStringArray(selectedNode.data.source_graphs).join(" / ") ||
      safeJsonSummary(selectedNode.data.provenance)
    : null;
  const actionBusy = Boolean(sourceMessageId) && actionBusyId === sourceMessageId;
  const overviewSummary = selectedNode ? buildOverviewSummary(selectedNode) : null;
  const highValueDetails = selectedNode ? buildHighValueDetails(selectedNode) : [];
  const rawSummary = selectedNode ? readString(selectedNode.data.summary) : null;
  const hasFullSummary = Boolean(rawSummary && rawSummary !== overviewSummary);

  useEffect(() => {
    if (!selectedNode) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onSelectNode(null);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onSelectNode, selectedNode]);

  return (
    <div className="workspace-attack-graph-workbench" data-testid="attack-graph-workbench">
      <AttackGraphCanvas
        graph={canvasGraph}
        selectedNodeId={selectedNodeId}
        latestNodeId={latestNodeId}
        onSelectNode={onSelectNode}
        overlayTitle={canvasOverlay?.title ?? null}
        overlayCopy={canvasOverlay?.copy ?? null}
      />

      {selectedNode ? (
        <div className="management-modal-backdrop" role="presentation">
          <button
            className="management-modal-dismiss"
            type="button"
            aria-label="关闭节点详情"
            onClick={() => onSelectNode(null)}
          />
          <section
            className="management-modal-card panel workspace-node-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`${selectedNode.label} 详情`}
          >
            <div className="management-modal-header">
              <div>
                <strong className="management-list-title">{selectedNode.label}</strong>
                <p className="management-empty-copy">
                  {formatAttackNodeType(selectedNode.node_type)}
                </p>
              </div>
              <button
                className="button button-secondary"
                type="button"
                onClick={() => onSelectNode(null)}
              >
                关闭
              </button>
            </div>

            <div className="workspace-node-detail-modal-body">
              <div className="management-subcard">
                <div className="workspace-node-overview-header">
                  <span className="management-token-chip">{formatAttackNodeType(selectedNode.node_type)}</span>
                  <span
                    className={`management-status-badge ${getNodeStatusTone(getNodeStatus(selectedNode))}`}
                  >
                    {formatNodeStatusLabel(getNodeStatus(selectedNode))}
                  </span>
                </div>
                {overviewSummary ? <p className="session-graph-body-copy">{overviewSummary}</p> : null}
                <div className="session-graph-token-row">
                  {readBoolean(selectedNode.data.current) ||
                  readBoolean(selectedNode.data.active) ? (
                    <span className="management-token-chip">活跃节点</span>
                  ) : null}
                </div>
              </div>

              <div className="management-subcard workspace-node-detail-section">
                <div className="management-list-card-header">
                  <strong className="management-list-title">会话动作</strong>
                  <span className="management-status-badge tone-neutral">
                    {sourceMessageId ?? "无锚点"}
                  </span>
                </div>
                <div className="management-action-row">
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!isEditable || actionBusy}
                    onClick={() => void onEditNode(selectedNode)}
                  >
                    编辑
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!isEditable || actionBusy}
                    onClick={() => void onRegenerateNode(selectedNode)}
                  >
                    重生成
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!isEditable || actionBusy}
                    onClick={() => void onForkNode(selectedNode)}
                  >
                    分叉
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!isEditable || actionBusy}
                    onClick={() => void onRollbackNode(selectedNode)}
                  >
                    回滚
                  </button>
                </div>
                {!isEditable ? (
                  <p className="management-empty-copy">该节点缺少会话锚点，无法直接操作对话</p>
                ) : actionBusy ? (
                  <p className="management-empty-copy">正在执行会话动作，请稍候。</p>
                ) : null}
              </div>

              <div className="management-subcard workspace-node-detail-section">
                <div className="management-list-card-header">
                  <strong className="management-list-title">概览</strong>
                </div>
                <dl className="session-graph-data-list attack-graph-detail-list attack-graph-detail-list-compact">
                  <div>
                    <dt>节点类型</dt>
                    <dd>{formatAttackNodeType(selectedNode.node_type)}</dd>
                  </div>
                  <div>
                    <dt>标题</dt>
                    <dd>{selectedNode.label}</dd>
                  </div>
                  <div>
                    <dt>状态</dt>
                    <dd>{formatNodeStatusLabel(getNodeStatus(selectedNode))}</dd>
                  </div>
                  {overviewSummary ? (
                    <div>
                      <dt>摘要</dt>
                      <dd>{overviewSummary}</dd>
                    </div>
                  ) : null}
                </dl>
              </div>

              <div className="management-subcard workspace-node-detail-section">
                <div className="management-list-card-header">
                  <strong className="management-list-title">高价值内容</strong>
                </div>
                {highValueDetails.length === 0 ? (
                  <p className="management-empty-copy">当前节点没有额外的高价值展示内容。</p>
                ) : (
                  <dl className="session-graph-data-list attack-graph-detail-list attack-graph-detail-list-compact">
                    {highValueDetails.map((item) => (
                      <div key={`${selectedNode.id}-${item.label}`}>
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>

              <details className="management-subcard workspace-node-advanced-disclosure">
                <summary className="workspace-node-advanced-summary">高级信息</summary>
                <div className="workspace-node-advanced-body">
                  <div className="workspace-node-detail-section">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">调试字段</strong>
                    </div>
                    <dl className="session-graph-data-list attack-graph-detail-list">
                      {hasFullSummary ? (
                        <div>
                          <dt>完整摘要</dt>
                          <dd>{rawSummary}</dd>
                        </div>
                      ) : null}
                      <div>
                        <dt>source_message_id</dt>
                        <dd>{sourceMessageId ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>branch_id</dt>
                        <dd>{branchId ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>generation_id</dt>
                        <dd>{generationId ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>来源上下文</dt>
                        <dd>{provenanceText ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>关系上下文</dt>
                        <dd>{relationContext ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>可编辑</dt>
                        <dd>{isEditable ? "是" : "否"}</dd>
                      </div>
                    </dl>
                  </div>

                  <div className="workspace-node-detail-section">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">节点时间线</strong>
                      <span className="management-status-badge tone-neutral">{timeline.length}</span>
                    </div>
                    {timeline.length === 0 ? (
                      <p className="management-empty-copy">当前节点没有可展示的时间线字段。</p>
                    ) : (
                      <ul className="workspace-node-timeline-list">
                        {timeline.map((item) => (
                          <li key={item.id} className="workspace-node-timeline-item">
                            <span className="workspace-node-timeline-label">{item.label}</span>
                            <strong className="workspace-node-timeline-value">{item.value}</strong>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="workspace-node-detail-section">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">关联边</strong>
                      <span className="management-status-badge tone-neutral">{selectedEdges.length}</span>
                    </div>
                    {selectedEdges.length === 0 ? (
                      <p className="management-empty-copy">这个节点当前没有可展示的关联边。</p>
                    ) : (
                      <ul className="management-list">
                        {selectedEdges.map((edge) => (
                          <li key={edge.id} className="management-subcard workspace-graph-edge-card">
                            <strong className="management-list-title">
                              {edge.source} → {edge.target}
                            </strong>
                            <span className="management-token-chip">{getRelationLabel(edge.relation)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="workspace-node-detail-section">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">其他字段</strong>
                    </div>
                    <NodeFieldList node={selectedNode} />
                  </div>
                </div>
              </details>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
