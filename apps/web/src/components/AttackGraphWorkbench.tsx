import { useEffect, useMemo } from "react";
import { formatDateTime } from "../lib/format";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import { AttackGraphCanvas } from "./AttackGraphCanvas";
import {
  buildAttackNodeDetailSections,
  buildAttackNodeOverviewSummary,
  formatAttackNodeStatus,
  formatAttackNodeType,
  getAttackGraphAutoFocusNodeId,
  getAttackNodeStatusTone,
  getAttackRelationLabel,
  isCommandLikeAttackNode,
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

type NodeScalarField = {
  key: string;
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

function buildRelationContext(edges: SessionGraphEdge[], nodeId: string): string {
  const incoming = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => getAttackRelationLabel(edge.relation));
  const outgoing = edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => getAttackRelationLabel(edge.relation));

  const parts: string[] = [];
  if (incoming.length > 0) {
    parts.push(`进入: ${incoming.join(" / ")}`);
  }
  if (outgoing.length > 0) {
    parts.push(`发出: ${outgoing.join(" / ")}`);
  }

  return parts.length > 0 ? parts.join(" · ") : "暂无关联上下文。";
}

function getNodeScalarFields(node: SessionGraphNode): NodeScalarField[] {
  const ignoredKeys = new Set([
    "summary",
    "status",
    "goal",
    "content",
    "command",
    "primary_command",
    "observation_summary",
    "best_observation_summary",
    "stdout",
    "stderr",
    "result",
    "related_findings",
    "related_hypotheses",
    "supporting_actions",
    "best_path_summary",
    "current_action_summary",
    "key_observation_summary",
    "blocker",
    "next_step",
    "source_message_id",
    "branch_id",
    "generation_id",
    "source_graphs",
    "provenance",
    "relation_context",
    "current",
  ]);

  return Object.entries(node.data)
    .filter(([key, value]) => {
      if (ignoredKeys.has(key)) {
        return false;
      }

      return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
    })
    .map(([key, value]) => ({ key, value: String(value) }));
}

