import { useEffect, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  createSession,
  deleteSession,
  getCausalGraph,
  getTaskGraph,
  listSessions,
  restoreSession,
  updateSession,
} from "../lib/api";
import { sortSessions, upsertSession } from "../lib/sessionUtils";
import { useUiStore } from "../store/uiStore";
import type { SessionGraph, SessionGraphEdge, SessionGraphNode } from "../types/graphs";
import type { SessionSummary } from "../types/sessions";
import { ConversationSidebar } from "./ConversationSidebar";

function getSessionDisplayTitle(title: string): string {
  return title === "New Session" ? "新对话" : title;
}

function visibleSessionsForSidebar(sessions: SessionSummary[], activeSessionId: string | null): SessionSummary[] {
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

function formatStageFallback(stage: string | null): string {
  if (!stage) {
    return "未开始";
  }

  return stage.replace(/_/g, " · ");
}

function formatTaskStatusLabel(status: string | null): string {
  switch (status) {
    case "completed":
      return "已完成";
    case "in_progress":
      return "进行中";
    case "blocked":
      return "待审批";
    case "error":
      return "异常";
    case "pending":
      return "待执行";
    default:
      return status ?? "未知状态";
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
    case "error":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

function formatRelationLabel(relation: string): string {
  switch (relation) {
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

function formatNodeTypeLabel(nodeType: string): string {
  switch (nodeType) {
    case "stage":
      return "阶段";
    case "finding":
      return "发现";
    default:
      return nodeType;
  }
}

function formatScalarValue(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  return null;
}

function getCurrentTaskNode(graph: SessionGraph | undefined): SessionGraphNode | null {
  return graph?.nodes.find((node) => readBoolean(node.data.current)) ?? null;
}

function getTaskNodeStatus(node: SessionGraphNode): string | null {
  return readString(node.data.status);
}

function isWorkflowGraphMissing(message: string): boolean {
  return message.includes("Workflow run not found");
}

function getCausalDataEntries(node: SessionGraphNode): Array<[string, string]> {
  const ignoredKeys = new Set(["id", "title", "supports", "contradicts", "validates", "causes"]);

  return Object.entries(node.data)
    .filter(([key, value]) => !ignoredKeys.has(key) && formatScalarValue(value) !== null)
    .slice(0, 4)
    .map(([key, value]) => [key, formatScalarValue(value) ?? ""]);
}

function buildNodeLookup(graph: SessionGraph | undefined): Map<string, string> {
  return new Map((graph?.nodes ?? []).map((node) => [node.id, node.label]));
}

function getEdgeTitle(edge: SessionGraphEdge, nodeLookup: Map<string, string>): string {
  const sourceLabel = nodeLookup.get(edge.source) ?? edge.source;
  const targetLabel = nodeLookup.get(edge.target) ?? edge.target;
  return `${sourceLabel} → ${targetLabel}`;
}

type GraphSectionStateProps = {
  title: string;
  countLabel: string;
  children: React.ReactNode;
};

function GraphStateShell({ title, countLabel, children }: GraphSectionStateProps) {
  return (
    <div className="management-subcard">
      <div className="management-section-header">
        <h4 className="management-section-title">{title}</h4>
        <span className="management-status-badge tone-neutral">{countLabel}</span>
      </div>
      {children}
    </div>
  );
}

type TaskGraphSectionProps = {
  graph: SessionGraph | undefined;
  isLoading: boolean;
  error: Error | null;
};

function TaskGraphSection({ graph, isLoading, error }: TaskGraphSectionProps) {
  const nodeLookup = buildNodeLookup(graph);

  return (
    <section className="management-section-card">
      <div className="management-section-header">
        <h3 className="management-section-title">任务图</h3>
        <span className="management-status-badge tone-neutral">
          {(graph?.nodes.length ?? 0) + " 节点 / " + (graph?.edges.length ?? 0) + " 连线"}
        </span>
      </div>

      {isLoading && !graph ? (
        <div className="management-empty-state">
          <p className="management-empty-title">正在读取任务图</p>
          <p className="management-empty-copy">稍后即可查看当前阶段顺序。</p>
        </div>
      ) : error && !graph ? (
        isWorkflowGraphMissing(error.message) ? (
          <div className="management-empty-state">
            <p className="management-empty-title">还没有任务图</p>
            <p className="management-empty-copy">先让当前会话启动一次工作流，这里才会出现阶段链路。</p>
          </div>
        ) : (
          <div className="management-error-banner">{error.message}</div>
        )
      ) : !graph || graph.nodes.length === 0 ? (
        <div className="management-empty-state">
          <p className="management-empty-title">任务图为空</p>
          <p className="management-empty-copy">当前 run 还没有可展示的阶段节点。</p>
        </div>
      ) : (
        <div className="management-unified-stack">
          <GraphStateShell title="节点" countLabel={`${graph.nodes.length} 项`}>
            <ul className="management-list">
              {graph.nodes.map((node) => {
                const status = getTaskNodeStatus(node);
                const sequence = readNumber(node.data.sequence);
                const role = readString(node.data.role);
                const requiresApproval = readBoolean(node.data.requires_approval);
                const isCurrent = readBoolean(node.data.current);

                return (
                  <li key={node.id} className="management-subcard session-graph-item-card">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{node.label}</strong>
                      <span className={`management-status-badge ${getTaskStatusTone(status)}`}>
                        {isCurrent ? "当前阶段" : formatTaskStatusLabel(status)}
                      </span>
                    </div>

                    <div className="session-graph-token-row">
                      {sequence !== null ? <span className="management-token-chip">#{sequence}</span> : null}
                      <span className="management-token-chip">{formatNodeTypeLabel(node.node_type)}</span>
                      {role ? <span className="management-token-chip">{role}</span> : null}
                      {requiresApproval ? <span className="management-token-chip">需审批</span> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </GraphStateShell>

          <GraphStateShell title="连线" countLabel={`${graph.edges.length} 项`}>
            {graph.edges.length === 0 ? (
              <div className="management-empty-state session-graph-inline-empty">
                <p className="management-empty-title">还没有连线</p>
                <p className="management-empty-copy">节点生成后，前后依赖会显示在这里。</p>
              </div>
            ) : (
              <ul className="management-list">
                {graph.edges.map((edge) => (
                  <li key={edge.id} className="management-subcard session-graph-item-card">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{getEdgeTitle(edge, nodeLookup)}</strong>
                      <span className="management-status-badge tone-neutral">
                        {formatRelationLabel(edge.relation)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </GraphStateShell>
        </div>
      )}
    </section>
  );
}

type CausalGraphSectionProps = {
  graph: SessionGraph | undefined;
  isLoading: boolean;
  error: Error | null;
};

function CausalGraphSection({ graph, isLoading, error }: CausalGraphSectionProps) {
  const nodeLookup = buildNodeLookup(graph);

  return (
    <section className="management-section-card">
      <div className="management-section-header">
        <h3 className="management-section-title">因果图</h3>
        <span className="management-status-badge tone-neutral">
          {(graph?.nodes.length ?? 0) + " 节点 / " + (graph?.edges.length ?? 0) + " 连线"}
        </span>
      </div>

      {isLoading && !graph ? (
        <div className="management-empty-state">
          <p className="management-empty-title">正在读取因果图</p>
          <p className="management-empty-copy">稍后即可查看 findings 之间的关系。</p>
        </div>
      ) : error && !graph ? (
        isWorkflowGraphMissing(error.message) ? (
          <div className="management-empty-state">
            <p className="management-empty-title">还没有因果图</p>
            <p className="management-empty-copy">等工作流进入图谱阶段后，这里会展示 findings 与关系。</p>
          </div>
        ) : (
          <div className="management-error-banner">{error.message}</div>
        )
      ) : !graph || graph.nodes.length === 0 ? (
        <div className="management-empty-state">
          <p className="management-empty-title">因果图为空</p>
          <p className="management-empty-copy">当前 run 还没有 findings，可先从任务图确认流程推进情况。</p>
        </div>
      ) : (
        <div className="management-unified-stack">
          <GraphStateShell title="节点" countLabel={`${graph.nodes.length} 项`}>
            <ul className="management-list">
              {graph.nodes.map((node) => {
                const entries = getCausalDataEntries(node);

                return (
                  <li key={node.id} className="management-subcard session-graph-item-card">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{node.label}</strong>
                      <span className="management-status-badge tone-neutral">
                        {formatNodeTypeLabel(node.node_type)}
                      </span>
                    </div>

                    {entries.length > 0 ? (
                      <dl className="session-graph-data-list">
                        {entries.map(([key, value]) => (
                          <div key={`${node.id}-${key}`}>
                            <dt>{key}</dt>
                            <dd>{value}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <p className="management-empty-copy">暂无附加字段。</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </GraphStateShell>

          <GraphStateShell title="连线" countLabel={`${graph.edges.length} 项`}>
            {graph.edges.length === 0 ? (
              <div className="management-empty-state session-graph-inline-empty">
                <p className="management-empty-title">还没有关系连线</p>
                <p className="management-empty-copy">当 findings 之间存在支撑、验证或因果关系时会出现在这里。</p>
              </div>
            ) : (
              <ul className="management-list">
                {graph.edges.map((edge) => (
                  <li key={edge.id} className="management-subcard session-graph-item-card">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{getEdgeTitle(edge, nodeLookup)}</strong>
                      <span className="management-status-badge tone-neutral">
                        {formatRelationLabel(edge.relation)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </GraphStateShell>
        </div>
      )}
    </section>
  );
}

export function SessionGraphWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId } = useParams<{ sessionId: string }>();
  const setLastVisitedSessionId = useUiStore((state) => state.setLastVisitedSessionId);

  useEffect(() => {
    if (sessionId) {
      setLastVisitedSessionId(sessionId);
    }
  }, [sessionId, setLastVisitedSessionId]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", "merged-workbench"],
    queryFn: ({ signal }) => listSessions(true, signal),
  });

  const sortedSessions = useMemo(() => sortSessions(sessionsQuery.data ?? []), [sessionsQuery.data]);
  const activeSession = useMemo(
    () => sortedSessions.find((session) => session.id === sessionId) ?? null,
    [sessionId, sortedSessions],
  );
  const sidebarSessions = useMemo(
    () => visibleSessionsForSidebar(sortedSessions, sessionId ?? null),
    [sessionId, sortedSessions],
  );

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
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: async (_value, deletedId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["session", deletedId] }),
      ]);
    },
  });

  const restoreSessionMutation = useMutation({
    mutationFn: (id: string) => restoreSession(id),
    onSuccess: async (restoredSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, restoredSession),
      );
      await queryClient.invalidateQueries({ queryKey: ["session", restoredSession.id] });
    },
  });

  const taskGraphQuery = useQuery({
    enabled: Boolean(sessionId),
    queryKey: ["session", sessionId, "graph", "task"],
    queryFn: ({ signal }) => getTaskGraph(sessionId!, signal),
  });

  const causalGraphQuery = useQuery({
    enabled: Boolean(sessionId),
    queryKey: ["session", sessionId, "graph", "causal"],
    queryFn: ({ signal }) => getCausalGraph(sessionId!, signal),
  });

  const currentTaskNode = getCurrentTaskNode(taskGraphQuery.data);
  const currentStageLabel = currentTaskNode?.label ?? formatStageFallback(taskGraphQuery.data?.current_stage ?? causalGraphQuery.data?.current_stage ?? null);
  const workflowRunId = taskGraphQuery.data?.workflow_run_id ?? causalGraphQuery.data?.workflow_run_id ?? null;

  async function handleRename(id: string): Promise<void> {
    const targetSession = sortedSessions.find((session) => session.id === id);
    if (!targetSession) {
      return;
    }

    const nextTitle = window.prompt("修改对话标题", targetSession.title);
    if (nextTitle === null) {
      return;
    }

    const trimmedTitle = nextTitle.trim();
    if (!trimmedTitle || trimmedTitle === targetSession.title) {
      return;
    }

    await renameSessionMutation.mutateAsync({ id, title: trimmedTitle });
  }

  function handleSelect(id: string): void {
    navigate(`/sessions/${id}/graph`);
  }

  return (
    <main className="conversation-workbench">
      <ConversationSidebar
        collapsed={false}
        onToggleCollapsed={() => {}}
        sessions={sidebarSessions}
        activeSessionId={sessionId ?? null}
        isCreating={createSessionMutation.isPending}
        onCreate={async () => {
          await createSessionMutation.mutateAsync();
        }}
        onSelect={handleSelect}
        onRename={handleRename}
        onArchive={async (id) => {
          await deleteSessionMutation.mutateAsync(id);
        }}
        onRestore={async (id) => {
          await restoreSessionMutation.mutateAsync(id);
        }}
      />

      <section className="conversation-main-shell">
        {sessionsQuery.isLoading && !activeSession ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在加载图谱</p>
            <p className="conversation-empty-state-copy">稍后即可查看当前会话的任务图与因果图。</p>
          </section>
        ) : sessionsQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话列表暂不可用</p>
            <p className="conversation-empty-state-copy">{sessionsQuery.error.message}</p>
          </section>
        ) : !activeSession ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">未找到该对话</p>
            <p className="conversation-empty-state-copy">请从左侧重新选择一个已有对话。</p>
          </section>
        ) : (
          <>
            <header className="conversation-header">
              <div className="conversation-header-copy">
                <h2 className="conversation-title">{getSessionDisplayTitle(activeSession.title)}</h2>
              </div>

              <div className="conversation-header-actions">
                <button
                  className="inline-button"
                  type="button"
                  onClick={() => navigate(`/sessions/${activeSession.id}/chat`)}
                >
                  返回对话
                </button>
                <span className="management-status-badge tone-neutral">{currentStageLabel}</span>
              </div>
            </header>

            {activeSession.deleted_at ? (
              <section className="conversation-inline-notice">对话已归档，图谱仍可查看，恢复后可继续推进流程。</section>
            ) : null}

            <section className="session-graph-body-shell">
              <section className="panel management-unified-panel session-graph-panel" aria-label="图谱查看">
                <div className="management-info-grid">
                  <div className="management-info-card">
                    <span className="management-info-label">当前阶段</span>
                    <strong className="management-info-value">{currentStageLabel}</strong>
                  </div>
                  <div className="management-info-card management-info-card-full">
                    <span className="management-info-label">Workflow Run</span>
                    <strong className="management-info-value management-info-code">
                      {workflowRunId ?? "尚未生成"}
                    </strong>
                  </div>
                </div>

                <div className="management-metric-row management-metric-row-wide">
                  <div className="management-metric-card">
                    <span className="management-metric-label">任务节点</span>
                    <strong className="management-metric-value">{taskGraphQuery.data?.nodes.length ?? 0}</strong>
                  </div>
                  <div className="management-metric-card">
                    <span className="management-metric-label">任务连线</span>
                    <strong className="management-metric-value">{taskGraphQuery.data?.edges.length ?? 0}</strong>
                  </div>
                  <div className="management-metric-card">
                    <span className="management-metric-label">因果节点</span>
                    <strong className="management-metric-value">{causalGraphQuery.data?.nodes.length ?? 0}</strong>
                  </div>
                  <div className="management-metric-card">
                    <span className="management-metric-label">因果连线</span>
                    <strong className="management-metric-value">{causalGraphQuery.data?.edges.length ?? 0}</strong>
                  </div>
                </div>

                <div className="management-dual-column">
                  <TaskGraphSection
                    graph={taskGraphQuery.data}
                    isLoading={taskGraphQuery.isLoading}
                    error={taskGraphQuery.isError ? taskGraphQuery.error : null}
                  />
                  <CausalGraphSection
                    graph={causalGraphQuery.data}
                    isLoading={causalGraphQuery.isLoading}
                    error={causalGraphQuery.isError ? causalGraphQuery.error : null}
                  />
                </div>
              </section>
            </section>
          </>
        )}
      </section>
    </main>
  );
}
