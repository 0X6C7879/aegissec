import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SkillContent, SkillContext, SkillRecord } from "../types/skills";
import "../styles.css";
import { SkillsWorkbench } from "./SkillsWorkbench";

const {
  mockGetSkill,
  mockGetSkillContent,
  mockGetSkillContext,
  mockListSkills,
  mockRefreshSkills,
  mockRescanSkills,
  mockScanSkills,
  mockToggleSkill,
} = vi.hoisted(() => ({
  mockGetSkill: vi.fn(),
  mockGetSkillContent: vi.fn(),
  mockGetSkillContext: vi.fn(),
  mockListSkills: vi.fn(),
  mockRefreshSkills: vi.fn(),
  mockRescanSkills: vi.fn(),
  mockScanSkills: vi.fn(),
  mockToggleSkill: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");

  return {
    ...actual,
    getSkill: mockGetSkill,
    getSkillContent: mockGetSkillContent,
    getSkillContext: mockGetSkillContext,
    listSkills: mockListSkills,
    refreshSkills: mockRefreshSkills,
    rescanSkills: mockRescanSkills,
    scanSkills: mockScanSkills,
    toggleSkill: mockToggleSkill,
  };
});

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function buildSkillRecord(overrides: Partial<SkillRecord> = {}): SkillRecord {
  return {
    id: "ctf-web",
    source: "local",
    scope: "project",
    root_dir: "D:/skills",
    directory_name: "ctf-web",
    entry_file: "D:/skills/ctf-web/SKILL.md",
    name: "ctf-web",
    description: "Web exploitation dispatcher",
    compatibility: ["windows", "linux"],
    metadata: {},
    parameter_schema: {},
    raw_frontmatter: {},
    status: "loaded",
    enabled: true,
    error_message: null,
    content_hash: "hash-1",
    last_scanned_at: "2026-04-12T10:00:00.000Z",
    ...overrides,
  };
}

function buildSkillContent(overrides: Partial<SkillContent> = {}): SkillContent {
  return {
    id: "ctf-web",
    name: "ctf-web",
    directory_name: "ctf-web",
    entry_file: "D:/skills/ctf-web/SKILL.md",
    parameter_schema: {},
    content: "# ctf-web\nUse this for web challenges.",
    ...overrides,
  };
}

function buildSkillContext(): SkillContext {
  return {
    payload: {
      skills: [
        {
          id: "ctf-web",
          name: "ctf-web",
          directory_name: "ctf-web",
          description: "Web exploitation dispatcher",
          compatibility: ["windows", "linux"],
          parameter_schema: {},
        },
      ],
      selected_skills: [
        { id: "ctf-web", directory_name: "ctf-web", role: "primary" },
        { id: "recon-web", directory_name: "recon-web", role: "supporting" },
        { id: "reference-doc", directory_name: "reference-doc", role: "reference" },
      ],
      prepared_selected_skills: [
        {
          id: "ctf-web",
          directory_name: "ctf-web",
          role: "primary",
          prepared_for_context: true,
          prepared_for_execution: true,
        },
        {
          id: "recon-web",
          directory_name: "recon-web",
          role: "supporting",
          prepared_for_context: true,
          prepared_for_execution: false,
        },
      ],
      skill_orchestration_plan: {
        active_stage: "exploit",
        stages: [
          {
            stage_name: "exploit",
            mode: "primary_with_parallel_supporting",
            failure_policy: "best_effort",
            steps: [
              { step_id: "primary-1", name: "ctf-web", role: "primary" },
              {
                step_id: "worker-1",
                name: "recon-web",
                role: "supporting",
                node_kind: "worker",
              },
              {
                step_id: "reduce-1",
                name: "Result Reducer",
                role: "reducer",
                node_kind: "reducer",
              },
              {
                step_id: "verify-1",
                name: "Result Verifier",
                role: "verifier",
                node_kind: "verifier",
              },
            ],
          },
        ],
      },
      skill_orchestration_execution: {
        status: "completed",
        duration_ms: 3900,
        worker_results: [
          {
            step_id: "worker-1",
            name: "recon-web",
            role: "supporting",
            status: "succeeded",
            stage_name: "exploit",
            node_kind: "worker",
          },
        ],
        node_results: [
          {
            step_id: "reduce-1",
            name: "Result Reducer",
            role: "reducer",
            status: "succeeded",
            stage_name: "exploit",
            node_kind: "reducer",
          },
          {
            step_id: "verify-1",
            name: "Result Verifier",
            role: "verifier",
            status: "succeeded",
            stage_name: "exploit",
            node_kind: "verifier",
          },
        ],
        stage_transition: {
          from_stage: "exploit",
          to_stage: "post-exploit",
          replan_required: true,
          reasons: ["需要后渗透上下文"],
        },
      },
      skill_stage_transition: {
        from_stage: "exploit",
        to_stage: "post-exploit",
        replan_required: true,
        reasons: ["需要后渗透上下文"],
      },
      replanned_skill_context: {
        selected_skills: [
          {
            id: "post-exploitation",
            directory_name: "post-exploitation",
            role: "primary",
          },
        ],
        skill_orchestration_plan: {
          active_stage: "post-exploit",
        },
      },
    },
    prompt_fragment: "Use ctf-web with recon-web and verifier/reducer nodes.",
  };
}

function renderWorkbench(initialPath = "/skills/ctf-web") {
  const queryClient = createQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/skills" element={<SkillsWorkbench />} />
          <Route path="/skills/:skillId" element={<SkillsWorkbench />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SkillsWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    const skillRecord = buildSkillRecord();

    mockListSkills.mockResolvedValue([skillRecord]);
    mockGetSkill.mockResolvedValue(skillRecord);
    mockGetSkillContent.mockResolvedValue(buildSkillContent());
    mockGetSkillContext.mockResolvedValue(buildSkillContext());
    mockScanSkills.mockResolvedValue([skillRecord]);
    mockRescanSkills.mockResolvedValue([skillRecord]);
    mockRefreshSkills.mockResolvedValue([skillRecord]);
    mockToggleSkill.mockResolvedValue(skillRecord);
  });

  it("shows complete multi-skill orchestration details in the skill detail modal", async () => {
    renderWorkbench();

    const dialog = await screen.findByRole("dialog", { name: "ctf-web 详情" });

    expect(within(dialog).getByText("Skill Orchestration")).toBeInTheDocument();
    expect(within(dialog).getByText("Selected Skills（含角色）")).toBeInTheDocument();
    expect(within(dialog).getAllByText("ctf-web").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText("recon-web").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText("Result Reducer").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText("Result Verifier").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Stage Transition")).toBeInTheDocument();
    expect(within(dialog).getAllByText(/post-exploit/).length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText("post-exploitation").length).toBeGreaterThan(0);
  });
});
