import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { getSkill, getSkillContent, listSkills, rescanSkills } from "../lib/api";
import { clampTextByLines } from "../lib/pretext";
import type { SkillRecord, SkillRecordStatus } from "../types/skills";

const SKILLS_QUERY_KEY = ["skills"] as const;
const UI_FONT_FAMILY = '"Segoe UI Variable", "Segoe UI", "Helvetica Neue", sans-serif';
const SKILL_TITLE_FONT = `600 15.36px ${UI_FONT_FAMILY}`;
const SKILL_DESCRIPTION_FONT = `400 14.08px ${UI_FONT_FAMILY}`;
const SKILL_TITLE_LINE_HEIGHT = 22;
const SKILL_DESCRIPTION_LINE_HEIGHT = 22;

function getInnerWidth(element: HTMLElement): number {
  const styles = window.getComputedStyle(element);
  const horizontalPadding = Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);

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
    () => clampTextByLines(description, SKILL_DESCRIPTION_FONT, width, SKILL_DESCRIPTION_LINE_HEIGHT, 3),
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
    </button>
  );
}

function buildSearchIndex(skill: SkillRecord): string {
  return [
    skill.name,
    skill.description,
    skill.status,
  ]
    .join(" ")
    .toLowerCase();
}

function getStatusCount(skills: SkillRecord[], status: SkillRecordStatus): number {
  return skills.filter((skill) => skill.status === status).length;
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

  const rescanMutation = useMutation({
    mutationFn: () => rescanSkills(),
    onSuccess: async (rescannedSkills) => {
      queryClient.setQueryData<SkillRecord[]>(SKILLS_QUERY_KEY, rescannedSkills);
      await queryClient.invalidateQueries({ queryKey: ["skills", "detail"] });
      await queryClient.invalidateQueries({ queryKey: ["skills", "content"] });

      if (selectedSkillId && !rescannedSkills.some((skill) => skill.id === selectedSkillId)) {
        const nextSkill = rescannedSkills[0];
        navigate(nextSkill ? `/skills/${nextSkill.id}` : "/skills", { replace: true });
      }
    },
  });

  const activeSkill = skillDetailQuery.data ?? activeSkillSummary;
  const totalCount = skillsQuery.data?.length ?? 0;
  const loadedCount = getStatusCount(skillsQuery.data ?? [], "loaded");
  const invalidCount = getStatusCount(skillsQuery.data ?? [], "invalid");
  const ignoredCount = getStatusCount(skillsQuery.data ?? [], "ignored");
  const filteredCount = filteredSkills.length;

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="Skills 管理">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <h2 className="panel-title">Skills</h2>
          </div>

          <button
            className="button button-secondary"
            type="button"
            onClick={() => void rescanMutation.mutateAsync()}
            disabled={rescanMutation.isPending}
          >
            {rescanMutation.isPending ? "重扫中" : "重新扫描"}
          </button>
        </header>

        <div className="management-metric-row management-metric-row-wide">
          <div className="management-metric-card">
            <span className="management-metric-label">总数</span>
            <strong className="management-metric-value">{totalCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">可用</span>
            <strong className="management-metric-value">{loadedCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">异常</span>
            <strong className="management-metric-value">{invalidCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">忽略</span>
            <strong className="management-metric-value">{ignoredCount}</strong>
          </div>
        </div>

        <div className="management-toolbar-row">
          <input
            className="management-search-input"
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="搜索名称、描述或状态"
          />

          <span className="management-status-badge tone-neutral">{filteredCount}/{totalCount} 个结果</span>
        </div>

        {rescanMutation.isError ? <div className="management-error-banner">{rescanMutation.error.message}</div> : null}

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
        ) : totalCount === 0 ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">还没有 Skills</p>
            <p className="management-empty-copy">执行一次重扫后，技能目录会展示在这里。</p>
          </div>
        ) : (
          <div className="management-unified-body management-unified-stack">
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
          </div>
        )}

        {selectedSkillId && activeSkill && typeof document !== "undefined"
          ? createPortal(
              <div className="management-modal-backdrop" role="presentation">
                <button
                  className="management-modal-dismiss"
                  type="button"
                  aria-label="关闭详情弹窗"
                  onClick={() => navigate("/skills")}
                />
                <section
                  className="management-modal-card panel"
                  role="dialog"
                  aria-modal="true"
                  aria-label={`${activeSkill.name} 详情`}
                >
                  <div className="management-modal-header">
                    <div className="management-detail-copy">
                      <h3 className="panel-title">{activeSkill.name}</h3>
                    </div>
                    <button className="button button-secondary" type="button" onClick={() => navigate("/skills")}>
                      关闭
                    </button>
                  </div>

                  {skillDetailQuery.isLoading ? <div className="management-inline-notice">正在加载详情。</div> : null}
                  {skillDetailQuery.isError ? <div className="management-error-banner">{skillDetailQuery.error.message}</div> : null}

                  <p className="skills-modal-description">{activeSkill.description || "暂无描述。"}</p>

                  {skillContentQuery.isLoading ? <div className="management-inline-notice">正在加载 SKILL.md。</div> : null}
                  {skillContentQuery.isError ? (
                    <div className="management-error-banner">{skillContentQuery.error.message}</div>
                  ) : null}

                  {skillContentQuery.data ? (
                    <section className="management-section-card management-section-card-compact">
                      <div className="management-section-header">
                        <h4 className="management-section-title">SKILL.md</h4>
                        <span className="management-status-badge tone-neutral">{activeSkill.directory_name}</span>
                      </div>
                      <pre
                        className="skills-modal-markdown"
                        style={{ margin: 0, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                      >
                        {skillContentQuery.data.content}
                      </pre>
                    </section>
                  ) : null}
                </section>
              </div>,
              document.body,
            )
          : null}
      </section>
    </main>
  );
}
