import { useMemo, useState } from "react";
import { formatDateTime } from "../lib/format";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import { AttackGraphCanvas } from "./AttackGraphCanvas";

type AttackGraphWorkbenchProps = {
  graph: SessionGraph | undefined;
  selectedNodeId: string | null;
  actionBusyId: string | null;
  onSelectNode: (nodeId: string) => void;
  onEditNode: (node: SessionGraphNode) => Promise<void>;
  onRegenerateNode: (node: SessionGraphNode) => Promise<void>;
  onForkNode: (node: SessionGraphNode) => Promise<void>;
  onRollbackNode: (node: SessionGraphNode) => Promise<void>;
};

type AttackGraphFilterState = {
  search: string;
  status: string;
  nodeType: string;
};

type TimelineItem = {
  id: string;
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

function buildTimelineItems(
  node: SessionGraphNode | null,
): TimelineItem[] {
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
  const incoming = edges.filter((edge) => edge.target === nodeId).map((edge) => getRelationLabel(edge.relation));
  const outgoing = edges.filter((edge) => edge.source === nodeId).map((edge) => getRelationLabel(edge.relation));

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
  const [filters, setFilters] = useState<AttackGraphFilterState>({
    search: "",
    status: "all",
    nodeType: "all",
  });

  const statusOptions = useMemo(() => {
    const values = new Set<string>();
    for (const node of graph?.nodes ?? []) {
      const status = getNodeStatus(node);
      if (status) {
        values.add(status);
      }
    }
    return [...values];
  }, [graph]);

  const nodeTypeOptions = useMemo(
    () => [...new Set((graph?.nodes ?? []).map((node) => node.node_type))],
    [graph],
  );

  const visibleNodes = useMemo(() => {
    const keyword = filters.search.trim().toLowerCase();

    return (graph?.nodes ?? []).filter((node) => {
      if (filters.status !== "all" && getNodeStatus(node) !== filters.status) {
        return false;
      }

      if (filters.nodeType !== "all" && node.node_type !== filters.nodeType) {
        return false;
      }

      if (keyword.length === 0) {
        return true;
      }

      const haystack = [node.label, ...Object.values(node.data).map((value) => String(value))]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [filters.nodeType, filters.search, filters.status, graph]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () =>
      (graph?.edges ?? []).filter(
        (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
      ),
    [graph?.edges, visibleNodeIds],
  );

  const selectedNode = useMemo(
    () => visibleNodes.find((node) => node.id === selectedNodeId) ?? graph?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graph?.nodes, selectedNodeId, visibleNodes],
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
  const latestNodeId = useMemo(() => getLatestNodeId(visibleNodes), [visibleNodes]);

  const graphData: SessionGraph =
    graph ?? {
      session_id: "",
      workflow_run_id: "",
      graph_type: "attack",
      current_stage: null,
      nodes: [],
      edges: [],
    };

  const canvasGraph: SessionGraph = {
    ...graphData,
    nodes: visibleNodes,
    edges: visibleEdges,
  };

  const canvasOverlay =
    graphData.nodes.length === 0
      ? {
          title: "等待攻击路径生成",
          copy: "攻击图主画布已就绪，当前还没有可展示的攻击节点。",
        }
      : visibleNodes.length === 0
        ? {
            title: "当前筛选无结果",
            copy: "调整搜索、状态或节点类型后继续查看攻击路径。",
          }
        : null;

  const sourceMessageId = selectedNode ? readString(selectedNode.data.source_message_id) : null;
  const branchId = selectedNode ? readString(selectedNode.data.branch_id) : null;
  const generationId = selectedNode ? readString(selectedNode.data.generation_id) : null;
  const isEditable = Boolean(sourceMessageId);
  const relationContext = selectedNode
    ? readString(selectedNode.data.relation_context) ?? buildRelationContext(selectedEdges, selectedNode.id)
    : null;
  const provenanceText = selectedNode
    ? readStringArray(selectedNode.data.source_graphs).join(" / ") || safeJsonSummary(selectedNode.data.provenance)
    : null;
  const actionBusy = Boolean(sourceMessageId) && actionBusyId === sourceMessageId;

  return (
    <div className="workspace-right-stack" data-testid="attack-graph-workbench">
      <section className="management-section-card workspace-attack-graph-panel">
        <div className="management-section-header">
          <h3 className="management-section-title">攻击图</h3>
          <span className="management-status-badge tone-neutral">
            {visibleNodes.length} / {graphData.nodes.length} 节点
          </span>
        </div>

        <div className="workspace-graph-toolbar">
          <input
            className="management-search-input"
            type="search"
            value={filters.search}
            onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
            placeholder="搜索攻击节点"
          />
          <select
            className="field-input workspace-graph-select"
            value={filters.status}
            onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}
          >
            <option value="all">全部状态</option>
            {statusOptions.map((status) => (
              <option key={status} value={status}>
                  {formatNodeStatusLabel(status)}
              </option>
            ))}
          </select>
          <select
            className="field-input workspace-graph-select"
            value={filters.nodeType}
            onChange={(event) => setFilters((current) => ({ ...current, nodeType: event.target.value }))}
          >
            <option value="all">全部类型</option>
            {nodeTypeOptions.map((nodeType) => (
              <option key={nodeType} value={nodeType}>
                {formatAttackNodeType(nodeType)}
              </option>
            ))}
          </select>
        </div>

        <AttackGraphCanvas
          graph={canvasGraph}
          selectedNodeId={selectedNodeId}
          latestNodeId={latestNodeId}
          onSelectNode={onSelectNode}
          overlayTitle={canvasOverlay?.title ?? null}
          overlayCopy={canvasOverlay?.copy ?? null}
        />
      </section>

      <section className="management-section-card workspace-node-detail-panel">
        <div className="management-section-header">
          <h3 className="management-section-title">节点详情</h3>
          {selectedNode ? (
                <span className={`management-status-badge ${getNodeStatusTone(getNodeStatus(selectedNode))}`}>
                  {formatNodeStatusLabel(getNodeStatus(selectedNode))}
                </span>
          ) : null}
        </div>

        {!selectedNode ? (
          <div className="management-empty-state session-graph-inline-empty">
            <p className="management-empty-title">尚未选择节点</p>
            <p className="management-empty-copy">点击攻击图中的任意节点后，这里会显示路径细节和可操作项。</p>
          </div>
        ) : (
          <>
            <div className="management-subcard">
              <div className="management-list-card-header">
                <strong className="management-list-title">{selectedNode.label}</strong>
                <span className="management-token-chip">{formatAttackNodeType(selectedNode.node_type)}</span>
              </div>
              {readString(selectedNode.data.summary) ? (
                <p className="session-graph-body-copy">{readString(selectedNode.data.summary)}</p>
              ) : null}
              <div className="session-graph-token-row">
                <span className="management-token-chip">图谱：攻击图</span>
                {readBoolean(selectedNode.data.current) || readBoolean(selectedNode.data.active) ? (
                  <span className="management-token-chip">当前节点</span>
                ) : null}
                <span className="management-token-chip">{isEditable ? "可编辑" : "不可编辑"}</span>
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
                <strong className="management-list-title">关键字段</strong>
              </div>
              <dl className="session-graph-data-list attack-graph-detail-list">
                <div>
                  <dt>节点类型</dt>
                  <dd>{formatAttackNodeType(selectedNode.node_type)}</dd>
                </div>
                <div>
                  <dt>节点标签</dt>
                  <dd>{selectedNode.label}</dd>
                </div>
                <div>
                  <dt>摘要</dt>
                  <dd>{readString(selectedNode.data.summary) ?? "—"}</dd>
                </div>
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

            <div className="management-subcard workspace-node-detail-section">
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

            <div className="management-subcard workspace-node-detail-section">
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

            <div className="management-subcard workspace-node-detail-section">
              <div className="management-list-card-header">
                <strong className="management-list-title">其他字段</strong>
              </div>
              <NodeFieldList node={selectedNode} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
