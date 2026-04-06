import { useEffect, useMemo } from "react";
import { formatDateTime } from "../lib/format";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import { AttackGraphCanvas } from "./AttackGraphCanvas";
import {
  buildAttackNodeDetailSections,
  formatAttackNodeStatus,
  formatAttackNodeType,
  getAttackNodeStatusTone,
  getAttackRelationLabel,
  readNumber,
  readString,
  readStringArray,
  safeJsonSummary,
} from "./AttackGraphCanvas.utils";

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

  const nodePriority = (node: SessionGraphNode): number => {
    if (node.node_type === "action") {
      return 0;
    }
    if (node.node_type === "task") {
      return 1;
    }
    if (node.node_type === "outcome") {
      return 2;
    }
    if (node.node_type === "root") {
      return 3;
    }
    return 4;
  };

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
      nodePriority(node) < nodePriority(bestNode) ||
      (nodePriority(node) === nodePriority(bestNode) && timestamp > bestTimestamp) ||
      (nodePriority(node) === nodePriority(bestNode) && timestamp === bestTimestamp && sequence > bestSequence)
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
    .map((edge) => getAttackRelationLabel(edge.relation));
  const outgoing = edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => getAttackRelationLabel(edge.relation));

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
  const detailSections = selectedNode ? buildAttackNodeDetailSections(selectedNode) : [];
  const rawSummary = selectedNode ? safeJsonSummary(selectedNode.data) : null;
  const basicSection = detailSections.find((section) => section.title === "Basic") ?? null;
  const rawSection = detailSections.find((section) => section.title === "Raw") ?? null;
  const visibleSections = detailSections.filter((section) => section.title !== "Raw");
  const basicSummary =
    selectedNode ? readString(selectedNode.data.summary) ?? readString(selectedNode.data.goal) ?? null : null;
  const hasFullSummary = Boolean(rawSummary && rawSummary !== basicSummary);

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
                <div className="management-list-card-header">
                  <strong className="management-list-title">Basic</strong>
                </div>
                <div className="workspace-node-overview-header">
                  <span className="management-token-chip">{formatAttackNodeType(selectedNode.node_type)}</span>
                  <span
                    className={`management-status-badge ${getAttackNodeStatusTone(getNodeStatus(selectedNode))}`}
                  >
                    {formatAttackNodeStatus(getNodeStatus(selectedNode))}
                  </span>
                </div>
                {basicSummary ? <p className="session-graph-body-copy">{basicSummary}</p> : null}
                {basicSection && basicSection.items.length > 0 ? (
                  <dl className="session-graph-data-list attack-graph-detail-list attack-graph-detail-list-compact">
                    {basicSection.items.map((item) => (
                      <div key={`${selectedNode.id}-${basicSection.title}-${item.label}`}>
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </div>

              {visibleSections.map((section) =>
                section.title === "Basic" || section.items.length === 0 ? null : (
                <div
                  key={`${selectedNode.id}-${section.title}`}
                  className="management-subcard workspace-node-detail-section"
                >
                  <div className="management-list-card-header">
                    <strong className="management-list-title">{section.title}</strong>
                  </div>
                  <dl className="session-graph-data-list attack-graph-detail-list attack-graph-detail-list-compact">
                    {section.items.map((item) => (
                      <div key={`${selectedNode.id}-${section.title}-${item.label}`}>
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                ),
              )}

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

              {rawSection && rawSection.items.length > 0 ? (
                <details className="management-subcard workspace-node-detail-section">
                  <summary className="workspace-node-advanced-summary">Raw</summary>
                  <div className="workspace-node-advanced-body">
                    <dl className="session-graph-data-list attack-graph-detail-list">
                      {hasFullSummary ? (
                        <div>
                          <dt>完整摘要</dt>
                          <dd>{rawSummary}</dd>
                        </div>
                      ) : null}
                      {rawSection.items.map((item) => (
                        <div key={`${selectedNode.id}-raw-${item.label}`}>
                          <dt>{item.label}</dt>
                          <dd>{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                </details>
              ) : null}

              <details className="management-subcard workspace-node-advanced-disclosure">
                <summary className="workspace-node-advanced-summary">高级信息</summary>
                <div className="workspace-node-advanced-body">
                  <div className="workspace-node-detail-section">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">调试元数据</strong>
                    </div>
                    <dl className="session-graph-data-list attack-graph-detail-list">
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
                            <span className="management-token-chip">{getAttackRelationLabel(edge.relation)}</span>
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
