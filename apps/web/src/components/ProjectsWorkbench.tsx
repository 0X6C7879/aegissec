import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  createProject,
  deleteProject,
  getProject,
  listProjects,
  restoreProject,
  updateProject,
} from "../lib/api";
import { formatDateTime } from "../lib/format";

const PROJECTS_QUERY_KEY = ["projects", "workspace"] as const;

export function ProjectsWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchValue, setSearchValue] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  const projectsQuery = useQuery({
    queryKey: PROJECTS_QUERY_KEY,
    queryFn: ({ signal }) => listProjects({ include_deleted: true, page_size: 100 }, signal),
  });

  const filteredProjects = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    if (!keyword) {
      return projectsQuery.data ?? [];
    }

    return (projectsQuery.data ?? []).filter((project) => {
      return [project.name, project.description ?? ""].join(" ").toLowerCase().includes(keyword);
    });
  }, [projectsQuery.data, searchValue]);

  useEffect(() => {
    if (!selectedProjectId) {
      setSelectedProjectId(filteredProjects[0]?.id ?? null);
      return;
    }

    if (!filteredProjects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(filteredProjects[0]?.id ?? null);
    }
  }, [filteredProjects, selectedProjectId]);

  const projectDetailQuery = useQuery({
    enabled: Boolean(selectedProjectId),
    queryKey: ["projects", "detail", selectedProjectId],
    queryFn: ({ signal }) => getProject(selectedProjectId!, signal),
  });

  useEffect(() => {
    if (!projectDetailQuery.data) {
      return;
    }

    setEditName(projectDetailQuery.data.name);
    setEditDescription(projectDetailQuery.data.description ?? "");
  }, [projectDetailQuery.data]);

  const createProjectMutation = useMutation({
    mutationFn: () =>
      createProject({ name: createName.trim(), description: createDescription.trim() || null }),
    onSuccess: async (project) => {
      setCreateName("");
      setCreateDescription("");
      setSelectedProjectId(project.id);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const updateProjectMutation = useMutation({
    mutationFn: (projectId: string) =>
      updateProject(projectId, {
        name: editName.trim(),
        description: editDescription.trim() || null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({ queryKey: ["projects", "detail", selectedProjectId] });
    },
  });

  const deleteProjectMutation = useMutation({
    mutationFn: (projectId: string) => deleteProject(projectId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({ queryKey: ["projects", "detail"] });
    },
  });

  const restoreProjectMutation = useMutation({
    mutationFn: (projectId: string) => restoreProject(projectId),
    onSuccess: async (project) => {
      setSelectedProjectId(project.id);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({ queryKey: ["projects", "detail", project.id] });
    },
  });

  const activeProject = projectDetailQuery.data ?? null;
  const mutationErrorMessage = createProjectMutation.isError
    ? createProjectMutation.error.message
    : updateProjectMutation.isError
      ? updateProjectMutation.error.message
      : deleteProjectMutation.isError
        ? deleteProjectMutation.error.message
        : restoreProjectMutation.isError
          ? restoreProjectMutation.error.message
          : null;

  return (
    <main className="management-workbench">
      <section className="panel management-sidebar-panel">
        <div className="management-unified-header">
          <div>
            <h2 className="management-section-title">Projects</h2>
            <p className="management-unified-description">
              轻量管理项目空间，并把已有会话绑定到统一上下文。
            </p>
          </div>
          <span className="management-status-badge tone-neutral">{filteredProjects.length}</span>
        </div>

        <input
          className="management-search-input"
          type="search"
          value={searchValue}
          onChange={(event) => setSearchValue(event.target.value)}
          placeholder="搜索项目"
        />

        <form
          className="management-unified-stack"
          onSubmit={(event) => {
            event.preventDefault();
            if (!createName.trim()) {
              return;
            }
            void createProjectMutation.mutateAsync();
          }}
        >
          <label className="field-label">
            新项目名称
            <input
              className="field-input"
              type="text"
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
              placeholder="例如：SRC 复盘"
            />
          </label>
          <label className="field-label">
            项目说明
            <textarea
              className="field-textarea"
              value={createDescription}
              onChange={(event) => setCreateDescription(event.target.value)}
              placeholder="记录项目范围、入口或授权说明"
            />
          </label>
          <button
            className="button button-primary"
            type="submit"
            disabled={createProjectMutation.isPending || !createName.trim()}
          >
            {createProjectMutation.isPending ? "创建中" : "创建项目"}
          </button>
        </form>

        <div className="management-list-shell">
          {filteredProjects.length === 0 ? (
            <div className="management-empty-state">
              <p className="management-empty-title">还没有项目</p>
              <p className="management-empty-copy">
                先创建一个项目，再把会话绑定到该项目以便后续历史检索。
              </p>
            </div>
          ) : (
            <ul className="management-list">
              {filteredProjects.map((project) => (
                <li key={project.id}>
                  <button
                    type="button"
                    className={`management-list-card${selectedProjectId === project.id ? " management-list-card-active" : ""}`}
                    onClick={() => setSelectedProjectId(project.id)}
                  >
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{project.name}</strong>
                      {project.deleted_at ? (
                        <span className="management-status-badge tone-warning">已归档</span>
                      ) : null}
                    </div>
                    <p className="management-list-copy">
                      {project.description ?? "暂无项目说明。"}
                    </p>
                    <span className="management-info-label">
                      {formatDateTime(project.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel management-detail-panel">
        {!selectedProjectId ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">选择一个项目</p>
            <p className="management-empty-copy">右侧会显示项目详情、会话列表与编辑操作。</p>
          </div>
        ) : projectDetailQuery.isLoading && !activeProject ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">正在读取项目</p>
            <p className="management-empty-copy">项目详情和会话绑定列表马上就绪。</p>
          </div>
        ) : projectDetailQuery.isError ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">项目详情暂不可用</p>
            <p className="management-empty-copy">{projectDetailQuery.error.message}</p>
          </div>
        ) : activeProject ? (
          <div className="management-unified-body">
            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">项目详情</h3>
                <span className="management-status-badge tone-neutral">{activeProject.id}</span>
              </div>

              <div className="management-info-grid">
                <div className="management-info-card">
                  <span className="management-info-label">创建时间</span>
                  <strong className="management-info-value">
                    {formatDateTime(activeProject.created_at)}
                  </strong>
                </div>
                <div className="management-info-card">
                  <span className="management-info-label">更新时间</span>
                  <strong className="management-info-value">
                    {formatDateTime(activeProject.updated_at)}
                  </strong>
                </div>
                <div className="management-info-card management-info-card-full">
                  <span className="management-info-label">状态</span>
                  <strong className="management-info-value">
                    {activeProject.deleted_at ? "已归档" : "活跃"}
                  </strong>
                </div>
              </div>

              <label className="field-label">
                名称
                <input
                  className="field-input"
                  type="text"
                  value={editName}
                  onChange={(event) => setEditName(event.target.value)}
                />
              </label>
              <label className="field-label">
                说明
                <textarea
                  className="field-textarea"
                  value={editDescription}
                  onChange={(event) => setEditDescription(event.target.value)}
                />
              </label>

              {mutationErrorMessage ? (
                <div className="management-error-banner">{mutationErrorMessage}</div>
              ) : null}

              <div className="management-action-row">
                <button
                  className="button button-primary"
                  type="button"
                  disabled={updateProjectMutation.isPending || !editName.trim()}
                  onClick={() => void updateProjectMutation.mutateAsync(activeProject.id)}
                >
                  {updateProjectMutation.isPending ? "保存中" : "保存项目"}
                </button>
                {activeProject.deleted_at ? (
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={restoreProjectMutation.isPending}
                    onClick={() => void restoreProjectMutation.mutateAsync(activeProject.id)}
                  >
                    {restoreProjectMutation.isPending ? "恢复中" : "恢复项目"}
                  </button>
                ) : (
                  <button
                    className="button button-danger"
                    type="button"
                    disabled={deleteProjectMutation.isPending}
                    onClick={() => void deleteProjectMutation.mutateAsync(activeProject.id)}
                  >
                    {deleteProjectMutation.isPending ? "归档中" : "归档项目"}
                  </button>
                )}
              </div>
            </section>

            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">项目会话</h3>
                <span className="management-status-badge tone-neutral">
                  {activeProject.sessions.length}
                </span>
              </div>

              {activeProject.sessions.length === 0 ? (
                <div className="management-empty-state session-graph-inline-empty">
                  <p className="management-empty-title">项目还没有会话</p>
                  <p className="management-empty-copy">
                    将 Session 绑定到当前项目后，这里会显示会话与阶段状态。
                  </p>
                </div>
              ) : (
                <ul className="management-list">
                  {activeProject.sessions.map((session) => (
                    <li key={session.id} className="management-subcard">
                      <div className="management-list-card-header">
                        <strong className="management-list-title">{session.title}</strong>
                        <span className="management-status-badge tone-neutral">
                          {session.status}
                        </span>
                      </div>
                      <p className="management-list-copy">
                        {session.goal ?? session.current_phase ?? "暂无目标说明。"}
                      </p>
                      <div className="management-action-row">
                        <button
                          className="button button-secondary"
                          type="button"
                          onClick={() => navigate(`/sessions/${session.id}/chat`)}
                        >
                          打开 Workspace
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        ) : null}
      </section>
    </main>
  );
}
