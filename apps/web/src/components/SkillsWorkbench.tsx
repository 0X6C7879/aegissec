import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  getSkill,
  getSkillContent,
  getSkillContext,
  listSkills,
  refreshSkills,
  rescanSkills,
  scanSkills,
  toggleSkill,
} from "../lib/api";
import { clampTextByLines } from "../lib/pretext";
import {
  extractSkillOrchestrationSnapshot,
  formatOrchestrationRole,
  readSkillDisplayName,
} from "../lib/skillOrchestration";
import type { SkillContextSkill, SkillRecord, SkillRecordStatus } from "../types/skills";

const SKILLS_QUERY_KEY = ["skills"] as const;
const SKILL_CONTEXT_QUERY_KEY = ["skills", "context"] as const;
const UI_FONT_FAMILY =
  '"JetBrains Mono", "IBM Plex Mono", "Fira Code", "SFMono-Regular", Consolas, "Liberation Mono", monospace';
const SKILL_TITLE_FONT = `600 15.36px ${UI_FONT_FAMILY}`;
const SKILL_DESCRIPTION_FONT = `400 14.08px ${UI_FONT_FAMILY}`;
const SKILL_TITLE_LINE_HEIGHT = 22;
const SKILL_DESCRIPTION_LINE_HEIGHT = 22;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readFirstString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }

  return null;
}

function getInnerWidth(element: HTMLElement): number {
  const styles = window.getComputedStyle(element);
  const horizontalPadding =
    Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);

  return Math.max(0, Math.floor(element.clientWidth - horizontalPadding));
}

function useCardInnerWidth<T extends HTMLElement>() {
  const elementRef = useRef<T | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = elementRef.current;

    if (!element || typeof ResizeObserver === "undefined") {
      return;
    }

    const syncWidth = () => setWidth(getInnerWidth(element));
    syncWidth();

    const observer = new ResizeObserver(() => {
      syncWidth();
    });

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, []);

  return {
    elementRef,
    width,
  };
}

function getSkillTone(status: SkillRecordStatus): string {
  switch (status) {
    case "loaded":
      return "tone-loaded";
    case "invalid":
      return "tone-invalid";
    case "ignored":
      return "tone-ignored";
    default:
      return "tone-neutral";
  }
}

type SkillListCardProps = {
  isActive: boolean;
  onOpen: () => void;
  skill: SkillRecord;
};

function SkillListCard({ isActive, onOpen, skill }: SkillListCardProps) {
  const { elementRef, width } = useCardInnerWidth<HTMLButtonElement>();
  const description = skill.description || "暂无描述。";

  const titleLayout = useMemo(
    () => clampTextByLines(skill.name, SKILL_TITLE_FONT, width, SKILL_TITLE_LINE_HEIGHT, 2),
    [skill.name, width],
  );
  const descriptionLayout = useMemo(
    () =>
      clampTextByLines(
        description,
        SKILL_DESCRIPTION_FONT,
        width,
        SKILL_DESCRIPTION_LINE_HEIGHT,
        3,
      ),
    [description, width],
  );

  return (
    <button
      ref={elementRef}
      className={`management-list-card skills-card${isActive ? " management-list-card-active" : ""}`}
      type="button"
      onClick={onOpen}
    >
      <strong
        className="management-list-title skills-card-title"
        title={titleLayout.isClamped ? skill.name : undefined}
      >
        {titleLayout.displayText}
      </strong>
      <p
        className="skills-card-description"
        title={descriptionLayout.isClamped ? description : undefined}
      >
        {descriptionLayout.displayText}
      </p>
      <div className="action-row">
        <span className={`management-status-badge ${getSkillTone(skill.status)}`}>
          {skill.status}
        </span>
        <span
          className={`management-status-badge ${skill.enabled ? "tone-success" : "tone-neutral"}`}
        >
          {skill.enabled ? "已启用" : "已禁用"}
        </span>
      </div>
    </button>
  );
}

function buildSearchIndex(skill: SkillRecord): string {
  return [skill.name, skill.description, skill.status, skill.root_dir, skill.entry_file]
    .join(" ")
    .toLowerCase();
}

function stringifyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function syncSkillCollection(
  queryClient: ReturnType<typeof useQueryClient>,
  navigate: ReturnType<typeof useNavigate>,
  selectedSkillId: string | null,
  refreshedSkills: SkillRecord[],
) {
  queryClient.setQueryData<SkillRecord[]>(SKILLS_QUERY_KEY, refreshedSkills);
  void queryClient.invalidateQueries({ queryKey: ["skills", "detail"] });
  void queryClient.invalidateQueries({ queryKey: ["skills", "content"] });
  void queryClient.invalidateQueries({ queryKey: SKILL_CONTEXT_QUERY_KEY });

  if (selectedSkillId && !refreshedSkills.some((skill) => skill.id === selectedSkillId)) {
    const nextSkill = refreshedSkills[0];
    navigate(nextSkill ? `/skills/${nextSkill.id}` : "/skills", { replace: true });
  }
}

export function SkillsWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { skillId } = useParams<{ skillId?: string }>();
  const [searchValue, setSearchValue] = useState("");

  const skillsQuery = useQuery({
    queryKey: SKILLS_QUERY_KEY,
    queryFn: ({ signal }) => listSkills(signal),
  });

  const filteredSkills = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    const skills = skillsQuery.data ?? [];

    if (!keyword) {
      return skills;
    }

    return skills.filter((skill) => buildSearchIndex(skill).includes(keyword));
  }, [searchValue, skillsQuery.data]);

  const selectedSkillId = useMemo(() => {
    const allSkills = skillsQuery.data ?? [];

    if (skillId && allSkills.some((skill) => skill.id === skillId)) {
      return skillId;
    }

    return null;
  }, [skillId, skillsQuery.data]);

  const activeSkillSummary = useMemo(
    () => (skillsQuery.data ?? []).find((skill) => skill.id === selectedSkillId) ?? null,
    [selectedSkillId, skillsQuery.data],
  );

  const skillDetailQuery = useQuery({
    enabled: Boolean(selectedSkillId),
    queryKey: ["skills", "detail", selectedSkillId],
    queryFn: ({ signal }) => getSkill(selectedSkillId!, signal),
  });

  const skillContentQuery = useQuery({
    enabled: Boolean(selectedSkillId),
    queryKey: ["skills", "content", selectedSkillId],
    queryFn: ({ signal }) => getSkillContent(selectedSkillId!, signal),
  });

  const skillContextQuery = useQuery({
    queryKey: SKILL_CONTEXT_QUERY_KEY,
    queryFn: ({ signal }) => getSkillContext(signal),
  });

  const scanMutation = useMutation({
    mutationFn: () => scanSkills(),
    onSuccess: (scannedSkills) => {
      syncSkillCollection(queryClient, navigate, selectedSkillId, scannedSkills);
    },
  });

  const rescanMutation = useMutation({
    mutationFn: () => rescanSkills(),
    onSuccess: (rescannedSkills) => {
      syncSkillCollection(queryClient, navigate, selectedSkillId, rescannedSkills);
    },
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshSkills(),
    onSuccess: (refreshedSkills) => {
      syncSkillCollection(queryClient, navigate, selectedSkillId, refreshedSkills);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => toggleSkill(id, enabled),
    onSuccess: async (updatedSkill) => {
      queryClient.setQueryData<SkillRecord[] | undefined>(SKILLS_QUERY_KEY, (currentValue) =>
        currentValue?.map((skill) => (skill.id === updatedSkill.id ? updatedSkill : skill)),
      );
      queryClient.setQueryData<SkillRecord | undefined>(
        ["skills", "detail", updatedSkill.id],
        updatedSkill,
      );
      await queryClient.invalidateQueries({ queryKey: ["skills", "detail", updatedSkill.id] });
      await queryClient.invalidateQueries({ queryKey: SKILL_CONTEXT_QUERY_KEY });
    },
  });

  const activeSkill = skillDetailQuery.data ?? activeSkillSummary;
  const activeSkillParameterSchema =
    skillContentQuery.data?.parameter_schema ?? activeSkill?.parameter_schema ?? {};
  const activeSkillContextEntry = useMemo<SkillContextSkill | null>(
    () =>
      skillContextQuery.data?.payload.skills.find((skill) => skill.id === selectedSkillId) ?? null,
    [selectedSkillId, skillContextQuery.data],
  );
  const orchestrationSnapshot = useMemo(
    () => extractSkillOrchestrationSnapshot(skillContextQuery.data?.payload),
    [skillContextQuery.data?.payload],
  );
  const selectedOrchestrationSkills = orchestrationSnapshot?.selectedSkills.filter(isRecord) ?? [];
  const preparedOrchestrationSkills =
    orchestrationSnapshot?.preparedSelectedSkills.filter(isRecord) ?? [];
  const orchestrationWorkerResults = orchestrationSnapshot?.workerResults.filter(isRecord) ?? [];
  const orchestrationNodeResults = orchestrationSnapshot?.nodeResults.filter(isRecord) ?? [];
  const orchestrationPlan =
    orchestrationSnapshot?.plan && isRecord(orchestrationSnapshot.plan)
      ? orchestrationSnapshot.plan
      : null;
  const orchestrationExecution =
    orchestrationSnapshot?.execution && isRecord(orchestrationSnapshot.execution)
      ? orchestrationSnapshot.execution
      : null;
  const orchestrationStageTransition =
    orchestrationSnapshot?.stageTransition && isRecord(orchestrationSnapshot.stageTransition)
      ? orchestrationSnapshot.stageTransition
      : null;
  const replannedSkillContext =
    orchestrationSnapshot?.replannedContext && isRecord(orchestrationSnapshot.replannedContext)
      ? orchestrationSnapshot.replannedContext
      : null;
  const replannedSelectedSkills = Array.isArray(replannedSkillContext?.["selected_skills"])
    ? replannedSkillContext["selected_skills"].filter(isRecord)
    : [];
  const filteredCount = filteredSkills.length;
  const mutationErrorMessage = toggleMutation.isError
    ? toggleMutation.error.message
    : scanMutation.isError
      ? scanMutation.error.message
      : rescanMutation.isError
        ? rescanMutation.error.message
        : refreshMutation.isError
          ? refreshMutation.error.message
          : null;
  const showGlobalOrchestrationOverview = false;

  const detailContent =
    !selectedSkillId || !activeSkill
      ? null
      : createPortal(
          <div className="management-modal-backdrop" role="presentation">
            <button
              className="management-modal-dismiss"
              type="button"
              aria-label="关闭 Skill 详情"
              onClick={() => navigate("/skills")}
            />
            <section
              className="management-modal-card panel"
              role="dialog"
              aria-modal="true"
              aria-label={`${activeSkill.name} 详情`}
            >
              <div className="management-modal-header management-sticky-toolbar">
                <div className="management-detail-copy">
                  <h3 className="panel-title">{activeSkill.name}</h3>
                  <p className="management-unified-description">{activeSkill.directory_name}</p>
                </div>

                <div className="management-action-row">
                  <button
                    className={
                      activeSkill.enabled ? "button button-secondary" : "button button-primary"
                    }
                    type="button"
                    disabled={toggleMutation.isPending}
                    onClick={() =>
                      void toggleMutation.mutateAsync({
                        id: activeSkill.id,
                        enabled: !activeSkill.enabled,
                      })
                    }
                  >
                    {toggleMutation.isPending
                      ? "提交中"
                      : activeSkill.enabled
                        ? "禁用 Skill"
                        : "启用 Skill"}
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={() => navigate("/skills")}
                  >
                    清空选择
                  </button>
                </div>
              </div>

              <div className="workspace-node-detail-modal-body">
                {skillDetailQuery.isLoading ? (
                  <div className="management-inline-notice">正在加载详情。</div>
                ) : null}
                {skillDetailQuery.isError ? (
                  <div className="management-error-banner">{skillDetailQuery.error.message}</div>
                ) : null}
                {activeSkill.error_message ? (
                  <div className="management-error-banner">{activeSkill.error_message}</div>
                ) : null}
                {skillContextQuery.isError ? (
                  <div className="management-error-banner">{skillContextQuery.error.message}</div>
                ) : null}

                <p className="skills-modal-description">
                  {activeSkill.description || "暂无描述。"}
                </p>

                <div className="management-info-grid">
                  <div className="management-info-card">
                    <span className="management-info-label">状态</span>
                    <strong
                      className={`management-status-badge ${getSkillTone(activeSkill.status)}`}
                    >
                      {activeSkill.status}
                    </strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">启用状态</span>
                    <strong
                      className={`management-status-badge ${activeSkill.enabled ? "tone-success" : "tone-neutral"}`}
                    >
                      {activeSkill.enabled ? "已启用" : "已禁用"}
                    </strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">来源</span>
                    <strong className="management-info-value">{activeSkill.source}</strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">范围</span>
                    <strong className="management-info-value">{activeSkill.scope}</strong>
                  </div>
                  <div className="management-info-card management-info-card-full">
                    <span className="management-info-label">根目录</span>
                    <strong className="management-info-value management-info-code">
                      {activeSkill.root_dir}
                    </strong>
                  </div>
                  <div className="management-info-card management-info-card-full">
                    <span className="management-info-label">入口文件</span>
                    <strong className="management-info-value management-info-code">
                      {activeSkill.entry_file}
                    </strong>
                  </div>
                </div>

                <section className="management-section-card management-section-card-compact">
                  <div className="management-section-header">
                    <h4 className="management-section-title">参数 Schema</h4>
                    <span className="management-status-badge tone-neutral">
                      {Object.keys(activeSkillParameterSchema).length > 0 ? "已提供" : "未提供"}
                    </span>
                  </div>

                  {Object.keys(activeSkillParameterSchema).length > 0 ? (
                    <pre className="management-code-block">
                      {stringifyJson(activeSkillParameterSchema)}
                    </pre>
                  ) : (
                    <div className="management-inline-notice">
                      该 Skill 当前没有声明 parameter schema。
                    </div>
                  )}
                </section>

                <section className="management-section-card management-section-card-compact">
                  <div className="management-section-header">
                    <h4 className="management-section-title">Skill Context</h4>
                    <span
                      className={`management-status-badge ${activeSkillContextEntry ? "tone-success" : "tone-neutral"}`}
                    >
                      {activeSkillContextEntry ? "已进入上下文" : "当前未进入"}
                    </span>
                  </div>

                  <div className="management-info-grid">
                    <div className="management-info-card">
                      <span className="management-info-label">上下文纳入条件</span>
                      <strong className="management-info-value">已加载且已启用</strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">已加载技能数</span>
                      <strong className="management-info-value">
                        {skillContextQuery.data?.payload.skills.length ?? 0}
                      </strong>
                    </div>
                  </div>

                  {activeSkillContextEntry ? (
                    <div className="management-subcard">
                      <span className="management-info-label">当前上下文条目</span>
                      <pre className="management-code-block">
                        {stringifyJson(activeSkillContextEntry)}
                      </pre>
                    </div>
                  ) : (
                    <div className="management-inline-notice">
                      该 Skill 未出现在 `/api/skills/skill-context`
                      结果里，通常表示它尚未启用、未加载或被忽略。
                    </div>
                  )}

                  {skillContextQuery.data ? (
                    <div className="management-subcard">
                      <span className="management-info-label">Prompt Fragment</span>
                      <pre className="management-code-block">
                        {skillContextQuery.data.prompt_fragment}
                      </pre>
                    </div>
                  ) : null}
                </section>

                  <section className="management-section-card management-section-card-compact">
                    <div className="management-section-header">
                      <h4 className="management-section-title">Skill Orchestration</h4>
                      <span className="management-status-badge tone-neutral">
                        {selectedOrchestrationSkills.length} 个 selected
                      </span>
                    </div>

                    <div className="management-info-grid">
                      <div className="management-info-card">
                        <span className="management-info-label">已选技能</span>
                        <strong className="management-info-value">
                          {selectedOrchestrationSkills.length}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">Prepared 技能</span>
                        <strong className="management-info-value">
                          {preparedOrchestrationSkills.length}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">Worker 结果</span>
                        <strong className="management-info-value">
                          {orchestrationWorkerResults.length}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">Node 结果</span>
                        <strong className="management-info-value">
                          {orchestrationNodeResults.length}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">当前 Stage</span>
                        <strong className="management-info-value">
                          {readFirstString(orchestrationPlan ?? {}, ["active_stage"]) ?? "-"}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">执行状态</span>
                        <strong className="management-info-value">
                          {readFirstString(orchestrationExecution ?? {}, ["status"]) ?? "-"}
                        </strong>
                      </div>
                    </div>

                    {selectedOrchestrationSkills.length > 0 ? (
                      <div className="management-subcard">
                        <span className="management-info-label">Selected Skills（含角色）</span>
                        <ul className="assistant-orchestration-list">
                          {selectedOrchestrationSkills.map((skill, index) => {
                            const role = readFirstString(skill, ["role"]);
                            const contextPrepared =
                              typeof skill["prepared_for_context"] === "boolean"
                                ? skill["prepared_for_context"]
                                : null;
                            const executionPrepared =
                              typeof skill["prepared_for_execution"] === "boolean"
                                ? skill["prepared_for_execution"]
                                : null;

                            return (
                              <li
                                key={`skill-orchestration-selected-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                                className="assistant-orchestration-list-item"
                              >
                                <div className="assistant-orchestration-item-head">
                                  <span className="assistant-tool-source-chip">
                                    {formatOrchestrationRole(role)}
                                  </span>
                                  <strong className="assistant-orchestration-item-title">
                                    {readSkillDisplayName(skill)}
                                  </strong>
                                </div>
                                <p className="assistant-tool-inline-meta">
                                  context: {contextPrepared === null ? "-" : contextPrepared ? "yes" : "no"}
                                  {" · "}
                                  execution:
                                  {" "}
                                  {executionPrepared === null ? "-" : executionPrepared ? "yes" : "no"}
                                </p>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ) : (
                      <div className="management-inline-notice">
                        当前 skill context 中暂无 selected_skills。
                      </div>
                    )}

                    {preparedOrchestrationSkills.length > 0 ? (
                      <div className="management-subcard">
                        <span className="management-info-label">Prepared Selected Skills</span>
                        <ul className="assistant-orchestration-list">
                          {preparedOrchestrationSkills.map((skill, index) => (
                            <li
                              key={`skill-orchestration-prepared-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                              className="assistant-orchestration-list-item"
                            >
                              <div className="assistant-orchestration-item-head">
                                <span className="assistant-tool-source-chip">
                                  {formatOrchestrationRole(readFirstString(skill, ["role"]))}
                                </span>
                                <strong className="assistant-orchestration-item-title">
                                  {readSkillDisplayName(skill)}
                                </strong>
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {orchestrationWorkerResults.length > 0 || orchestrationNodeResults.length > 0 ? (
                      <div className="management-subcard">
                        <span className="management-info-label">执行节点结果</span>
                        <ul className="assistant-orchestration-list">
                          {[...orchestrationWorkerResults, ...orchestrationNodeResults].map(
                            (result, index) => (
                              <li
                                key={`skill-orchestration-node-${String(result["step_id"] ?? result["name"] ?? index)}`}
                                className="assistant-orchestration-list-item"
                              >
                                <div className="assistant-orchestration-item-head">
                                  <span className="assistant-tool-source-chip">
                                    {formatOrchestrationRole(readFirstString(result, ["role"]))}
                                  </span>
                                  {readFirstString(result, ["status"]) ? (
                                    <span className="assistant-tool-source-chip">
                                      {readFirstString(result, ["status"])}
                                    </span>
                                  ) : null}
                                  <strong className="assistant-orchestration-item-title">
                                    {readFirstString(result, [
                                      "name",
                                      "directory_name",
                                      "skill_id",
                                      "step_id",
                                    ]) ?? "unknown"}
                                  </strong>
                                </div>
                                <p className="assistant-tool-inline-meta">
                                  {readFirstString(result, ["node_kind"]) ?? "node"}
                                  {" · "}
                                  {readFirstString(result, ["stage_name"]) ?? "stage:-"}
                                </p>
                              </li>
                            ),
                          )}
                        </ul>
                      </div>
                    ) : null}

                    {orchestrationStageTransition ? (
                      <div className="management-subcard">
                        <span className="management-info-label">Stage Transition</span>
                        <p className="assistant-tool-inline-meta">
                          {readFirstString(orchestrationStageTransition, ["from_stage"]) ?? "-"} →
                          {" "}
                          {readFirstString(orchestrationStageTransition, ["to_stage"]) ?? "-"}
                          {" · replan="}
                          {String(orchestrationStageTransition["replan_required"])}
                        </p>
                        {Array.isArray(orchestrationStageTransition["reasons"]) &&
                        orchestrationStageTransition["reasons"].length > 0 ? (
                          <p className="assistant-tool-inline-meta">
                            reasons: {(orchestrationStageTransition["reasons"] as unknown[])
                              .map((item) => String(item))
                              .join("; ")}
                          </p>
                        ) : null}
                      </div>
                    ) : null}

                    {replannedSkillContext ? (
                      <div className="management-subcard">
                        <span className="management-info-label">Replanned Skill Context</span>
                        {replannedSelectedSkills.length > 0 ? (
                          <ul className="assistant-orchestration-list">
                            {replannedSelectedSkills.map((skill, index) => (
                              <li
                                key={`skill-orchestration-replanned-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                                className="assistant-orchestration-list-item"
                              >
                                <div className="assistant-orchestration-item-head">
                                  <span className="assistant-tool-source-chip">
                                    {formatOrchestrationRole(readFirstString(skill, ["role"]))}
                                  </span>
                                  <strong className="assistant-orchestration-item-title">
                                    {readSkillDisplayName(skill)}
                                  </strong>
                                </div>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="assistant-tool-inline-meta">暂无 replanned selected skills。</p>
                        )}

                        {replannedSkillContext ? (
                          <pre className="management-code-block">
                            {stringifyJson(replannedSkillContext)}
                          </pre>
                        ) : null}
                      </div>
                    ) : null}
                  </section>

                {skillContentQuery.isLoading ? (
                  <div className="management-inline-notice">正在加载 SKILL.md。</div>
                ) : null}
                {skillContentQuery.isError ? (
                  <div className="management-error-banner">{skillContentQuery.error.message}</div>
                ) : null}

                {skillContentQuery.data ? (
                  <section className="management-section-card management-section-card-compact">
                    <div className="management-section-header">
                      <h4 className="management-section-title">SKILL.md</h4>
                      <span className="management-status-badge tone-neutral">
                        {activeSkill.directory_name}
                      </span>
                    </div>
                    <pre className="management-code-block">{skillContentQuery.data.content}</pre>
                  </section>
                ) : null}
              </div>
            </section>
          </div>,
          document.body,
        );

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="Skills 管理">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <h2 className="panel-title">Skills</h2>
            <p className="management-unified-description">扫描、筛选并维护当前可用的 Skill。</p>
          </div>

          <div className="management-action-row">
            <button
              className="button button-secondary"
              type="button"
              onClick={() => void scanMutation.mutateAsync()}
              disabled={
                scanMutation.isPending || rescanMutation.isPending || refreshMutation.isPending
              }
            >
              {scanMutation.isPending ? "扫描中" : "扫描目录"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => void rescanMutation.mutateAsync()}
              disabled={
                scanMutation.isPending || rescanMutation.isPending || refreshMutation.isPending
              }
            >
              {rescanMutation.isPending ? "重扫中" : "重新扫描"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => void refreshMutation.mutateAsync()}
              disabled={
                scanMutation.isPending || rescanMutation.isPending || refreshMutation.isPending
              }
            >
              {refreshMutation.isPending ? "刷新中" : "刷新列表"}
            </button>
          </div>
        </header>

        <div className="management-unified-body management-unified-stack">
          <div className="management-toolbar-row">
            <input
              className="management-search-input"
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="搜索名称、描述、目录或状态"
            />

            <span className="management-status-badge tone-neutral">{filteredCount} 项</span>
          </div>

          {mutationErrorMessage ? (
            <div className="management-error-banner">{mutationErrorMessage}</div>
          ) : null}

          {showGlobalOrchestrationOverview ? (
            <section className="management-section-card management-section-card-compact">
            <div className="management-section-header">
              <h3 className="management-section-title">多 Skill Orchestration 视图</h3>
              <span className="management-status-badge tone-neutral">
                {selectedOrchestrationSkills.length} 个 selected
              </span>
            </div>

            {skillContextQuery.isLoading ? (
              <div className="management-inline-notice">正在加载全局编排快照。</div>
            ) : null}
            {skillContextQuery.isError ? (
              <div className="management-error-banner">{skillContextQuery.error.message}</div>
            ) : null}

            {orchestrationSnapshot ? (
              <>
                <div className="management-info-grid">
                  <div className="management-info-card">
                    <span className="management-info-label">已选技能</span>
                    <strong className="management-info-value">{selectedOrchestrationSkills.length}</strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">Prepared 技能</span>
                    <strong className="management-info-value">{preparedOrchestrationSkills.length}</strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">Worker 结果</span>
                    <strong className="management-info-value">{orchestrationWorkerResults.length}</strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">Node 结果</span>
                    <strong className="management-info-value">{orchestrationNodeResults.length}</strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">当前 Stage</span>
                    <strong className="management-info-value">
                      {readFirstString(orchestrationPlan ?? {}, ["active_stage"]) ?? "-"}
                    </strong>
                  </div>
                  <div className="management-info-card">
                    <span className="management-info-label">执行状态</span>
                    <strong className="management-info-value">
                      {readFirstString(orchestrationExecution ?? {}, ["status"]) ?? "-"}
                    </strong>
                  </div>
                </div>

                {selectedOrchestrationSkills.length > 0 ? (
                  <div className="management-subcard">
                    <span className="management-info-label">Selected Skills（全量）</span>
                    <ul className="assistant-orchestration-list">
                      {selectedOrchestrationSkills.map((skill, index) => (
                        <li
                          key={`global-skill-orchestration-selected-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                          className="assistant-orchestration-list-item"
                        >
                          <div className="assistant-orchestration-item-head">
                            <span className="assistant-tool-source-chip">
                              {formatOrchestrationRole(readFirstString(skill, ["role"]))}
                            </span>
                            <strong className="assistant-orchestration-item-title">
                              {readSkillDisplayName(skill)}
                            </strong>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {preparedOrchestrationSkills.length > 0 ? (
                  <div className="management-subcard">
                    <span className="management-info-label">Prepared Selected Skills</span>
                    <ul className="assistant-orchestration-list">
                      {preparedOrchestrationSkills.map((skill, index) => (
                        <li
                          key={`global-skill-orchestration-prepared-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                          className="assistant-orchestration-list-item"
                        >
                          <div className="assistant-orchestration-item-head">
                            <span className="assistant-tool-source-chip">
                              {formatOrchestrationRole(readFirstString(skill, ["role"]))}
                            </span>
                            <strong className="assistant-orchestration-item-title">
                              {readSkillDisplayName(skill)}
                            </strong>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {orchestrationWorkerResults.length > 0 || orchestrationNodeResults.length > 0 ? (
                  <div className="management-subcard">
                    <span className="management-info-label">Worker / Node Results</span>
                    <ul className="assistant-orchestration-list">
                      {[...orchestrationWorkerResults, ...orchestrationNodeResults].map(
                        (result, index) => (
                          <li
                            key={`global-skill-orchestration-node-${String(result["step_id"] ?? result["name"] ?? index)}`}
                            className="assistant-orchestration-list-item"
                          >
                            <div className="assistant-orchestration-item-head">
                              <span className="assistant-tool-source-chip">
                                {formatOrchestrationRole(readFirstString(result, ["role"]))}
                              </span>
                              {readFirstString(result, ["status"]) ? (
                                <span className="assistant-tool-source-chip">
                                  {readFirstString(result, ["status"])}
                                </span>
                              ) : null}
                              <strong className="assistant-orchestration-item-title">
                                {readFirstString(result, [
                                  "name",
                                  "directory_name",
                                  "skill_id",
                                  "step_id",
                                ]) ?? "unknown"}
                              </strong>
                            </div>
                            <p className="assistant-tool-inline-meta">
                              {readFirstString(result, ["node_kind"]) ?? "node"}
                              {" · "}
                              {readFirstString(result, ["stage_name"]) ?? "stage:-"}
                            </p>
                          </li>
                        ),
                      )}
                    </ul>
                  </div>
                ) : null}

                {orchestrationStageTransition ? (
                  <div className="management-subcard">
                    <span className="management-info-label">Stage Transition</span>
                    <p className="assistant-tool-inline-meta">
                      {readFirstString(orchestrationStageTransition, ["from_stage"]) ?? "-"} →
                      {" "}
                      {readFirstString(orchestrationStageTransition, ["to_stage"]) ?? "-"}
                      {" · replan="}
                      {String(orchestrationStageTransition["replan_required"])}
                    </p>
                    {Array.isArray(orchestrationStageTransition["reasons"]) &&
                    orchestrationStageTransition["reasons"].length > 0 ? (
                      <p className="assistant-tool-inline-meta">
                        reasons: {(orchestrationStageTransition["reasons"] as unknown[])
                          .map((item) => String(item))
                          .join("; ")}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {replannedSkillContext ? (
                  <div className="management-subcard">
                    <span className="management-info-label">Replanned Skill Context</span>
                    {replannedSelectedSkills.length > 0 ? (
                      <ul className="assistant-orchestration-list">
                        {replannedSelectedSkills.map((skill, index) => (
                          <li
                            key={`global-skill-orchestration-replanned-${String(skill["id"] ?? skill["directory_name"] ?? index)}`}
                            className="assistant-orchestration-list-item"
                          >
                            <div className="assistant-orchestration-item-head">
                              <span className="assistant-tool-source-chip">
                                {formatOrchestrationRole(readFirstString(skill, ["role"]))}
                              </span>
                              <strong className="assistant-orchestration-item-title">
                                {readSkillDisplayName(skill)}
                              </strong>
                            </div>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="assistant-tool-inline-meta">暂无 replanned selected skills。</p>
                    )}
                  </div>
                ) : null}
              </>
            ) : !skillContextQuery.isLoading && !skillContextQuery.isError ? (
              <div className="management-inline-notice">当前没有可展示的 multi-skill orchestration 快照。</div>
            ) : null}
            </section>
          ) : null}

          {skillsQuery.isLoading && !activeSkill ? (
            <div className="management-empty-state management-empty-state-full">
              <p className="management-empty-title">准备 Skills 工作台</p>
              <p className="management-empty-copy">正在获取目录与详情。</p>
            </div>
          ) : skillsQuery.isError ? (
            <div className="management-empty-state management-empty-state-full">
              <p className="management-empty-title">当前无法展示详情</p>
              <p className="management-empty-copy">{skillsQuery.error.message}</p>
            </div>
          ) : (
            <section className="management-section-card management-section-card-compact">
              <div className="management-section-header">
                <h3 className="management-section-title">技能列表</h3>
                <span className="management-status-badge tone-neutral">{filteredCount} 项</span>
              </div>

              <div className="management-list-shell">
                {filteredSkills.length === 0 ? (
                  <div className="management-empty-state">
                    <p className="management-empty-title">没有匹配的 Skills</p>
                    <p className="management-empty-copy">试试更短的关键词，或重新扫描一次。</p>
                  </div>
                ) : (
                  <ul className="management-card-grid skills-card-grid">
                    {filteredSkills.map((skill) => {
                      const isActive = skill.id === selectedSkillId;

                      return (
                        <li key={skill.id}>
                          <SkillListCard
                            isActive={isActive}
                            onOpen={() => navigate(`/skills/${skill.id}`)}
                            skill={skill}
                          />
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </section>
          )}
        </div>
      </section>
      {detailContent}
    </main>
  );
}
