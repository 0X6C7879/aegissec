import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SessionGraph } from "../types/graphs";
import { AttackGraphWorkbench } from "./AttackGraphWorkbench";

vi.mock("./AttackGraphCanvas", () => ({
  AttackGraphCanvas: ({
    graph,
    onSelectNode,
  }: {
    graph: SessionGraph;
    onSelectNode: (nodeId: string) => void;
  }) => (
    <div data-testid="attack-graph-canvas-mock">
      {graph.nodes.map((node) => (
        <button key={node.id} type="button" onClick={() => onSelectNode(node.id)}>
          {node.label}
        </button>
      ))}
    </div>
  ),
}));

function createGraph(): SessionGraph {
  return {
    session_id: "session-1",
    workflow_run_id: "run-1",
    graph_type: "attack",
    current_stage: "safe_validation",
    nodes: [
      {
        id: "node-without-anchor",
        graph_type: "attack",
        node_type: "observation",
        label: "无锚点节点",
        data: {
          status: "completed",
          summary: "没有 source_message_id。",
        },
      },
      {
        id: "node-with-anchor",
        graph_type: "attack",
        node_type: "exploit",
        label: "可操作节点",
        data: {
          status: "in_progress",
          summary: "可以映射到会话消息。",
          source_message_id: "message-1",
          branch_id: "branch-1",
          generation_id: "generation-1",
        },
      },
    ],
    edges: [],
  };
}

function StatefulAttackGraphWorkbench({
  onEditNode = vi.fn().mockResolvedValue(undefined),
}: {
  onEditNode?: ReturnType<typeof vi.fn>;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  return (
    <AttackGraphWorkbench
      graph={createGraph()}
      selectedNodeId={selectedNodeId}
      actionBusyId={null}
      onSelectNode={setSelectedNodeId}
      onEditNode={onEditNode}
      onRegenerateNode={vi.fn().mockResolvedValue(undefined)}
      onForkNode={vi.fn().mockResolvedValue(undefined)}
      onRollbackNode={vi.fn().mockResolvedValue(undefined)}
    />
  );
}

describe("AttackGraphWorkbench", () => {
  it("disables node actions and shows the exact missing-anchor hint", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);

    await user.click(screen.getByRole("button", { name: "无锚点节点" }));

    expect(screen.getByText("该节点缺少会话锚点，无法直接操作对话")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重生成" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "分叉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "回滚" })).toBeDisabled();
  });

  it("enables node actions when source_message_id exists", async () => {
    const user = userEvent.setup();
    const onEditNode = vi.fn().mockResolvedValue(undefined);

    render(<StatefulAttackGraphWorkbench onEditNode={onEditNode} />);

    await user.click(screen.getByRole("button", { name: "可操作节点" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));

    expect(screen.queryByText("该节点缺少会话锚点，无法直接操作对话")).not.toBeInTheDocument();
    expect(onEditNode).toHaveBeenCalledTimes(1);
  });
});
