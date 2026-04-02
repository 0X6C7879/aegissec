import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  advanceWorkflow,
  cancelGeneration,
  cancelSession,
  createSession,
  deleteSession,
  editSessionMessage,
  getCausalGraph,
  getCausalGraphForRun,
  getEvidenceGraph,
  getEvidenceGraphForRun,
  getRuntimeStatus,
  getSessionConversation,
  getSessionQueue,
  getTaskGraph,
  getTaskGraphForRun,
  getWorkflow,
  getWorkflowExport,
  getWorkflowReplay,
  forkSessionMessage,
  listSessions,
  listWorkflowTemplates,
  regenerateSessionMessage,
  rollbackSessionMessage,
  startWorkflow,
  updateSession,
  sendChatMessage,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { mergeSessionMessages, sortSessions, upsertSession } from "../lib/sessionUtils";
import { useUiStore } from "../store/uiStore";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import type { SessionConversation, SessionSummary } from "../types/sessions";
import type {
  WorkflowRunDetail,
  WorkflowRunExport,
  WorkflowRunReplay,
  WorkflowTaskNode,
  WorkflowTemplate,
} from "../types/workflows";
import { ConversationFeed } from "./ConversationFeed";
import { ConversationSidebar } from "./ConversationSidebar";
import { WorkbenchComposer } from "./WorkbenchComposer";

type WorkspaceDrawerTab = "outline" | "task" | "evidence";
type EvidenceMode = "evidence" | "causal";
type GraphFilterState = {
  search: string;
  status: string;
  nodeType: string;
};
type SelectedNode = {
  graphType: "task" | "evidence" | "causal";
  nodeId: string;
};
type TimelineItem = {
  id: string;
  label: string;
  value: string;
};

const EMPTY_SESSION_EVENTS: ReturnType<typeof useUiStore.getState>["eventsBySession"][string] = [];
const WORKSPACE_SIDEBAR_STORAGE_KEY = "aegissec.workspace.sidebar.collapsed.v1";

function getStoredWorkspaceSidebarState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY) === "true";
}

function getSessionDisplayTitle(title: string): string {
  return title === "New Session" ? "新对话" : title;
}

function visibleSessionsForSidebar(
  sessions: SessionSummary[],
  activeSessionId: string | null,
): SessionSummary[] {
  return sessions.filter((session) => !session.deleted_at || session.id === activeSessionId);
}

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

function formatWorkflowStatus(status: string | null): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "运行中";
    case "needs_approval":
      return "待审批";
    case "paused":
      return "已暂停";
    case "done":
      return "已完成";
    case "error":
      return "异常";
    case "blocked":
      return "已阻塞";
    default:
      return status ?? "未开始";
  }
}

function formatTaskStatusLabel(status: string | null): string {
  switch (status) {
    case "completed":
      return "已完成";
    case "in_progress":
      return "进行中";
    case "blocked":
      return "待审批";
    case "failed":
      return "异常";
    case "ready":
      return "就绪";
    case "pending":
      return "待执行";
    case "skipped":
      return "已跳过";
    default:
      return status ?? "未知";
  }
}

