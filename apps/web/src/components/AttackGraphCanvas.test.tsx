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
        id: "surface-1",
        graph_type: "attack",
        node_type: "surface",
        label: "攻击面",
        data: {
          status: "completed",
        },
      },
      {
        id: "exploit-1",
        graph_type: "attack",
        node_type: "exploit",
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
    expect(getAttackGraphAutoFocusNodeId(createGraph(), "surface-1")).toBe("exploit-1");
  });

  it("stops auto focus once the user has interacted", () => {
    const signature = buildAttackGraphAutoFocusSignature(createGraph(), "exploit-1");

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
          id: "observation-1",
          graph_type: "attack",
          node_type: "observation",
          label: "原始观测",
          data: {
            summary: "发现了非常长的一段观测文本，用来验证默认节点不会展示整段摘要，而只保留一行短摘录。",
          },
        },
      ],
      edges: [
        {
          id: "edge-1",
          graph_type: "attack",
          source: "goal-1",
          target: "observation-1",
          relation: "discovers",
          data: {},
        },
      ],
    };

    const layout = buildAutoLayout(graph, null, null);
    const observationNode = layout.nodes.find((node) => node.id === "observation-1");

    expect(observationNode?.data.label).toContain("发现了非常长的一段观测文本");
    expect(observationNode?.data.label.length).toBeLessThan(60);
    expect(observationNode?.data.excerpt ?? null).toBeNull();
    expect(layout.edges[0]?.label).toBeUndefined();
  });

  it("shows relation labels for selected-path edges", () => {
    const graph = createGraph();
    graph.edges = [
      {
        id: "edge-1",
        graph_type: "attack",
        source: "surface-1",
        target: "exploit-1",
        relation: "attempts",
        data: {},
      },
    ];

    const layout = buildAutoLayout(graph, "exploit-1", "exploit-1");

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
          id: "surface-quiet",
          graph_type: "attack",
          node_type: "surface",
          label: "静态入口",
          data: { status: "completed" },
        },
        {
          id: "observation-quiet",
          graph_type: "attack",
          node_type: "observation",
          label: "发现响应特征",
          data: { status: "completed" },
        },
      ],
      edges: [
        {
          id: "edge-hover",
          graph_type: "attack",
          source: "surface-quiet",
          target: "observation-quiet",
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
});