function NodeFieldList({ entries }: { entries: NodeScalarField[] }) {
  return (
    <dl className="session-graph-data-list">
      {entries.map(({ key, value }) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type NodeSessionActionControlsProps = {
  sourceMessageId: string | null;
  isEditable: boolean;
  actionBusy: boolean;
  selectedNode: SessionGraphNode;
  onEditNode: (node: SessionGraphNode) => Promise<void>;
  onRegenerateNode: (node: SessionGraphNode) => Promise<void>;
  onForkNode: (node: SessionGraphNode) => Promise<void>;
  onRollbackNode: (node: SessionGraphNode) => Promise<void>;
};

function NodeSessionActionControls({
  sourceMessageId,
  isEditable,
  actionBusy,
  selectedNode,
  onEditNode,
  onRegenerateNode,
  onForkNode,
  onRollbackNode,
}: NodeSessionActionControlsProps) {
  return (
    <div className="workspace-node-detail-section">
      <div className="management-list-card-header">
        <strong className="management-list-title">Conversation Controls</strong>
        <span className="management-status-badge tone-neutral">{sourceMessageId ?? "无锚点"}</span>
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
          重新生成
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
        <p className="management-empty-copy">该节点缺少会话锚点，无法直接对话操作。</p>
      ) : actionBusy ? (
        <p className="management-empty-copy">会话动作执行中，请稍候。</p>
      ) : null}
    </div>
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
  const graphData = useMemo<SessionGraph>(
    () =>
      graph ?? {
        session_id: "",
        workflow_run_id: "",
        graph_type: "attack",
        current_stage: null,
        nodes: [],
        edges: [],
      },
    [graph],
  );
  const selectedNode = useMemo(
    () => graphData.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graphData.nodes, selectedNodeId],
  );
  const selectedEdges = useMemo(
    () =>
      selectedNode
        ? graphData.edges.filter(
            (edge) => edge.source === selectedNode.id || edge.target === selectedNode.id,
          )
        : [],
    [graphData.edges, selectedNode],
  );
  const timeline = useMemo(() => buildTimelineItems(selectedNode), [selectedNode]);
  const defaultFocusNodeId = useMemo(
    () => getAttackGraphAutoFocusNodeId(graphData, null),
    [graphData],
  );

  const canvasOverlay =
    graphData.nodes.length === 0
      ? {
          title: "等待攻击路径生成",
          copy: "当前还没有可展示的攻击链节点。",
        }
      : null;

  const sourceMessageId = selectedNode ? readString(selectedNode.data.source_message_id) : null;
  const branchId = selectedNode ? readString(selectedNode.data.branch_id) : null;
  const generationId = selectedNode ? readString(selectedNode.data.generation_id) : null;
  const isEditable = Boolean(sourceMessageId);
  const relationContext = selectedNode
    ? (readString(selectedNode.data.relation_context) ??
      (() => {
        const context = buildRelationContext(selectedEdges, selectedNode.id);
        return context === "暂无关联上下文。" ? null : context;
      })())
    : null;
  const provenanceText = selectedNode
    ? readStringArray(selectedNode.data.source_graphs).join(" / ") ||
      safeJsonSummary(selectedNode.data.provenance)
    : null;
  const actionBusy = Boolean(sourceMessageId) && actionBusyId === sourceMessageId;
  const detailSections = selectedNode ? buildAttackNodeDetailSections(selectedNode) : [];
  const overviewSummary = selectedNode ? buildAttackNodeOverviewSummary(selectedNode) : null;
  const rawSummary = selectedNode ? safeJsonSummary(selectedNode.data) : null;
  const otherFieldEntries = selectedNode ? getNodeScalarFields(selectedNode) : [];
  const isCommandLikeNode = selectedNode ? isCommandLikeAttackNode(selectedNode) : false;
  const debugMetadataItems = [
    sourceMessageId ? { label: "source_message_id", value: sourceMessageId } : null,
    branchId ? { label: "branch_id", value: branchId } : null,
    generationId ? { label: "generation_id", value: generationId } : null,
    provenanceText ? { label: "来源上下文", value: provenanceText } : null,
    relationContext ? { label: "关系上下文", value: relationContext } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

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
    <div
      className="workspace-attack-graph-workbench workspace-stage-panel"
      data-testid="attack-graph-workbench"
    >
      <AttackGraphCanvas
        graph={graphData}
        selectedNodeId={selectedNodeId}
        latestNodeId={defaultFocusNodeId}
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
              {isCommandLikeNode ? (
                <div className="workspace-node-detail-modal-copy workspace-node-overview-header">
                  <strong className="management-list-title">{selectedNode.label}</strong>
                  <span
                    className={`management-status-badge ${getAttackNodeStatusTone(getNodeStatus(selectedNode))}`}
                  >
                    {formatAttackNodeStatus(getNodeStatus(selectedNode))}
                  </span>
                </div>
              ) : (
                <div className="workspace-node-detail-modal-copy">
                  <strong className="management-list-title">{selectedNode.label}</strong>
                  <p className="management-empty-copy">
                    {formatAttackNodeType(selectedNode.node_type)}
                  </p>
                </div>
              )}
              <button
                className="button button-secondary"
                type="button"
                onClick={() => onSelectNode(null)}
              >
                关闭
              </button>
            </div>

            <div className="workspace-node-detail-modal-body">
              {isCommandLikeNode ? null : (
                <div className="management-subcard">
                  <div className="management-list-card-header">
                    <strong className="management-list-title">Overview</strong>
                  </div>
                  <div className="workspace-node-overview-header">
                    <span className="management-token-chip">
                      {formatAttackNodeType(selectedNode.node_type)}
                    </span>
                    <span
                      className={`management-status-badge ${getAttackNodeStatusTone(getNodeStatus(selectedNode))}`}
                    >
                      {formatAttackNodeStatus(getNodeStatus(selectedNode))}
                    </span>
                  </div>
                  {overviewSummary ? (
                    <p className="session-graph-body-copy">{overviewSummary}</p>
                  ) : null}
                </div>
              )}

              {detailSections.map((section) =>
                section.items.length === 0 ? null : (
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

              {isCommandLikeNode ? (
                <details className="management-subcard workspace-node-advanced-disclosure">
                  <summary className="workspace-node-advanced-summary">高级 / 调试</summary>
                  <div className="workspace-node-advanced-body">
                    <NodeSessionActionControls
                      sourceMessageId={sourceMessageId}
                      isEditable={isEditable}
                      actionBusy={actionBusy}
                      selectedNode={selectedNode}
                      onEditNode={onEditNode}
                      onRegenerateNode={onRegenerateNode}
                      onForkNode={onForkNode}
                      onRollbackNode={onRollbackNode}
                    />

                    {debugMetadataItems.length > 0 ? (
                      <div className="workspace-node-detail-section">
                        <div className="management-list-card-header">
                          <strong className="management-list-title">调试元数据</strong>
                        </div>
                        <dl className="session-graph-data-list attack-graph-detail-list">
                          {debugMetadataItems.map((item) => (
                            <div key={`${selectedNode.id}-${item.label}`}>
                              <dt>{item.label}</dt>
                              <dd>{item.value}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    ) : null}

                    {timeline.length > 0 ? (
                      <div className="workspace-node-detail-section">
                        <div className="management-list-card-header">
                          <strong className="management-list-title">节点时间线</strong>
                          <span className="management-status-badge tone-neutral">
                            {timeline.length}
                          </span>
                        </div>
                        <ul className="workspace-node-timeline-list">
                          {timeline.map((item) => (
                            <li key={item.id} className="workspace-node-timeline-item">
                              <span className="workspace-node-timeline-label">{item.label}</span>
                              <strong className="workspace-node-timeline-value">
                                {item.value}
                              </strong>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {selectedEdges.length > 0 ? (
                      <div className="workspace-node-detail-section">
                        <div className="management-list-card-header">
                          <strong className="management-list-title">关联边</strong>
                          <span className="management-status-badge tone-neutral">
                            {selectedEdges.length}
                          </span>
                        </div>
                        <ul className="management-list">
                          {selectedEdges.map((edge) => (
                            <li
                              key={edge.id}
                              className="management-subcard workspace-graph-edge-card"
                            >
                              <strong className="management-list-title">
                                {edge.source} → {edge.target}
                              </strong>
                              <span className="management-token-chip">
                                {getAttackRelationLabel(edge.relation)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {otherFieldEntries.length > 0 ? (
                      <div className="workspace-node-detail-section">
                        <div className="management-list-card-header">
                          <strong className="management-list-title">其他字段</strong>
                        </div>
                        <NodeFieldList entries={otherFieldEntries} />
                      </div>
                    ) : null}

                    {rawSummary ? (
                      <div className="workspace-node-detail-section">
                        <div className="management-list-card-header">
                          <strong className="management-list-title">Raw payload</strong>
                        </div>
                        <pre className="session-graph-body-copy">{rawSummary}</pre>
                      </div>
                    ) : null}
                  </div>
                </details>
              ) : (
                <>
                  <details className="management-subcard workspace-node-detail-section">
                    <summary className="workspace-node-advanced-summary">会话动作</summary>
                    <div className="workspace-node-advanced-body">
                      <NodeSessionActionControls
                        sourceMessageId={sourceMessageId}
                        isEditable={isEditable}
                        actionBusy={actionBusy}
                        selectedNode={selectedNode}
                        onEditNode={onEditNode}
                        onRegenerateNode={onRegenerateNode}
                        onForkNode={onForkNode}
                        onRollbackNode={onRollbackNode}
                      />
                    </div>
                  </details>

                  {debugMetadataItems.length > 0 ||
                  timeline.length > 0 ||
                  selectedEdges.length > 0 ||
                  otherFieldEntries.length > 0 ? (
                    <details className="management-subcard workspace-node-advanced-disclosure">
                      <summary className="workspace-node-advanced-summary">高级信息</summary>
                      <div className="workspace-node-advanced-body">
                        {debugMetadataItems.length > 0 ? (
                          <div className="workspace-node-detail-section">
                            <div className="management-list-card-header">
                              <strong className="management-list-title">调试元数据</strong>
                            </div>
                            <dl className="session-graph-data-list attack-graph-detail-list">
                              {debugMetadataItems.map((item) => (
                                <div key={`${selectedNode.id}-${item.label}`}>
                                  <dt>{item.label}</dt>
                                  <dd>{item.value}</dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        ) : null}

                        {timeline.length > 0 ? (
                          <div className="workspace-node-detail-section">
                            <div className="management-list-card-header">
                              <strong className="management-list-title">节点时间线</strong>
                              <span className="management-status-badge tone-neutral">
                                {timeline.length}
                              </span>
                            </div>
                            <ul className="workspace-node-timeline-list">
                              {timeline.map((item) => (
                                <li key={item.id} className="workspace-node-timeline-item">
                                  <span className="workspace-node-timeline-label">
                                    {item.label}
                                  </span>
                                  <strong className="workspace-node-timeline-value">
                                    {item.value}
                                  </strong>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}

                        {selectedEdges.length > 0 ? (
                          <div className="workspace-node-detail-section">
                            <div className="management-list-card-header">
                              <strong className="management-list-title">关联边</strong>
                              <span className="management-status-badge tone-neutral">
                                {selectedEdges.length}
                              </span>
                            </div>
                            <ul className="management-list">
                              {selectedEdges.map((edge) => (
                                <li
                                  key={edge.id}
                                  className="management-subcard workspace-graph-edge-card"
                                >
                                  <strong className="management-list-title">
                                    {edge.source} → {edge.target}
                                  </strong>
                                  <span className="management-token-chip">
                                    {getAttackRelationLabel(edge.relation)}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}

                        {otherFieldEntries.length > 0 ? (
                          <div className="workspace-node-detail-section">
                            <div className="management-list-card-header">
                              <strong className="management-list-title">其他字段</strong>
                            </div>
                            <NodeFieldList entries={otherFieldEntries} />
                          </div>
                        ) : null}
                      </div>
                    </details>
                  ) : null}

                  {rawSummary ? (
                    <details className="management-subcard workspace-node-detail-section">
                      <summary className="workspace-node-advanced-summary">Raw payload</summary>
                      <div className="workspace-node-advanced-body">
                        <pre className="session-graph-body-copy">{rawSummary}</pre>
                      </div>
                    </details>
                  ) : null}
                </>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
