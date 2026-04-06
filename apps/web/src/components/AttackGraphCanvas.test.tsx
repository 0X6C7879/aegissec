import { describe, expect, it } from "vitest";
import type { SessionGraph } from "../types/graphs";
import { buildAutoLayout } from "./AttackGraphCanvas";
import {
  buildAttackGraphAutoFocusSignature,
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
        id: "task-1",
        graph_type: "attack",
        node_type: "task",
        label: "攻击面确认",
        data: {
          status: "completed",
        },
      },
      {
        id: "action-1",
        graph_type: "attack",
        node_type: "action",
        label: "验证链路",
        data: {
          status: "in_progress",
          current: true,
          updated_at: "2026-04-04T06:00:00.000Z",
        },
      },
    ],
    edges: [],
  };
}

describe("AttackGraphCanvas helpers", () => {
  it("prefers the active node over the latest node when choosing autofocus target", () => {
    expect(getAttackGraphAutoFocusNodeId(createGraph(), "task-1")).toBe("action-1");
  });

  it("stops auto focus once the user has interacted", () => {
    const signature = buildAttackGraphAutoFocusSignature(createGraph(), "action-1");

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

  it("builds compact node previews and keeps edge labels quiet by default", () => {
    const graph: SessionGraph = {
      ...createGraph(),
      nodes: [
        {
          id: "goal-1",
          graph_type: "attack",
          node_type: "goal",
          label: "拿到初始访问",
          data: {},
        },
        {
          id: "action-2",
          graph_type: "attack",
          node_type: "action",
          label: "执行命令",
          data: {
            command: "python exploit.py --target demo.internal --token very-long-token-value",
            observation_summary: "发现了非常长的一段观测文本，用来验证默认节点不会展示第二行摘录。",
          },
        },
      ],
      edges: [
        {
          id: "edge-1",
          graph_type: "attack",
          source: "goal-1",
          target: "action-2",
          relation: "discovers",
          data: {},
        },
      ],
    };

    const layout = buildAutoLayout(graph, null, null);
    const actionNode = layout.nodes.find((node) => node.id === "action-2");

    expect(actionNode?.data.label).toContain("python exploit.py");
    expect(actionNode?.data.label.length).toBeLessThan(60);
    expect(actionNode?.data.excerpt ?? null).toBeNull();
    expect(layout.edges[0]?.label).toBeUndefined();
  });

  it("shows relation labels for selected-path edges", () => {
    const graph = createGraph();
    graph.edges = [
      {
        id: "edge-1",
        graph_type: "attack",
        source: "task-1",
        target: "action-1",
        relation: "attempts",
        data: {},
      },
    ];

    const layout = buildAutoLayout(graph, "action-1", "action-1");

    expect(layout.edges[0]?.label).toBe("尝试");
  });

  it("shows relation labels for blocked paths even without selection", () => {
    const graph = createGraph();
    graph.nodes = [
      {
        id: "action-1",
        graph_type: "attack",
        node_type: "action",
        label: "执行动作",
        data: { status: "blocked" },
      },
      {
        id: "outcome-1",
        graph_type: "attack",
        node_type: "outcome",
        label: "结果节点",
        data: { status: "blocked" },
      },
    ];
    graph.edges = [
      {
        id: "edge-blocked",
        graph_type: "attack",
        source: "action-1",
        target: "outcome-1",
        relation: "blocks",
        data: {},
      },
    ];

    const layout = buildAutoLayout(graph, null, null);

    expect(layout.edges[0]?.label).toBe("阻断");
  });

  it("reveals relation labels when an edge is hovered", () => {
    const graph: SessionGraph = {
      ...createGraph(),
      nodes: [
        {
          id: "task-quiet",
          graph_type: "attack",
          node_type: "task",
          label: "静态入口",
          data: { status: "completed" },
        },
        {
          id: "action-quiet",
          graph_type: "attack",
          node_type: "action",
          label: "发现响应特征",
          data: { status: "completed" },
        },
      ],
      edges: [
        {
          id: "edge-hover",
          graph_type: "attack",
          source: "task-quiet",
          target: "action-quiet",
          relation: "discovers",
          data: {},
        },
      ],
    };

    const quietLayout = buildAutoLayout(graph, null, null);
    const hoveredLayout = buildAutoLayout(graph, null, null, "edge-hover");

    expect(quietLayout.edges[0]?.label).toBeUndefined();
    expect(hoveredLayout.edges[0]?.label).toBe("发现");
  });

  it("keeps the full ancestor and descendant path visible for a selected node", () => {
    const graph: SessionGraph = {
      ...createGraph(),
      nodes: [
        {
          id: "goal-1",
          graph_type: "attack",
          node_type: "goal",
          label: "目标",
          data: {},
        },
        {
          id: "task-1",
          graph_type: "attack",
          node_type: "task",
          label: "攻击面",
          data: {},
        },
        {
          id: "action-1",
          graph_type: "attack",
          node_type: "action",
          label: "验证链路",
          data: {},
        },
        {
          id: "outcome-1",
          graph_type: "attack",
          node_type: "outcome",
          label: "结果",
          data: {},
        },
        {
          id: "side-1",
          graph_type: "attack",
          node_type: "action",
          label: "旁支",
          data: {},
        },
      ],
      edges: [
        {
          id: "edge-goal-surface",
          graph_type: "attack",
          source: "goal-1",
          target: "task-1",
          relation: "attempts",
          data: {},
        },
        {
          id: "edge-task-action",
          graph_type: "attack",
          source: "task-1",
          target: "action-1",
          relation: "enables",
          data: {},
        },
        {
          id: "edge-action-outcome",
          graph_type: "attack",
          source: "action-1",
          target: "outcome-1",
          relation: "confirms",
          data: {},
        },
        {
          id: "edge-side-outcome",
          graph_type: "attack",
          source: "side-1",
          target: "outcome-1",
          relation: "confirms",
          data: {},
        },
      ],
    };

    const layout = buildAutoLayout(graph, "action-1", null);
    const nodeById = new Map(layout.nodes.map((node) => [node.id, node]));
    const edgeById = new Map(layout.edges.map((edge) => [edge.id, edge]));

    expect(nodeById.get("goal-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("task-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("action-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("outcome-1")?.data.isDimmed).toBe(false);
    expect(nodeById.get("side-1")?.data.isDimmed).toBe(true);
    expect(edgeById.get("edge-goal-surface")?.label).toBe("尝试");
    expect(edgeById.get("edge-task-action")?.label).toBe("使能");
    expect(edgeById.get("edge-action-outcome")?.label).toBe("确认");
    expect(edgeById.get("edge-side-outcome")?.style?.strokeOpacity).toBe(0.52);
  });

  it("handles cycles when expanding selected path context", () => {
    const graph: SessionGraph = {
      ...createGraph(),
      nodes: [
        {
          id: "task-1",
          graph_type: "attack",
          node_type: "task",
          label: "入口",
          data: {},
        },
        {
          id: "action-1",
          graph_type: "attack",
          node_type: "action",
          label: "利用",
          data: {},
        },
      ],
      edges: [
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
      ],
    };

    const layout = buildAutoLayout(graph, "action-1", null);

    expect(layout.nodes).toHaveLength(2);
    expect(layout.edges.find((edge) => edge.id === "edge-forward")?.label).toBe("使能");
    expect(layout.edges.find((edge) => edge.id === "edge-cycle")?.label).toBe("分支自");
  });
});