function getTaskStatusTone(status: string | null): string {
  switch (status) {
    case "completed":
      return "tone-success";
    case "in_progress":
      return "tone-connected";
    case "blocked":
      return "tone-warning";
    case "failed":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

function formatStageFallback(stage: string | null): string {
  if (!stage) {
    return "未开始";
  }

  return stage.replace(/_/g, " · ");
}

function formatConfidence(value: number | null): string | null {
  if (value === null) {
    return null;
  }

  if (value >= 0 && value <= 1) {
    return `${Math.round(value * 100)}%`;
  }

  return value.toFixed(2);
}

function getConfidenceTone(value: number | null): string {
  if (value === null) {
    return "tone-neutral";
  }

  if (value >= 0.75) {
    return "tone-success";
  }

  if (value >= 0.4) {
    return "tone-warning";
  }

  return "tone-error";
}

function getConnectionTone(state: string): string {
  return state === "open" ? "在线" : state === "connecting" ? "连接中" : "离线";
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
    default:
      return relation;
  }
}

function buildOptimisticUserMessage(sessionId: string, content: string) {
  return {
    id: `optimistic-user-${crypto.randomUUID()}`,
    session_id: sessionId,
    role: "user" as const,
    content,
    attachments: [],
    created_at: new Date().toISOString(),
  };
}

function downloadJson(fileName: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function getCurrentTaskNode(graph: SessionGraph | undefined): SessionGraphNode | null {
  return graph?.nodes.find((node) => readBoolean(node.data.current)) ?? null;
}

function getNodeStatus(node: SessionGraphNode): string | null {
  return readString(node.data.status);
}

function buildTaskChildren(tasks: WorkflowTaskNode[]): Map<string | null, WorkflowTaskNode[]> {
  const taskChildren = new Map<string | null, WorkflowTaskNode[]>();

  for (const task of tasks) {
    const current = taskChildren.get(task.parent_id) ?? [];
    current.push(task);
    taskChildren.set(task.parent_id, current);
  }

  for (const group of taskChildren.values()) {
    group.sort(
      (left, right) =>
        left.sequence - right.sequence || left.created_at.localeCompare(right.created_at),
    );
  }

  return taskChildren;
}

function flattenTaskTree(
  tasks: WorkflowTaskNode[],
): Array<{ task: WorkflowTaskNode; depth: number }> {
  const taskChildren = buildTaskChildren(tasks);
  const flattened: Array<{ task: WorkflowTaskNode; depth: number }> = [];

  function visit(parentId: string | null, depth: number): void {
    const children = taskChildren.get(parentId) ?? [];
    for (const child of children) {
      flattened.push({ task: child, depth });
      visit(child.id, depth + 1);
    }
  }

  visit(null, 0);
  return flattened;
}

function buildTimelineItems(
  selectedNode: SessionGraphNode | null,
  workflowTask: WorkflowTaskNode | null,
  replay: WorkflowRunReplay | undefined,
): TimelineItem[] {
  if (!selectedNode) {
    return [];
  }

  const items: TimelineItem[] = [];
  const createdAt =
    readString(workflowTask?.created_at) ?? readString(selectedNode.data.created_at);
  const startedAt = readString(selectedNode.data.started_at);
  const endedAt = readString(selectedNode.data.ended_at);

  if (createdAt) {
    items.push({
      id: `${selectedNode.id}-created`,
      label: "创建",
      value: formatDateTime(createdAt),
    });
  }
  if (startedAt) {
    items.push({
      id: `${selectedNode.id}-started`,
      label: "开始",
      value: formatDateTime(startedAt),
    });
  }
  if (endedAt) {
    items.push({ id: `${selectedNode.id}-ended`, label: "结束", value: formatDateTime(endedAt) });
  }

  const replayStep = replay?.replay_steps.find((step) => step.task_node_id === selectedNode.id);
  if (replayStep?.started_at) {
    items.push({
      id: `${selectedNode.id}-replay-start`,
      label: "回放开始",
      value: formatDateTime(replayStep.started_at),
    });
  }
  if (replayStep?.ended_at) {
    items.push({
      id: `${selectedNode.id}-replay-end`,
      label: "回放结束",
      value: formatDateTime(replayStep.ended_at),
    });
  }

  return items;
}

function getSelectedGraphNode(
  selectedNode: SelectedNode | null,
  taskGraph: SessionGraph | undefined,
  evidenceGraph: SessionGraph | undefined,
  causalGraph: SessionGraph | undefined,
): SessionGraphNode | null {
  if (!selectedNode) {
    return null;
  }

  const graph =
    selectedNode.graphType === "task"
      ? taskGraph
      : selectedNode.graphType === "evidence"
        ? evidenceGraph
        : causalGraph;

  return graph?.nodes.find((node) => node.id === selectedNode.nodeId) ?? null;
}

function getSelectedGraphEdges(
  selectedNode: SelectedNode | null,
  taskGraph: SessionGraph | undefined,
  evidenceGraph: SessionGraph | undefined,
  causalGraph: SessionGraph | undefined,
): SessionGraphEdge[] {
  if (!selectedNode) {
    return [];
  }

  const graph =
    selectedNode.graphType === "task"
      ? taskGraph
      : selectedNode.graphType === "evidence"
        ? evidenceGraph
        : causalGraph;

  return (graph?.edges ?? []).filter(
    (edge) => edge.source === selectedNode.nodeId || edge.target === selectedNode.nodeId,
  );
}

function NodeFieldList({ node }: { node: SessionGraphNode }) {
  const entries = Object.entries(node.data).filter(([, value]) => {
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

function WorkspaceSidebarSections({
  tasks,
  onSelectTask,
}: {
  tasks: WorkflowTaskNode[];
  onSelectTask: (taskId: string) => void;
}) {
  const flattenedTasks = useMemo(() => flattenTaskTree(tasks), [tasks]);

  return (
    <section className="conversation-sidebar-section conversation-sidebar-section-stacked">
      <div className="conversation-sidebar-section-header">
        <h3>任务树</h3>
        <span className="management-status-badge tone-neutral">{tasks.length}</span>
      </div>

      {flattenedTasks.length === 0 ? (
        <div className="conversation-empty-list conversation-empty-list-panel">
          <p>工作流启动后，这里会出现任务树。</p>
        </div>
      ) : (
        <ul className="workspace-task-tree-list">
          {flattenedTasks.map(({ task, depth }) => (
            <li key={task.id}>
              <button
                type="button"
                className={`workspace-task-tree-item workspace-task-tree-item-depth-${Math.min(depth, 4)}`}
                onClick={() => onSelectTask(task.id)}
              >
                <span
                  className={`workspace-task-tree-dot ${getTaskStatusTone(task.status)}`}
                  aria-hidden="true"
                />
                <span className="workspace-task-tree-copy">
                  <span className="workspace-task-tree-title">
                    {task.metadata.title ?? task.name}
                  </span>
                  <span className="workspace-task-tree-meta">
                    {formatTaskStatusLabel(task.status)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WorkflowPlanCard({
  templates,
  selectedTemplateName,
  workflow,
  canAdvanceWorkflow,
  workflowNeedsApproval,
  isStarting,
  isAdvancing,
  onSelectTemplate,
  onStartWorkflow,
  onAdvanceWorkflow,
  onOpenReplay,
  onExport,
}: {
  templates: WorkflowTemplate[] | undefined;
  selectedTemplateName: string;
  workflow: WorkflowRunDetail | undefined;
  canAdvanceWorkflow: boolean;
  workflowNeedsApproval: boolean;
  isStarting: boolean;
  isAdvancing: boolean;
  onSelectTemplate: (templateName: string) => void;
  onStartWorkflow: () => Promise<void>;
  onAdvanceWorkflow: () => Promise<void>;
  onOpenReplay: () => void;
  onExport: () => void;
}) {
  const selectedTemplate =
    templates?.find((template) => template.name === selectedTemplateName) ?? null;
  const planSummary = readString(workflow?.state.plan?.summary);
  const stageOrder = readStringArray(workflow?.state.plan?.stage_order);
  const activeTemplate = workflow
    ? (templates?.find((template) => template.name === workflow.template_name) ?? null)
    : selectedTemplate;

  return (
    <section className="management-section-card workspace-plan-card">
      <div className="management-section-header">
        <h3 className="management-section-title">计划与推进</h3>
        {workflow ? (
          <span className="management-status-badge tone-neutral">
            {formatWorkflowStatus(workflow.status)}
          </span>
        ) : null}
      </div>

      <div className="workspace-inline-field-grid">
        <label className="field-label workspace-inline-field">
          工作流模板
          <select
            className="field-input"
            value={selectedTemplateName}
            onChange={(event) => onSelectTemplate(event.target.value)}
          >
            {(templates ?? []).map((template) => (
              <option key={template.name} value={template.name}>
                {template.title}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="session-graph-token-row">
        {(activeTemplate?.stages ?? []).map((stage) => (
          <span key={stage.key} className="management-token-chip">
            {stage.title}
          </span>
        ))}
      </div>

      {planSummary ? <p className="session-graph-body-copy">{planSummary}</p> : null}
      {stageOrder.length > 0 ? (
        <p className="management-unified-description">
          顺序：{stageOrder.map((stage) => stage.replace(/_/g, " · ")).join(" → ")}
        </p>
      ) : null}

      <div className="management-action-row">
        <button
          className="button button-primary"
          type="button"
          disabled={isStarting}
          onClick={() => void onStartWorkflow()}
        >
          {isStarting ? "启动中" : workflow ? "重新启动" : "启动工作流"}
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={!canAdvanceWorkflow || workflowNeedsApproval || isAdvancing}
          onClick={() => void onAdvanceWorkflow()}
        >
          {isAdvancing ? "推进中" : "推进下一步"}
        </button>
        <button className="text-button" type="button" onClick={onOpenReplay}>
          回放
        </button>
        <button className="text-button" type="button" onClick={onExport}>
          导出
        </button>
      </div>
    </section>
  );
}

function ApprovalCard({
  workflow,
  policyDraft,
  isSavingPolicy,
  onChangePolicy,
  onSavePolicy,
  onApprove,
  onReject,
}: {
  workflow: WorkflowRunDetail;
  policyDraft: Record<string, unknown>;
  isSavingPolicy: boolean;
  onChangePolicy: (key: string, value: unknown) => void;
  onSavePolicy: () => Promise<void>;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}) {
  const pendingTaskId = readString(workflow.state.approval?.pending_task_id);
  const pendingTask = workflow.tasks.find((task) => task.id === pendingTaskId) ?? null;
  const allowNetwork = readBoolean(policyDraft.allow_network);
  const allowWrite = readBoolean(policyDraft.allow_write);
  const maxExecutionSeconds = readNumber(policyDraft.max_execution_seconds) ?? 120;

  return (
    <section className="management-section-card workspace-approval-card">
      <div className="management-section-header">
        <h3 className="management-section-title">审批卡</h3>
        <span className="management-status-badge tone-warning">等待确认</span>
      </div>

      <p className="session-graph-body-copy">
        当前工作流在 <strong>{workflow.current_stage ?? "未知阶段"}</strong> 暂停。
        {pendingTask ? ` 待确认节点：${pendingTask.metadata.title ?? pendingTask.name}。` : ""}
      </p>

      <div className="workspace-inline-field-grid workspace-inline-field-grid-tight">
        <label className="settings-inline-toggle">
          <input
            type="checkbox"
            checked={allowNetwork}
            onChange={(event) => onChangePolicy("allow_network", event.target.checked)}
          />
          允许联网
        </label>
        <label className="settings-inline-toggle">
          <input
            type="checkbox"
            checked={allowWrite}
            onChange={(event) => onChangePolicy("allow_write", event.target.checked)}
          />
          允许写文件
        </label>
        <label className="field-label workspace-inline-field">
          最大执行秒数
          <input
            className="field-input"
            type="number"
            min={30}
            step={30}
            value={String(maxExecutionSeconds)}
            onChange={(event) =>
              onChangePolicy("max_execution_seconds", Number(event.target.value))
            }
          />
        </label>
      </div>

      <div className="management-action-row">
        <button className="button button-primary" type="button" onClick={() => void onApprove()}>
          审批并继续
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={isSavingPolicy}
          onClick={() => void onSavePolicy()}
        >
          {isSavingPolicy ? "保存中" : "保存策略"}
        </button>
        <button className="button button-danger" type="button" onClick={() => void onReject()}>
          拒绝并暂停
        </button>
      </div>
    </section>
  );
}

function GraphCanvasWrapper({
  title,
  graph,
  filters,
  selectedNodeId,
  onFilterChange,
  onSelectNode,
}: {
  title: string;
  graph: SessionGraph | undefined;
  filters: GraphFilterState;
  selectedNodeId: string | null;
  onFilterChange: (next: GraphFilterState) => void;
  onSelectNode: (nodeId: string) => void;
}) {
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

  const nodeTypeOptions = useMemo(() => {
    return [...new Set((graph?.nodes ?? []).map((node) => node.node_type))];
  }, [graph]);

  const visibleNodes = useMemo(() => {
    const searchKeyword = filters.search.trim().toLowerCase();

    return (graph?.nodes ?? []).filter((node) => {
      if (filters.status !== "all") {
        const status = getNodeStatus(node);
        if (status !== filters.status) {
          return false;
        }
      }

      if (filters.nodeType !== "all" && node.node_type !== filters.nodeType) {
        return false;
      }

      if (searchKeyword.length === 0) {
        return true;
      }

      const haystack = [node.label, ...Object.values(node.data).map((value) => String(value))]
        .join(" ")
        .toLowerCase();
      return haystack.includes(searchKeyword);
    });
  }, [filters.nodeType, filters.search, filters.status, graph]);

  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () =>
      (graph?.edges ?? []).filter(
        (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
      ),
    [graph?.edges, visibleNodeIds],
  );

  if (!graph) {
    return (
      <section className="management-section-card">
        <div className="management-section-header">
          <h3 className="management-section-title">{title}</h3>
        </div>
        <div className="management-empty-state session-graph-inline-empty">
          <p className="management-empty-title">暂无图谱数据</p>
          <p className="management-empty-copy">工作流开始执行后，这里会展示图谱视图。</p>
        </div>
      </section>
    );
  }

  return (
    <section className="management-section-card workspace-graph-canvas-panel">
      <div className="management-section-header">
        <h3 className="management-section-title">{title}</h3>
        <span className="management-status-badge tone-neutral">
          {visibleNodes.length} / {graph.nodes.length} 节点
        </span>
      </div>

      <div className="workspace-graph-toolbar">
        <input
          className="management-search-input"
          type="search"
          value={filters.search}
          onChange={(event) => onFilterChange({ ...filters, search: event.target.value })}
          placeholder="搜索节点"
        />
        <select
          className="field-input workspace-graph-select"
          value={filters.status}
          onChange={(event) => onFilterChange({ ...filters, status: event.target.value })}
        >
          <option value="all">全部状态</option>
          {statusOptions.map((status) => (
            <option key={status} value={status}>
              {formatTaskStatusLabel(status)}
            </option>
          ))}
        </select>
        <select
          className="field-input workspace-graph-select"
          value={filters.nodeType}
          onChange={(event) => onFilterChange({ ...filters, nodeType: event.target.value })}
        >
          <option value="all">全部类型</option>
          {nodeTypeOptions.map((nodeType) => (
            <option key={nodeType} value={nodeType}>
              {nodeType}
            </option>
          ))}
        </select>
      </div>

      <div className="workspace-graph-canvas">
        {visibleNodes.length === 0 ? (
          <div className="management-empty-state session-graph-inline-empty">
            <p className="management-empty-title">筛选后没有节点</p>
            <p className="management-empty-copy">调整状态、类型或搜索条件后重试。</p>
          </div>
        ) : (
          <div className="workspace-graph-card-grid">
            {visibleNodes.map((node) => {
              const confidence =
                readNumber(node.data.confidence) ?? readNumber(node.data.evidence_confidence);
              return (
                <button
                  key={node.id}
                  type="button"
                  className={`management-subcard workspace-graph-node-card${selectedNodeId === node.id ? " workspace-graph-node-card-active" : ""}`}
                  onClick={() => onSelectNode(node.id)}
                >
                  <div className="management-list-card-header">
                    <strong className="management-list-title">{node.label}</strong>
                    <span
                      className={`management-status-badge ${getTaskStatusTone(getNodeStatus(node))}`}
                    >
                      {formatTaskStatusLabel(getNodeStatus(node))}
                    </span>
                  </div>
                  <div className="session-graph-token-row">
                    <span className="management-token-chip">{node.node_type}</span>
                    {confidence !== null ? (
                      <span className={`management-status-badge ${getConfidenceTone(confidence)}`}>
                        {`置信度 ${formatConfidence(confidence)}`}
                      </span>
                    ) : null}
                  </div>
                  {readString(node.data.summary) ? (
                    <p className="session-graph-body-copy">{readString(node.data.summary)}</p>
                  ) : null}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="workspace-graph-edge-list">
        <div className="management-section-header workspace-graph-edge-header">
          <h4 className="management-section-title">关系</h4>
          <span className="management-status-badge tone-neutral">{visibleEdges.length}</span>
        </div>
        {visibleEdges.length === 0 ? (
          <p className="management-empty-copy">当前筛选范围内没有关系连线。</p>
        ) : (
          <ul className="management-list">
            {visibleEdges.slice(0, 8).map((edge) => (
              <li key={edge.id} className="management-subcard workspace-graph-edge-card">
                <strong className="management-list-title">
                  {edge.source} → {edge.target}
                </strong>
                <span className="management-status-badge tone-neutral">
                  {getRelationLabel(edge.relation)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function ReplayExportPanel({
  session,
  exportData,
  replayData,
}: {
  session: SessionSummary;
  exportData: WorkflowRunExport | undefined;
  replayData: WorkflowRunReplay | undefined;
}) {
  const replayStepCount = replayData?.replay_steps.length ?? 0;
  const executionRecordCount = exportData?.execution_records.length ?? 0;
  const taskNodeCount = exportData?.task_graph.nodes.length ?? 0;
  const evidenceNodeCount = exportData?.evidence_graph.nodes.length ?? 0;

  return (
    <section className="management-section-card workspace-export-panel">
      <div className="management-section-header">
        <h3 className="management-section-title">回放 / 导出</h3>
        <span className="management-status-badge tone-neutral">{session.id}</span>
      </div>

      <div className="management-action-row">
        <button
          className="button button-secondary"
          type="button"
          disabled={!replayData}
          onClick={() => {
            if (!replayData) {
              return;
            }
            downloadJson(`session-${session.id}-replay.json`, replayData);
          }}
        >
          导出回放 JSON
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={!exportData}
          onClick={() => {
            if (!exportData) {
              return;
            }
            downloadJson(`session-${session.id}-export.json`, exportData);
          }}
        >
          导出快照 JSON
        </button>
      </div>

      <p className="management-unified-description">
        回放 {replayStepCount} 步，执行记录 {executionRecordCount} 条，图谱共{" "}
        {taskNodeCount + evidenceNodeCount} 个节点。
      </p>

      {replayData?.replay_steps.length ? (
        <ul className="management-list">
          {replayData.replay_steps.slice(0, 6).map((step) => (
            <li key={`${step.trace_id}-${step.index}`} className="management-subcard">
              <div className="management-list-card-header">
                <strong className="management-list-title">{step.task_name}</strong>
                <span className={`management-status-badge ${getTaskStatusTone(step.status)}`}>
                  {formatTaskStatusLabel(step.status)}
                </span>
              </div>
              {step.summary ? <p className="session-graph-body-copy">{step.summary}</p> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="management-empty-copy">当前 run 还没有可导出的回放步骤。</p>
      )}
    </section>
  );
}

function NodeDetailPanel({
  selectedNode,
  relatedEdges,
  workflowTask,
  timeline,
}: {
  selectedNode: SessionGraphNode | null;
  relatedEdges: SessionGraphEdge[];
  workflowTask: WorkflowTaskNode | null;
  timeline: TimelineItem[];
}) {
  if (!selectedNode) {
    return (
      <section className="management-section-card">
        <div className="management-section-header">
          <h3 className="management-section-title">节点详情</h3>
        </div>
        <div className="management-empty-state session-graph-inline-empty">
          <p className="management-empty-title">尚未选择节点</p>
          <p className="management-empty-copy">
            从任务图、证据图或任务树点击任意节点后，这里会显示时间线与关系详情。
          </p>
        </div>
      </section>
    );
  }

  const confidence =
    readNumber(selectedNode.data.confidence) ?? readNumber(selectedNode.data.evidence_confidence);

  return (
    <section className="management-section-card workspace-node-detail-panel">
      <div className="management-section-header">
        <h3 className="management-section-title">节点详情</h3>
        <span className="management-status-badge tone-neutral">{selectedNode.graph_type}</span>
      </div>

      <div className="management-subcard">
        <div className="management-list-card-header">
          <strong className="management-list-title">{selectedNode.label}</strong>
          {confidence !== null ? (
            <span className={`management-status-badge ${getConfidenceTone(confidence)}`}>
              {`置信度 ${formatConfidence(confidence)}`}
            </span>
          ) : null}
        </div>
        <div className="session-graph-token-row">
          <span className="management-token-chip">{selectedNode.node_type}</span>
          {workflowTask?.metadata.role ? (
            <span className="management-token-chip">{workflowTask.metadata.role}</span>
          ) : null}
          {workflowTask?.metadata.approval_required ? (
            <span className="management-token-chip">需审批</span>
          ) : null}
        </div>
        {readString(selectedNode.data.summary) ? (
          <p className="session-graph-body-copy">{readString(selectedNode.data.summary)}</p>
        ) : null}
      </div>

      <div className="management-subcard workspace-node-detail-section">
        <div className="management-list-card-header">
          <strong className="management-list-title">节点时间线</strong>
          <span className="management-status-badge tone-neutral">{timeline.length}</span>
        </div>
        {timeline.length === 0 ? (
          <p className="management-empty-copy">当前节点没有时间线字段。</p>
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
          <strong className="management-list-title">关联关系</strong>
          <span className="management-status-badge tone-neutral">{relatedEdges.length}</span>
        </div>
        {relatedEdges.length === 0 ? (
          <p className="management-empty-copy">这个节点当前没有可展示的关联边。</p>
        ) : (
          <ul className="management-list">
            {relatedEdges.map((edge) => (
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
          <strong className="management-list-title">字段</strong>
        </div>
        <NodeFieldList node={selectedNode} />
      </div>
    </section>
  );
}

export function SessionWorkspaceWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() =>
    getStoredWorkspaceSidebarState(),
  );
  const [selectedTemplateName, setSelectedTemplateName] = useState("");
  const [pinnedWorkflowRunId, setPinnedWorkflowRunId] = useState<string | null>(null);
  const [selectedDrawerTab, setSelectedDrawerTab] = useState<WorkspaceDrawerTab>("outline");
  const [isInsightsOpen, setIsInsightsOpen] = useState(false);
  const [isPlanDialogOpen, setIsPlanDialogOpen] = useState(false);
  const [isReplayPanelOpen, setIsReplayPanelOpen] = useState(false);
  const [evidenceMode, setEvidenceMode] = useState<EvidenceMode>("evidence");
  const [taskFilters, setTaskFilters] = useState<GraphFilterState>({
    search: "",
    status: "all",
    nodeType: "all",
  });
  const [evidenceFilters, setEvidenceFilters] = useState<GraphFilterState>({
    search: "",
    status: "all",
    nodeType: "all",
  });
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(null);
  const [policyDraft, setPolicyDraft] = useState<Record<string, unknown>>({});
  const [messageActionBusyId, setMessageActionBusyId] = useState<string | null>(null);

  const lastVisitedSessionId = useUiStore((state) => state.lastVisitedSessionId);
  const setLastVisitedSessionId = useUiStore((state) => state.setLastVisitedSessionId);
  const appendEvent = useUiStore((state) => state.appendEvent);
  const sessionEvents = useUiStore((state) =>
    sessionId ? (state.eventsBySession[sessionId] ?? EMPTY_SESSION_EVENTS) : EMPTY_SESSION_EVENTS,
  );

  useEffect(() => {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", "workspace"],
    queryFn: ({ signal }) => listSessions(true, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const templatesQuery = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: ({ signal }) => listWorkflowTemplates(signal),
  });

  const runtimeStatusQuery = useQuery({
    queryKey: ["runtime-status"],
    queryFn: ({ signal }) => getRuntimeStatus(signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: 15000,
  });

  const sortedSessions = useMemo(
    () => sortSessions(sessionsQuery.data ?? []),
    [sessionsQuery.data],
  );
  const activeSessionId = useMemo(() => {
    if (sessionId) {
      return sessionId;
    }

    if (
      lastVisitedSessionId &&
      sortedSessions.some((session) => session.id === lastVisitedSessionId)
    ) {
      return lastVisitedSessionId;
    }

    return (
      sortedSessions.find((session) => !session.deleted_at)?.id ?? sortedSessions[0]?.id ?? null
    );
  }, [lastVisitedSessionId, sessionId, sortedSessions]);

  const activeSession = useMemo(
    () => sortedSessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sortedSessions],
  );

  const sidebarSessions = useMemo(
    () => visibleSessionsForSidebar(sortedSessions, activeSessionId),
    [activeSessionId, sortedSessions],
  );

  useEffect(() => {
    if (!sessionId && activeSessionId) {
      navigate(`/sessions/${activeSessionId}/chat`, { replace: true });
    }
  }, [activeSessionId, navigate, sessionId]);

  useEffect(() => {
    if (activeSessionId) {
      setLastVisitedSessionId(activeSessionId);
    }
  }, [activeSessionId, setLastVisitedSessionId]);

  const shouldLoadEvidenceGraph = isInsightsOpen && selectedDrawerTab === "evidence";
  const shouldLoadReplayArtifacts = isReplayPanelOpen;

  const conversationQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["conversation", activeSessionId],
    queryFn: ({ signal }) => getSessionConversation(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const sessionQueueQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session-queue", activeSessionId],
    queryFn: ({ signal }) => getSessionQueue(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: (query) => {
      const value = query.state.data;
      if (!value) {
        return false;
      }
      return value.active_generation || value.queued_generations.length > 0 ? 1500 : false;
    },
  });

  const sessionTaskGraphQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session", activeSessionId, "graph", "task"],
    queryFn: ({ signal }) => getTaskGraph(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const sessionEvidenceGraphQuery = useQuery({
    enabled: Boolean(activeSessionId) && shouldLoadEvidenceGraph && evidenceMode === "evidence",
    queryKey: ["session", activeSessionId, "graph", "evidence"],
    queryFn: ({ signal }) => getEvidenceGraph(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const sessionCausalGraphQuery = useQuery({
    enabled: Boolean(activeSessionId) && shouldLoadEvidenceGraph && evidenceMode === "causal",
    queryKey: ["session", activeSessionId, "graph", "causal"],
    queryFn: ({ signal }) => getCausalGraph(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const inferredWorkflowRunId = sessionTaskGraphQuery.data?.workflow_run_id ?? null;

  useEffect(() => {
    if (inferredWorkflowRunId) {
      setPinnedWorkflowRunId(inferredWorkflowRunId);
    }
  }, [inferredWorkflowRunId]);

  const workflowRunId = pinnedWorkflowRunId ?? inferredWorkflowRunId;

  useEffect(() => {
    if (workflowRunId) {
      return;
    }

    setIsInsightsOpen(false);
    setIsPlanDialogOpen(false);
    setIsReplayPanelOpen(false);
    setSelectedDrawerTab("outline");
    setSelectedNode(null);
  }, [workflowRunId]);

  useEffect(() => {
    if (!isPlanDialogOpen && !isInsightsOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Escape") {
        return;
      }

      if (isPlanDialogOpen) {
        setIsPlanDialogOpen(false);
      } else if (isInsightsOpen) {
        setIsInsightsOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isInsightsOpen, isPlanDialogOpen]);

  const workflowQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId],
    queryFn: ({ signal }) => getWorkflow(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runTaskGraphQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "graph", "task"],
    queryFn: ({ signal }) => getTaskGraphForRun(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runEvidenceGraphQuery = useQuery({
    enabled: Boolean(workflowRunId) && shouldLoadEvidenceGraph && evidenceMode === "evidence",
    queryKey: ["workflow", workflowRunId, "graph", "evidence"],
    queryFn: ({ signal }) => getEvidenceGraphForRun(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runCausalGraphQuery = useQuery({
    enabled: Boolean(workflowRunId) && shouldLoadEvidenceGraph && evidenceMode === "causal",
    queryKey: ["workflow", workflowRunId, "graph", "causal"],
    queryFn: ({ signal }) => getCausalGraphForRun(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const workflowExportQuery = useQuery({
    enabled: Boolean(workflowRunId) && shouldLoadReplayArtifacts,
    queryKey: ["workflow", workflowRunId, "export"],
    queryFn: ({ signal }) => getWorkflowExport(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const workflowReplayQuery = useQuery({
    enabled: Boolean(workflowRunId) && shouldLoadReplayArtifacts,
    queryKey: ["workflow", workflowRunId, "replay"],
    queryFn: ({ signal }) => getWorkflowReplay(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const connectionState = useSessionEvents(activeSessionId);
  const sessionRuns = useMemo(
    () =>
      (runtimeStatusQuery.data?.recent_runs ?? []).filter(
        (run) => run.session_id === activeSessionId,
      ),
    [activeSessionId, runtimeStatusQuery.data?.recent_runs],
  );

  useEffect(() => {
    if (!activeSession) {
      setPolicyDraft({});
      return;
    }

    setPolicyDraft(activeSession.runtime_policy_json ?? {});
  }, [activeSession]);

  useEffect(() => {
    const templateNames = new Set((templatesQuery.data ?? []).map((template) => template.name));
    const activeTemplateName = workflowQuery.data?.template_name ?? null;

    if (!selectedTemplateName) {
      if (activeTemplateName && templateNames.has(activeTemplateName)) {
        setSelectedTemplateName(activeTemplateName);
        return;
      }

      const firstTemplateName = templatesQuery.data?.[0]?.name;
      if (firstTemplateName) {
        setSelectedTemplateName(firstTemplateName);
      }
      return;
    }

    if (templateNames.size > 0 && !templateNames.has(selectedTemplateName)) {
      setSelectedTemplateName(templatesQuery.data?.[0]?.name ?? "");
    }
  }, [selectedTemplateName, templatesQuery.data, workflowQuery.data?.template_name]);

  const createSessionMutation = useMutation({
    mutationFn: () => createSession(),
    onSuccess: async (createdSession) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setLastVisitedSessionId(createdSession.id);
      navigate(`/sessions/${createdSession.id}/chat`);
    },
  });

  const renameSessionMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateSession(id, { title }),
    onSuccess: (updatedSession) => {
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", updatedSession.id],
        (currentValue) => (currentValue ? { ...currentValue, session: updatedSession } : currentValue),
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: async (_value, deletedId) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (sessionId === deletedId) {
        navigate("/sessions");
      }
    },
  });

  const restoreSessionMutation = useMutation({
    mutationFn: (id: string) => updateSession(id, {}),
    onSuccess: async (_value, restoredId) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      navigate(`/sessions/${restoredId}/chat`);
    },
    onError: async (_error, restoredId) => {
      await restoreSessionMutation.reset();
      await queryClient.invalidateQueries({ queryKey: ["conversation", restoredId] });
    },
  });

  const restoreArchivedSessionMutation = useMutation({
    mutationFn: async (id: string) => {
      const { restoreSession } = await import("../lib/api");
      return restoreSession(id);
    },
    onSuccess: (restoredSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, restoredSession),
      );
      navigate(`/sessions/${restoredSession.id}/chat`);
    },
  });

  const updatePolicyMutation = useMutation({
    mutationFn: ({
      id,
      runtime_policy_json,
    }: {
      id: string;
      runtime_policy_json: Record<string, unknown>;
    }) => updateSession(id, { runtime_policy_json }),
    onSuccess: (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", updatedSession.id],
        (currentValue) => (currentValue ? { ...currentValue, session: updatedSession } : currentValue),
      );
    },
  });

  const pauseSessionMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => updateSession(id, { status: "paused" }),
    onSuccess: (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const cancelSessionMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => cancelSession(id),
    onSuccess: (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", updatedSession.id],
        (currentValue) => (currentValue ? { ...currentValue, session: updatedSession } : currentValue),
      );
      void queryClient.invalidateQueries({ queryKey: ["session-queue", updatedSession.id] });
    },
    onError: (error, variables) => {
      appendEvent(variables.id, {
        id: crypto.randomUUID(),
        sessionId: variables.id,
        type: "assistant.trace",
        createdAt: new Date().toISOString(),
        summary: "停止当前回复失败。",
        payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
      });
    },
  });

  const sendChatMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      sendChatMessage(id, {
        content,
        attachments: [],
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onMutate: async ({ id, content }) => {
      await queryClient.cancelQueries({ queryKey: ["conversation", id] });
      const previousDetail = queryClient.getQueryData<SessionConversation | undefined>([
        "conversation",
        id,
      ]);
      const optimisticMessage = buildOptimisticUserMessage(id, content);

      queryClient.setQueryData<SessionConversation | undefined>(["conversation", id], (currentValue) => {
        const targetDetail = currentValue ?? previousDetail;
        if (!targetDetail) {
          return targetDetail;
        }
        return {
          ...targetDetail,
          session: {
            ...targetDetail.session,
            status: "running",
            updated_at: optimisticMessage.created_at,
          },
          messages:
            mergeSessionMessages(
              { ...targetDetail.session, messages: targetDetail.messages },
              [optimisticMessage],
            )?.messages ?? targetDetail.messages,
        };
      });

      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) => {
        const currentSession = currentValue?.find((session) => session.id === id);
        if (!currentSession) {
          return currentValue;
        }

        return upsertSession(currentValue, {
          ...currentSession,
          status: "running",
          updated_at: optimisticMessage.created_at,
        });
      });

      void queryClient.invalidateQueries({ queryKey: ["session-queue", id] });

      return { previousDetail, optimisticMessageId: optimisticMessage.id };
    },
    onSuccess: async (response, _variables, context) => {
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", response.session.id],
        (currentValue) => {
          const baseMessages = (currentValue?.messages ?? []).filter(
            (message) => message.id !== context?.optimisticMessageId,
          );
          const nextMessages = [response.user_message, response.assistant_message];
          const updatedDetail = currentValue
            ? { ...currentValue, session: response.session, messages: baseMessages }
            : undefined;
          if (!updatedDetail) {
            return updatedDetail;
          }
          return {
            ...updatedDetail,
            active_branch: response.branch ?? updatedDetail.active_branch,
            generations: response.generation
              ? [
                  ...updatedDetail.generations.filter((generation) => generation.id !== response.generation?.id),
                  response.generation,
                ]
              : updatedDetail.generations,
            messages:
              mergeSessionMessages(
                { ...updatedDetail.session, messages: updatedDetail.messages },
                nextMessages,
              )?.messages ?? updatedDetail.messages,
          };
        },
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, response.session),
      );
      await queryClient.invalidateQueries({ queryKey: ["session-queue", response.session.id] });
      await queryClient.invalidateQueries({ queryKey: ["runtime-status"] });
    },
    onError: (error, variables, context) => {
      const isCancelledError =
        error instanceof Error && /cancelled|stopped current generation/i.test(error.message);
      const previousDetail = context?.previousDetail;
      const currentDetail = queryClient.getQueryData<SessionConversation | undefined>([
        "conversation",
        variables.id,
      ]);
      const hasPersistedUserMessage = (currentDetail?.messages ?? []).some(
        (message) =>
          message.role === "user" &&
          !message.id.startsWith("optimistic-user-") &&
          message.content.trim() === variables.content.trim(),
      );

      if (hasPersistedUserMessage && context?.optimisticMessageId) {
        queryClient.setQueryData<SessionConversation | undefined>(["conversation", variables.id], (detail) =>
          detail
            ? {
                ...detail,
                messages: detail.messages.filter(
                  (message) => message.id !== context.optimisticMessageId,
                ),
              }
            : detail,
        );
      } else if (previousDetail) {
        queryClient.setQueryData<SessionConversation | undefined>(
          ["conversation", variables.id],
          previousDetail,
        );
        queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
          upsertSession(currentValue, previousDetail.session),
        );
      }

      if (!isCancelledError) {
        appendEvent(variables.id, {
          id: crypto.randomUUID(),
          sessionId: variables.id,
          type: "assistant.trace",
          createdAt: new Date().toISOString(),
          summary: "模型请求失败。",
          payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
        });
      }

      void queryClient.invalidateQueries({ queryKey: ["conversation", variables.id] });
      void queryClient.invalidateQueries({ queryKey: ["session-queue", variables.id] });
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const cancelGenerationMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, generationId }: { sessionId: string; generationId: string }) =>
      cancelGeneration(targetSessionId, generationId),
    onSuccess: async (_generation, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const editMessageMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, messageId, content }: { sessionId: string; messageId: string; content: string }) =>
      editSessionMessage(targetSessionId, messageId, {
        content,
        attachments: [],
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onSuccess: async (_response, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const regenerateMessageMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, messageId }: { sessionId: string; messageId: string }) =>
      regenerateSessionMessage(targetSessionId, messageId, {
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onSuccess: async (_response, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const forkMessageMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, messageId, name }: { sessionId: string; messageId: string; name?: string | null }) =>
      forkSessionMessage(targetSessionId, messageId, { name: name ?? null }),
    onSuccess: async (response, variables) => {
      queryClient.setQueryData(["conversation", variables.sessionId], response);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const rollbackMessageMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, messageId }: { sessionId: string; messageId: string }) =>
      rollbackSessionMessage(targetSessionId, messageId, {
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onSuccess: async (response, variables) => {
      queryClient.setQueryData(["conversation", variables.sessionId], response);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const switchBranchMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, branchId }: { sessionId: string; branchId: string }) =>
      updateSession(targetSessionId, { active_branch_id: branchId }),
    onSuccess: async (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", updatedSession.id] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", updatedSession.id] }),
      ]);
    },
  });

  const invalidateWorkflowViews = useCallback(
    async (targetSessionId: string, targetRunId: string | null): Promise<void> => {
      const invalidations = [
        queryClient.invalidateQueries({ queryKey: ["conversation", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["session", targetSessionId, "graph", "task"] }),
      ];

      if (targetRunId) {
        invalidations.push(
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId] }),
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "graph", "task"] }),
        );

        if (shouldLoadEvidenceGraph) {
          invalidations.push(
            queryClient.invalidateQueries({
              queryKey: ["session", targetSessionId, "graph", "evidence"],
            }),
            queryClient.invalidateQueries({
              queryKey: ["session", targetSessionId, "graph", "causal"],
            }),
            queryClient.invalidateQueries({
              queryKey: ["workflow", targetRunId, "graph", "evidence"],
            }),
            queryClient.invalidateQueries({
              queryKey: ["workflow", targetRunId, "graph", "causal"],
            }),
          );
        }

        if (shouldLoadReplayArtifacts) {
          invalidations.push(
            queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "export"] }),
            queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "replay"] }),
          );
        }
      }

      await Promise.all(invalidations);
    },
    [queryClient, shouldLoadEvidenceGraph, shouldLoadReplayArtifacts],
  );

  const startWorkflowMutation = useMutation({
    mutationFn: ({
      activeSessionId: targetSessionId,
      templateName,
    }: {
      activeSessionId: string;
      templateName: string | null;
    }) => startWorkflow({ session_id: targetSessionId, template_name: templateName }),
    onSuccess: async (workflow) => {
      setPinnedWorkflowRunId(workflow.id);
      queryClient.setQueryData<WorkflowRunDetail>(["workflow", workflow.id], workflow);
      await invalidateWorkflowViews(workflow.session_id, workflow.id);
    },
  });

  const advanceWorkflowMutation = useMutation({
    mutationFn: ({ runId, approve }: { runId: string; approve?: boolean }) =>
      advanceWorkflow(runId, approve ? { approve: true } : {}),
    onSuccess: async (workflow) => {
      setPinnedWorkflowRunId(workflow.id);
      queryClient.setQueryData<WorkflowRunDetail>(["workflow", workflow.id], workflow);
      await invalidateWorkflowViews(workflow.session_id, workflow.id);
    },
  });

  const latestEvent = sessionEvents[sessionEvents.length - 1] ?? null;

  useEffect(() => {
    if (!activeSessionId || !latestEvent) {
      return;
    }

    const eventType = latestEvent.type;
    const shouldRefresh =
      eventType.startsWith("workflow.") ||
      eventType.startsWith("task.") ||
      eventType === "graph.updated";

    if (!shouldRefresh) {
      return;
    }

    const refreshTimer = window.setTimeout(() => {
      void invalidateWorkflowViews(activeSessionId, workflowRunId);
    }, 180);

    return () => {
      window.clearTimeout(refreshTimer);
    };
  }, [activeSessionId, invalidateWorkflowViews, latestEvent, workflowRunId]);

  const taskGraph = runTaskGraphQuery.data ?? sessionTaskGraphQuery.data;
  const evidenceGraph = runEvidenceGraphQuery.data ?? sessionEvidenceGraphQuery.data;
  const causalGraph = runCausalGraphQuery.data ?? sessionCausalGraphQuery.data;
  const activeConversation = conversationQuery.data ?? null;

  const workflowNeedsApproval =
    workflowQuery.data?.status === "needs_approval" ||
    workflowQuery.data?.state.approval?.required === true;
  const currentTaskNode = getCurrentTaskNode(taskGraph);
  const currentStageLabel =
    currentTaskNode?.label ??
    workflowQuery.data?.current_stage ??
    formatStageFallback(
      taskGraph?.current_stage ??
        evidenceGraph?.current_stage ??
        causalGraph?.current_stage ??
        activeSession?.current_phase ??
        null,
    );

  const selectedGraphNode = getSelectedGraphNode(
    selectedNode,
    taskGraph,
    evidenceGraph,
    causalGraph,
  );
  const selectedGraphEdges = getSelectedGraphEdges(
    selectedNode,
    taskGraph,
    evidenceGraph,
    causalGraph,
  );
  const selectedWorkflowTask = selectedNode
    ? (workflowQuery.data?.tasks.find((task) => task.id === selectedNode.nodeId) ?? null)
    : null;
  const selectedNodeTimeline = buildTimelineItems(
    selectedGraphNode,
    selectedWorkflowTask,
    workflowReplayQuery.data,
  );

  const canAdvanceWorkflow =
    Boolean(workflowRunId) &&
    workflowQuery.data?.status !== "done" &&
    workflowQuery.data?.status !== "error";
  const activeGeneration = sessionQueueQuery.data?.active_generation ?? null;
  const queuedGenerationCount = sessionQueueQuery.data?.queued_generations.length ?? 0;
  const isGenerationActive = activeGeneration !== null || activeSession?.status === "running";

  function handleToggleSidebarCollapsed(): void {
    setIsSidebarCollapsed((currentValue) => !currentValue);
  }

  function handleSelectSession(nextSessionId: string): void {
    navigate(`/sessions/${nextSessionId}/chat`);
  }

  async function handleRenameSession(targetSessionId: string): Promise<void> {
    const targetSession = sortedSessions.find((session) => session.id === targetSessionId);
    if (!targetSession) {
      return;
    }

    const nextTitle = window.prompt("修改对话标题", targetSession.title);
    if (nextTitle === null) {
      return;
    }

    const trimmed = nextTitle.trim();
    if (!trimmed || trimmed === targetSession.title) {
      return;
    }

    await renameSessionMutation.mutateAsync({ id: targetSessionId, title: trimmed });
  }

  function handleSelectNode(graphType: "task" | "evidence" | "causal", nodeId: string): void {
    setIsInsightsOpen(true);
    setSelectedDrawerTab(graphType === "task" ? "task" : "evidence");
    if (graphType !== "task") {
      setEvidenceMode(graphType === "causal" ? "causal" : "evidence");
    }
    setSelectedNode({ graphType, nodeId });
  }

  function handleChangePolicy(key: string, value: unknown): void {
    setPolicyDraft((currentValue) => ({ ...currentValue, [key]: value }));
  }

  if (sessionsQuery.isLoading && sessionsQuery.data === undefined && !activeSession) {
    return (
      <main className="conversation-workbench">
        <section className="conversation-main-shell">
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在加载 Workspace</p>
            <p className="conversation-empty-state-copy">稍后即可查看当前会话与聊天状态。</p>
          </section>
        </section>
      </main>
    );
  }

  return (
    <main
      className={`conversation-workbench${isSidebarCollapsed ? " conversation-workbench-sidebar-collapsed" : ""}`}
    >
      <ConversationSidebar
        sessions={sidebarSessions}
        activeSessionId={activeSessionId}
        collapsed={isSidebarCollapsed}
        isCreating={createSessionMutation.isPending}
        onCreate={async () => {
          await createSessionMutation.mutateAsync();
        }}
        onToggleCollapsed={handleToggleSidebarCollapsed}
        onSelect={handleSelectSession}
        onRename={handleRenameSession}
        onArchive={async (id) => {
          await deleteSessionMutation.mutateAsync(id);
        }}
        onRestore={async (id) => {
          await restoreArchivedSessionMutation.mutateAsync(id);
        }}
      />

      <section
        className={`conversation-main-shell workspace-session-shell${workflowRunId ? " workspace-session-shell-drawer-active" : ""}`}
      >
        {sessionsQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话列表暂不可用</p>
            <p className="conversation-empty-state-copy">{sessionsQuery.error.message}</p>
          </section>
        ) : !activeSession ? (
          <section className="conversation-empty-state workspace-empty-state-card">
            <p className="conversation-empty-state-title">还没有 Workspace</p>
            <p className="conversation-empty-state-copy">
              新建一个对话后，这里会进入聊天主视图，并按需展开执行进度。
            </p>
            <div className="management-action-row">
              <button
                className="button button-primary"
                type="button"
                onClick={() => void createSessionMutation.mutateAsync()}
              >
                新建对话
              </button>
            </div>
          </section>
        ) : activeSession && conversationQuery.isLoading && !activeConversation ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在打开对话</p>
            <p className="conversation-empty-state-copy">消息与工作流状态正在同步。</p>
          </section>
        ) : activeSession && activeConversation ? (
          <>
            <header className="conversation-header workspace-session-header">
              <div className="conversation-header-copy">
                <h2 className="conversation-title">
                  {getSessionDisplayTitle(activeSession.title)}
                </h2>
                <p className="management-unified-description">当前阶段：{currentStageLabel}</p>
                {activeConversation.branches.length > 0 ? (
                  <label className="field-label workspace-inline-field">
                    对话分支
                    <select
                      className="field-inline-input"
                      value={activeConversation.active_branch?.id ?? ""}
                      onChange={(event) => {
                        const branchId = event.target.value;
                        if (!branchId || branchId === activeConversation.active_branch?.id) {
                          return;
                        }
                        void switchBranchMutation.mutateAsync({
                          sessionId: activeSession.id,
                          branchId,
                        });
                      }}
                    >
                      {activeConversation.branches.map((branch) => (
                        <option key={branch.id} value={branch.id}>
                          {branch.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>

              <div className="conversation-header-actions workspace-session-header-actions">
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => setIsPlanDialogOpen(true)}
                >
                  计划与推进
                </button>
                <span className="management-status-badge tone-neutral">
                  {formatWorkflowStatus(workflowQuery.data?.status ?? activeSession.status)}
                </span>
                <span className={`connection-pill connection-${connectionState}`}>
                  {getConnectionTone(connectionState)}
                </span>
              </div>
            </header>

            {activeSession.deleted_at ? (
              <section className="conversation-inline-notice">
                对话已归档，仍可查看当前消息与执行摘要。
              </section>
            ) : null}

            <section className="workspace-session-grid workspace-session-grid-single">
              <section className="workspace-session-center-column">
                <section className="workspace-message-panel">
                  <ConversationFeed
                    messages={activeConversation.messages}
                    generations={activeConversation.generations}
                    events={sessionEvents}
                    runtimeRuns={sessionRuns}
                    activeBranchId={activeConversation.active_branch?.id ?? null}
                    messageActionBusyId={messageActionBusyId}
                    onEditMessage={(message) => {
                      const nextContent = window.prompt("编辑这条用户消息", message.content);
                      if (nextContent === null) {
                        return;
                      }
                      const trimmed = nextContent.trim();
                      if (!trimmed || trimmed === message.content.trim()) {
                        return;
                      }
                      setMessageActionBusyId(message.id);
                      void editMessageMutation
                        .mutateAsync({
                          sessionId: activeSession.id,
                          messageId: message.id,
                          content: trimmed,
                        })
                        .finally(() => setMessageActionBusyId((currentValue) => (currentValue === message.id ? null : currentValue)));
                    }}
                    onRegenerateMessage={(message) => {
                      setMessageActionBusyId(message.id);
                      void regenerateMessageMutation
                        .mutateAsync({ sessionId: activeSession.id, messageId: message.id })
                        .finally(() => setMessageActionBusyId((currentValue) => (currentValue === message.id ? null : currentValue)));
                    }}
                    onForkMessage={(message) => {
                      const branchName = window.prompt("新分支名称", `Branch from ${message.id.slice(0, 8)}`);
                      setMessageActionBusyId(message.id);
                      void forkMessageMutation
                        .mutateAsync({
                          sessionId: activeSession.id,
                          messageId: message.id,
                          name: branchName,
                        })
                        .finally(() => setMessageActionBusyId((currentValue) => (currentValue === message.id ? null : currentValue)));
                    }}
                    onRollbackMessage={(message) => {
                      setMessageActionBusyId(message.id);
                      void rollbackMessageMutation
                        .mutateAsync({ sessionId: activeSession.id, messageId: message.id })
                        .finally(() => setMessageActionBusyId((currentValue) => (currentValue === message.id ? null : currentValue)));
                    }}
                  />
                  <WorkbenchComposer
                    sessionId={activeSession.id}
                    disabled={activeSession.deleted_at !== null}
                    isGenerating={isGenerationActive}
                    isInterrupting={cancelGenerationMutation.isPending || cancelSessionMutation.isPending}
                    queuedCount={queuedGenerationCount}
                    onSend={async (content) => {
                      await sendChatMutation.mutateAsync({ id: activeSession.id, content });
                    }}
                    onInterrupt={async () => {
                      if (activeGeneration) {
                        await cancelGenerationMutation.mutateAsync({
                          sessionId: activeSession.id,
                          generationId: activeGeneration.id,
                        });
                        return;
                      }
                      await cancelSessionMutation.mutateAsync({ id: activeSession.id });
                    }}
                  />
                </section>
              </section>

              {workflowRunId ? (
                <aside
                  className={`workspace-graph-drawer${isInsightsOpen ? " workspace-graph-drawer-open" : ""}`}
                >
                  <button
                    className="workspace-graph-drawer-handle"
                    type="button"
                    onClick={() => setIsInsightsOpen((currentValue) => !currentValue)}
                    aria-expanded={isInsightsOpen}
                    aria-label={isInsightsOpen ? "收起图谱抽屉" : "展开图谱抽屉"}
                  >
                    <span className="workspace-graph-drawer-handle-indicator" aria-hidden="true" />
                  </button>

                  <section className="management-section-card workspace-graph-drawer-panel">
                    <div className="workspace-graph-drawer-panel-header">
                      <div>
                        <strong className="workspace-graph-drawer-title">任务与图谱</strong>
                        <p className="workspace-graph-drawer-copy">右侧抽屉，随时展开和收起。</p>
                      </div>
                      <button
                        className="workspace-graph-drawer-close"
                        type="button"
                        onClick={() => setIsInsightsOpen(false)}
                        aria-label="关闭图谱抽屉"
                      >
                        关闭
                      </button>
                    </div>

                    <div className="workspace-right-tabs">
                      <button
                        type="button"
                        className={`workspace-right-tab${selectedDrawerTab === "outline" ? " workspace-right-tab-active" : ""}`}
                        onClick={() => setSelectedDrawerTab("outline")}
                      >
                        任务树
                      </button>
                      <button
                        type="button"
                        className={`workspace-right-tab${selectedDrawerTab === "task" ? " workspace-right-tab-active" : ""}`}
                        onClick={() => setSelectedDrawerTab("task")}
                      >
                        任务图
                      </button>
                      <button
                        type="button"
                        className={`workspace-right-tab${selectedDrawerTab === "evidence" ? " workspace-right-tab-active" : ""}`}
                        onClick={() => setSelectedDrawerTab("evidence")}
                      >
                        {evidenceMode === "evidence" ? "证据图" : "因果图"}
                      </button>
                    </div>

                    <div className="workspace-graph-drawer-body">
                      {selectedDrawerTab === "outline" ? (
                        <WorkspaceSidebarSections
                          tasks={workflowQuery.data?.tasks ?? []}
                          onSelectTask={(taskId) => handleSelectNode("task", taskId)}
                        />
                      ) : null}

                      {selectedDrawerTab === "task" ? (
                        <div className="workspace-right-stack">
                          <GraphCanvasWrapper
                            title="任务图"
                            graph={taskGraph}
                            filters={taskFilters}
                            selectedNodeId={
                              selectedNode?.graphType === "task" ? selectedNode.nodeId : null
                            }
                            onFilterChange={setTaskFilters}
                            onSelectNode={(nodeId) => handleSelectNode("task", nodeId)}
                          />
                          {selectedNode?.graphType === "task" ? (
                            <NodeDetailPanel
                              selectedNode={selectedGraphNode}
                              relatedEdges={selectedGraphEdges}
                              workflowTask={selectedWorkflowTask}
                              timeline={selectedNodeTimeline}
                            />
                          ) : null}
                        </div>
                      ) : null}

                      {selectedDrawerTab === "evidence" ? (
                        <div className="workspace-right-stack">
                          <div className="session-graph-token-row workspace-evidence-mode-row">
                            <button
                              type="button"
                              className={`workspace-evidence-mode-chip${evidenceMode === "evidence" ? " workspace-evidence-mode-chip-active" : ""}`}
                              onClick={() => setEvidenceMode("evidence")}
                            >
                              证据图
                            </button>
                            <button
                              type="button"
                              className={`workspace-evidence-mode-chip${evidenceMode === "causal" ? " workspace-evidence-mode-chip-active" : ""}`}
                              onClick={() => setEvidenceMode("causal")}
                            >
                              因果图
                            </button>
                          </div>

                          <GraphCanvasWrapper
                            title={evidenceMode === "evidence" ? "证据图" : "因果图"}
                            graph={evidenceMode === "evidence" ? evidenceGraph : causalGraph}
                            filters={evidenceFilters}
                            selectedNodeId={
                              selectedNode?.graphType ===
                              (evidenceMode === "evidence" ? "evidence" : "causal")
                                ? selectedNode.nodeId
                                : null
                            }
                            onFilterChange={setEvidenceFilters}
                            onSelectNode={(nodeId) =>
                              handleSelectNode(
                                evidenceMode === "evidence" ? "evidence" : "causal",
                                nodeId,
                              )
                            }
                          />

                          {selectedNode &&
                          selectedNode.graphType ===
                            (evidenceMode === "evidence" ? "evidence" : "causal") ? (
                            <NodeDetailPanel
                              selectedNode={selectedGraphNode}
                              relatedEdges={selectedGraphEdges}
                              workflowTask={selectedWorkflowTask}
                              timeline={selectedNodeTimeline}
                            />
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </section>
                </aside>
              ) : null}
            </section>

            {isPlanDialogOpen ? (
              <div className="workspace-plan-modal-layer" role="presentation">
                <button
                  className="workspace-plan-modal-backdrop"
                  type="button"
                  aria-label="关闭计划弹窗"
                  onClick={() => setIsPlanDialogOpen(false)}
                />

                <section
                  className="workspace-plan-modal"
                  role="dialog"
                  aria-modal="true"
                  aria-label="计划与推进"
                >
                  <div className="workspace-plan-modal-header">
                    <div>
                      <h3 className="workspace-plan-modal-title">计划与推进</h3>
                      <p className="workspace-plan-modal-copy">放到弹窗层，避免持续占用聊天正文。</p>
                    </div>
                    <button
                      className="workspace-plan-modal-close"
                      type="button"
                      onClick={() => setIsPlanDialogOpen(false)}
                    >
                      关闭
                    </button>
                  </div>

                  <div className="workspace-plan-modal-body">
                    <WorkflowPlanCard
                      templates={templatesQuery.data}
                      selectedTemplateName={selectedTemplateName}
                      workflow={workflowQuery.data}
                      canAdvanceWorkflow={canAdvanceWorkflow}
                      workflowNeedsApproval={workflowNeedsApproval}
                      isStarting={startWorkflowMutation.isPending}
                      isAdvancing={advanceWorkflowMutation.isPending}
                      onSelectTemplate={setSelectedTemplateName}
                      onStartWorkflow={async () => {
                        setIsInsightsOpen(true);
                        await startWorkflowMutation.mutateAsync({
                          activeSessionId: activeSession.id,
                          templateName: selectedTemplateName || null,
                        });
                      }}
                      onAdvanceWorkflow={async () => {
                        if (!workflowRunId) {
                          return;
                        }
                        await advanceWorkflowMutation.mutateAsync({ runId: workflowRunId });
                      }}
                      onOpenReplay={() => setIsReplayPanelOpen(true)}
                      onExport={() => {
                        setIsReplayPanelOpen(true);
                        if (workflowExportQuery.data) {
                          downloadJson(
                            `session-${activeSession.id}-export.json`,
                            workflowExportQuery.data,
                          );
                        }
                      }}
                    />

                    {workflowNeedsApproval && workflowQuery.data ? (
                      <ApprovalCard
                        workflow={workflowQuery.data}
                        policyDraft={policyDraft}
                        isSavingPolicy={updatePolicyMutation.isPending}
                        onChangePolicy={handleChangePolicy}
                        onSavePolicy={async () => {
                          await updatePolicyMutation.mutateAsync({
                            id: activeSession.id,
                            runtime_policy_json: policyDraft,
                          });
                        }}
                        onApprove={async () => {
                          if (!workflowRunId) {
                            return;
                          }
                          await advanceWorkflowMutation.mutateAsync({
                            runId: workflowRunId,
                            approve: true,
                          });
                        }}
                        onReject={async () => {
                          await pauseSessionMutation.mutateAsync({ id: activeSession.id });
                        }}
                      />
                    ) : null}

                    {workflowRunId ? (
                      <details
                        className="management-section-card workspace-collapsible-panel"
                        open={isReplayPanelOpen}
                        onToggle={(event) =>
                          setIsReplayPanelOpen((event.currentTarget as HTMLDetailsElement).open)
                        }
                      >
                        <summary className="workspace-collapsible-summary">
                          <div>
                            <strong>回放与导出</strong>
                            <p>只在需要时加载，避免默认刷新和界面干扰。</p>
                          </div>
                          <span className="management-status-badge tone-neutral">
                            {workflowQuery.data?.status
                              ? formatWorkflowStatus(workflowQuery.data.status)
                              : "未开始"}
                          </span>
                        </summary>

                        <div className="workspace-collapsible-body">
                          <ReplayExportPanel
                            session={activeSession}
                            exportData={workflowExportQuery.data}
                            replayData={workflowReplayQuery.data}
                          />
                        </div>
                      </details>
                    ) : null}
                  </div>
                </section>
              </div>
            ) : null}
          </>
        ) : conversationQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话详情暂不可用</p>
            <p className="conversation-empty-state-copy">{conversationQuery.error.message}</p>
          </section>
        ) : null}
      </section>
    </main>
  );
}
