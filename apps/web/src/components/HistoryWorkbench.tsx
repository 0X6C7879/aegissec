import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  getSession,
  getSessionHistory,
  getTaskGraph,
  getWorkflowExport,
  getWorkflowReplay,
  listSessions,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { sortSessions } from "../lib/sessionUtils";

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

export function HistoryWorkbench() {
  const navigate = useNavigate();
  const [searchValue, setSearchValue] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", "history-workbench"],
    queryFn: ({ signal }) => listSessions(true, signal),
  });

  const filteredSessions = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    const sessions = sortSessions(sessionsQuery.data ?? []);
    if (!keyword) {
      return sessions;
    }

    return sessions.filter((session) => {
      return [session.title, session.goal ?? "", session.current_phase ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
  }, [searchValue, sessionsQuery.data]);

  useEffect(() => {
    if (!selectedSessionId) {
      setSelectedSessionId(filteredSessions[0]?.id ?? null);
      return;
    }

    if (!filteredSessions.some((session) => session.id === selectedSessionId)) {
      setSelectedSessionId(filteredSessions[0]?.id ?? null);
    }
  }, [filteredSessions, selectedSessionId]);

  const sessionDetailQuery = useQuery({
    enabled: Boolean(selectedSessionId),
    queryKey: ["session", selectedSessionId, "history-detail"],
    queryFn: ({ signal }) => getSession(selectedSessionId!, signal),
  });

  const sessionHistoryQuery = useQuery({
    enabled: Boolean(selectedSessionId),
    queryKey: ["session", selectedSessionId, "history-list"],
    queryFn: ({ signal }) => getSessionHistory(selectedSessionId!, { page_size: 40 }, signal),
  });

  const taskGraphQuery = useQuery({
    enabled: Boolean(selectedSessionId),
    queryKey: ["session", selectedSessionId, "history-task-graph"],
    queryFn: ({ signal }) => getTaskGraph(selectedSessionId!, signal),
  });

  const workflowRunId = taskGraphQuery.data?.workflow_run_id ?? null;

  const replayQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "history-replay"],
    queryFn: ({ signal }) => getWorkflowReplay(workflowRunId!, signal),
  });

  const exportQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "history-export"],
    queryFn: ({ signal }) => getWorkflowExport(workflowRunId!, signal),
  });

  const activeSession =
    filteredSessions.find((session) => session.id === selectedSessionId) ?? null;

  return (
    <main className="management-workbench">
      <section className="panel management-sidebar-panel">
        <div className="management-unified-header">
          <div>
            <h2 className="management-section-title">History</h2>
            <p className="management-unified-description">
              查看历史会话、回放步骤和导出入口，避免离开工作台主链路。
            </p>
          </div>
          <span className="management-status-badge tone-neutral">{filteredSessions.length}</span>
        </div>

        <input
          className="management-search-input"
          type="search"
          value={searchValue}
          onChange={(event) => setSearchValue(event.target.value)}
          placeholder="搜索历史会话"
        />

        <div className="management-list-shell">
          {filteredSessions.length === 0 ? (
            <div className="management-empty-state">
              <p className="management-empty-title">没有历史会话</p>
              <p className="management-empty-copy">
                生成 Session 和 Workflow 后，这里会自动出现历史记录。
              </p>
            </div>
          ) : (
            <ul className="management-list">
              {filteredSessions.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={`management-list-card${selectedSessionId === session.id ? " management-list-card-active" : ""}`}
                    onClick={() => setSelectedSessionId(session.id)}
                  >
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{session.title}</strong>
                      <span className="management-status-badge tone-neutral">{session.status}</span>
                    </div>
                    <p className="management-list-copy">
                      {session.goal ?? session.current_phase ?? "暂无目标摘要。"}
                    </p>
                    <span className="management-info-label">
                      {formatDateTime(session.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel management-detail-panel">
        {!selectedSessionId ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">选择一个历史会话</p>
            <p className="management-empty-copy">右侧会显示摘要、运行历史、回放步骤与导出入口。</p>
          </div>
        ) : sessionDetailQuery.isLoading && !sessionDetailQuery.data ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">正在读取历史详情</p>
            <p className="management-empty-copy">会话摘要和回放信息马上就绪。</p>
          </div>
        ) : activeSession ? (
          <div className="management-unified-body">
            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">会话摘要</h3>
                <span className="management-status-badge tone-neutral">{activeSession.id}</span>
              </div>

              <div className="management-info-grid">
                <div className="management-info-card">
                  <span className="management-info-label">当前 Phase</span>
                  <strong className="management-info-value">
                    {activeSession.current_phase ?? "未开始"}
                  </strong>
                </div>
                <div className="management-info-card">
                  <span className="management-info-label">更新时间</span>
                  <strong className="management-info-value">
                    {formatDateTime(activeSession.updated_at)}
                  </strong>
                </div>
                <div className="management-info-card management-info-card-full">
                  <span className="management-info-label">目标</span>
                  <strong className="management-info-value">
                    {activeSession.goal ?? "暂无目标说明。"}
                  </strong>
                </div>
              </div>

              <div className="management-action-row">
                <button
                  className="button button-primary"
                  type="button"
                  onClick={() => navigate(`/sessions/${activeSession.id}/chat`)}
                >
                  打开 Workspace
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={!replayQuery.data}
                  onClick={() => {
                    if (!replayQuery.data) {
                      return;
                    }
                    downloadJson(`session-${activeSession.id}-replay.json`, replayQuery.data);
                  }}
                >
                  打开回放
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={!exportQuery.data}
                  onClick={() => {
                    if (!exportQuery.data) {
                      return;
                    }
                    downloadJson(`session-${activeSession.id}-export.json`, exportQuery.data);
                  }}
                >
                  导出结果
                </button>
              </div>
            </section>

            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">最近日志</h3>
                <span className="management-status-badge tone-neutral">
                  {sessionHistoryQuery.data?.length ?? 0}
                </span>
              </div>
              {sessionHistoryQuery.data?.length ? (
                <ul className="management-list">
                  {sessionHistoryQuery.data.slice(0, 18).map((entry) => (
                    <li key={entry.id} className="management-subcard">
                      <div className="management-list-card-header">
                        <strong className="management-list-title">{entry.message}</strong>
                        <span className="management-token-chip">
                          {formatDateTime(entry.created_at)}
                        </span>
                      </div>
                      <div className="session-graph-token-row">
                        <span className="management-status-badge tone-neutral">{entry.level}</span>
                        <span className="management-token-chip">{entry.source}</span>
                        <span className="management-token-chip">{entry.event_type}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="management-empty-state session-graph-inline-empty">
                  <p className="management-empty-title">还没有日志摘要</p>
                  <p className="management-empty-copy">
                    执行记录进入数据库后，这里会显示结构化历史。
                  </p>
                </div>
              )}
            </section>

            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">回放视图</h3>
                <span className="management-status-badge tone-neutral">
                  {replayQuery.data?.replay_steps.length ?? 0}
                </span>
              </div>

              {replayQuery.data?.replay_steps.length ? (
                <ul className="management-list">
                  {replayQuery.data.replay_steps.slice(0, 8).map((step) => (
                    <li key={`${step.trace_id}-${step.index}`} className="management-subcard">
                      <div className="management-list-card-header">
                        <strong className="management-list-title">{step.task_name}</strong>
                        <span className="management-status-badge tone-neutral">{step.status}</span>
                      </div>
                      {step.summary ? <p className="management-list-copy">{step.summary}</p> : null}
                      <span className="management-info-label">
                        {formatDateTime(step.started_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="management-empty-state session-graph-inline-empty">
                  <p className="management-empty-title">还没有回放步骤</p>
                  <p className="management-empty-copy">
                    一旦当前 Session 进入工作流执行，这里会展示最近的 replay 片段。
                  </p>
                </div>
              )}
            </section>
          </div>
        ) : null}
      </section>
    </main>
  );
}
