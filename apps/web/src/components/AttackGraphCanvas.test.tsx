import { describe, expect, it } from "vitest";
import type { SessionGraph } from "../types/graphs";
import { buildAutoLayout } from "./AttackGraphCanvas";
import {
  buildAttackGraphAutoFocusSignature,
  chooseRepresentativeMilestoneChain,
  displayExcerpt,
  displayImportance,
  displayTitle,
  formatAttackNodeType,
  getAttackGraphAutoFocusNodeId,
  shouldAutoFocusAttackGraph,
} from "./AttackGraphCanvas.utils";

function createGraph(): SessionGraph {
  return {
    session_id: "session-1",
    workflow_run_id: "run-1",
    graph_type: "attack",
    current_stage: "safe_validation",
    nodes: [
      {
        id: "root-1",
        graph_type: "attack",
        node_type: "root",
        label: "授权目标",
        data: {
          goal: "优先查看当前最佳工作链。",
          best_path_summary: "验证主路径仍在推进。",
        },
      },
      {
        id: "task-1",
        graph_type: "attack",
        node_type: "task",
        label: "验证主路径",
        data: {
          status: "completed",
          title: "验证主路径",
        },
      },
      {
        id: "task-2",
        graph_type: "attack",
        node_type: "task",
        label: "旁支任务",
        data: {
          status: "completed",
          title: "旁支任务",
        },
      },
      {
        id: "action-1",
        graph_type: "attack",
        node_type: "action",
        label: "入口探测",
        data: {
          task_id: "task-1",
          status: "completed",
          command: "curl -s -I https://target.internal/health",
          observation_summary: "确认健康检查入口可访问。",
          updated_at: "2026-04-04T05:00:00.000Z",
        },
      },
      {
        id: "action-2",
        graph_type: "attack",
        node_type: "action",
        label: "关键验证",
        data: {
          task_id: "task-1",
          status: "in_progress",
          current: true,
          summary: "验证当前 exploit 前置条件。",
          observation_summary: "发现后台管理面仍然暴露。",
          related_findings: [{ title: "后台入口" }],
          updated_at: "2026-04-04T06:00:00.000Z",
        },
      },
      {
        id: "action-side",
        graph_type: "attack",
        node_type: "action",
        label: "历史旁支",
        data: {
          task_id: "task-2",
          status: "completed",
          command: "curl -s https://target.internal/debug",
          observation_summary: "只返回静态横幅。",
          updated_at: "2026-04-04T07:00:00.000Z",
        },
      },
      {
        id: "outcome-1",
        graph_type: "attack",
        node_type: "outcome",
        label: "当前结论",
        data: {
          status: "blocked",
          content: "主路径尚未完成，但阻断点已确认。",
        },
      },
    ],
    edges: [
      {
        id: "edge-root-task-1",
        graph_type: "attack",
        source: "root-1",
        target: "task-1",
        relation: "attempts",
        data: {},
      },
      {
        id: "edge-root-task-2",
        graph_type: "attack",
        source: "root-1",
        target: "task-2",
        relation: "attempts",
        data: {},
      },
      {
        id: "edge-task-1-action-1",
        graph_type: "attack",
        source: "task-1",
        target: "action-1",
        relation: "enables",
        data: {},
      },
      {
        id: "edge-action-1-action-2",
        graph_type: "attack",
        source: "action-1",
        target: "action-2",
        relation: "precedes",
        data: {},
      },
      {
        id: "edge-action-2-outcome",
        graph_type: "attack",
        source: "action-2",
        target: "outcome-1",
        relation: "blocks",
        data: {},
      },
      {
        id: "edge-task-2-side",
        graph_type: "attack",
        source: "task-2",
        target: "action-side",
        relation: "enables",
        data: {},
      },
      {
        id: "edge-side-outcome",
        graph_type: "attack",
        source: "action-side",
        target: "outcome-1",
        relation: "confirms",
        data: {},
      },
    ],
  };
}

