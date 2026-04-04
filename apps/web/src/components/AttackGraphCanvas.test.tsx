import { describe, expect, it } from "vitest";
import type { SessionGraph } from "../types/graphs";
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
});