describe("AttackGraphCanvas helpers", () => {
  it("prefers the active node over the latest node when choosing autofocus target", () => {
    expect(getAttackGraphAutoFocusNodeId(createGraph(), "action-side")).toBe("action-2");
  });

  it("stops auto focus once the user has interacted", () => {
    const signature = buildAttackGraphAutoFocusSignature(createGraph(), "action-2");

    expect(
      shouldAutoFocusAttackGraph({
        hasUserInteracted: false,
        nextSignature: signature,
        previousSignature: null,
      }),
    ).toBe(true);

    expect(
      shouldAutoFocusAttackGraph({
        hasUserInteracted: true,
        nextSignature: signature,
        previousSignature: null,
      }),
    ).toBe(false);
  });

  it("prioritizes command-oriented action copy for compact previews", () => {
    const commandNode = createGraph().nodes.find((node) => node.id === "action-1")!;

    expect(displayTitle(commandNode)).toContain("curl -s -I");
    expect(displayExcerpt(commandNode)).toBe("确认健康检查入口可访问。");
  });

  it("keeps execution graph emphasis tied to milestone value instead of legacy node types", () => {
    const blockedAction = createGraph().nodes.find((node) => node.id === "action-2")!;
    const semanticNode = {
      id: "legacy-1",
      graph_type: "attack",
      node_type: "finding",
      label: "Legacy",
      data: { status: "completed" },
    } satisfies SessionGraph["nodes"][number];

    expect(displayImportance(blockedAction)).toBe("critical");
    expect(displayImportance(semanticNode)).toBe("supporting");
    expect(formatAttackNodeType("root")).toBe("目标");
  });

  it("default highlight prefers the best execution chain instead of a generic neighborhood", () => {
    const layout = buildAutoLayout(createGraph(), null, "action-side");
    const nodeById = new Map(layout.nodes.map((node) => [node.id, node]));
    const edgeById = new Map(layout.edges.map((edge) => [edge.id, edge]));

    expect(nodeById.get("root-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("task-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("action-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("action-2")?.data.isDimmed).toBe(false);
    expect(nodeById.get("outcome-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("task-2")?.data.isDimmed).toBe(true);
    expect(nodeById.get("action-side")?.data.isDimmed).toBe(true);
    expect(edgeById.get("edge-task-1-action-1")?.label).toBe("使能");
    expect(edgeById.get("edge-action-1-action-2")?.label).toBe("推进");
    expect(edgeById.get("edge-action-2-outcome")?.label).toBe("阻断");
    expect(edgeById.get("edge-side-outcome")?.style?.strokeOpacity).toBe(0.52);
  });

  it("selected task highlights only its representative milestone chain", () => {
    const graph = createGraph();
    const chain = chooseRepresentativeMilestoneChain(graph, "task-1");
    const layout = buildAutoLayout(graph, "task-1", null);
    const nodeById = new Map(layout.nodes.map((node) => [node.id, node]));

    expect(chain).toEqual(["root-1", "task-1", "action-1", "action-2", "outcome-1"]);
    expect(nodeById.get("action-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("action-2")?.data.isDimmed).toBe(false);
    expect(nodeById.get("action-side")?.data.isDimmed).toBe(true);
  });

  it("handles cycles while keeping the representative milestone chain stable", () => {
    const graph = createGraph();
    graph.nodes = [
      {
        id: "task-1",
        graph_type: "attack",
        node_type: "task",
        label: "入口任务",
        data: {},
      },
      {
        id: "action-1",
        graph_type: "attack",
        node_type: "action",
        label: "入口验证",
        data: {
          status: "in_progress",
        },
      },
    ];
    graph.edges = [
      {
        id: "edge-forward",
        graph_type: "attack",
        source: "task-1",
        target: "action-1",
        relation: "enables",
        data: {},
      },
      {
        id: "edge-cycle",
        graph_type: "attack",
        source: "action-1",
        target: "task-1",
        relation: "branches_from",
        data: {},
      },
    ];

    const layout = buildAutoLayout(graph, "action-1", null);

    expect(layout.nodes).toHaveLength(2);
    expect(layout.edges.find((edge) => edge.id === "edge-forward")?.label).toBe("使能");
    expect(layout.edges.find((edge) => edge.id === "edge-cycle")?.style?.strokeOpacity).toBe(0.52);
  });
});
