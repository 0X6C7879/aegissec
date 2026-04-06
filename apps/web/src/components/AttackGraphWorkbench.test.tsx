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
    onSelectNode: (nodeId: string | null) => void;
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
        node_type: "action",
        label: "无锚点节点",
        data: {
          status: "completed",
          observation_summary: "没有 source_message_id。",
        },
      },
      {
        id: "node-with-anchor",
        graph_type: "attack",
        node_type: "action",
        label: "可操作节点",
        data: {
          status: "in_progress",
          summary: "命令执行后确认存在可利用入口。",
          command: "nmap -sV target.internal",
          tool_name: "nmap",
          request_summary: "确认可利用入口",
          observation_summary: "识别到开放端口与服务版本。",
          related_findings: [
            {
              title: "开放端口",
              summary: "22/tcp 与 443/tcp 对外可达",
            },
          ],
          related_hypotheses: [
            {
              summary: "SSH 可能存在弱口令入口",
              status: "open",
            },
          ],
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
  onRegenerateNode = vi.fn().mockResolvedValue(undefined),
  onForkNode = vi.fn().mockResolvedValue(undefined),
  onRollbackNode = vi.fn().mockResolvedValue(undefined),
}: {
  onEditNode?: ReturnType<typeof vi.fn>;
  onRegenerateNode?: ReturnType<typeof vi.fn>;
  onForkNode?: ReturnType<typeof vi.fn>;
  onRollbackNode?: ReturnType<typeof vi.fn>;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  return (
    <AttackGraphWorkbench
      graph={createGraph()}
      selectedNodeId={selectedNodeId}
      actionBusyId={null}
      onSelectNode={setSelectedNodeId}
      onEditNode={onEditNode}
      onRegenerateNode={onRegenerateNode}
      onForkNode={onForkNode}
      onRollbackNode={onRollbackNode}
    />
  );
}

describe("AttackGraphWorkbench", () => {
  it("opens node details in a dialog and shows the exact missing-anchor hint", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);

    await user.click(screen.getByRole("button", { name: "无锚点节点" }));

    const dialog = screen.getByRole("dialog", { name: "无锚点节点 详情" });

    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("该节点缺少会话锚点，无法直接操作对话")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重生成" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "分叉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "回滚" })).toBeDisabled();
  });

  it("enables node actions when source_message_id exists and closes via close button", async () => {
    const user = userEvent.setup();
    const onEditNode = vi.fn().mockResolvedValue(undefined);
    const onRegenerateNode = vi.fn().mockResolvedValue(undefined);
    const onForkNode = vi.fn().mockResolvedValue(undefined);
    const onRollbackNode = vi.fn().mockResolvedValue(undefined);

    render(
      <StatefulAttackGraphWorkbench
        onEditNode={onEditNode}
        onRegenerateNode={onRegenerateNode}
        onForkNode={onForkNode}
        onRollbackNode={onRollbackNode}
      />,
    );

    await user.click(screen.getByRole("button", { name: "可操作节点" }));
    expect(screen.getByRole("dialog", { name: "可操作节点 详情" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "重生成" }));
    await user.click(screen.getByRole("button", { name: "分叉" }));
    await user.click(screen.getByRole("button", { name: "回滚" }));
    await user.click(screen.getByRole("button", { name: "关闭" }));

    expect(screen.queryByText("该节点缺少会话锚点，无法直接操作对话")).not.toBeInTheDocument();
    expect(onEditNode).toHaveBeenCalledTimes(1);
    expect(onRegenerateNode).toHaveBeenCalledTimes(1);
    expect(onForkNode).toHaveBeenCalledTimes(1);
    expect(onRollbackNode).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog", { name: "可操作节点 详情" })).not.toBeInTheDocument();
  });

  it("keeps debug metadata hidden behind advanced information by default", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);

    await user.click(screen.getByRole("button", { name: "可操作节点" }));

    expect(screen.getByText("会话动作")).toBeInTheDocument();
    expect(screen.getByText("Observation")).toBeInTheDocument();
    expect(screen.getByText("Interpretation")).toBeInTheDocument();
    expect(screen.getByText("source_message_id")).not.toBeVisible();
    expect(screen.getByText("branch_id")).not.toBeVisible();
    expect(screen.getByText("generation_id")).not.toBeVisible();

    await user.click(screen.getByText("高级信息"));

    expect(screen.getByText("source_message_id")).toBeVisible();
    expect(screen.getByText("branch_id")).toBeVisible();
    expect(screen.getByText("generation_id")).toBeVisible();
  });

  it("renders execution-first detail sections in order", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);

    await user.click(screen.getByRole("button", { name: "可操作节点" }));

    expect(screen.getByText("Basic")).toBeInTheDocument();
    expect(screen.getByText("Why")).toBeInTheDocument();
    expect(screen.getByText("Action")).toBeInTheDocument();
    expect(screen.getByText("Observation")).toBeInTheDocument();
    expect(screen.getByText("Interpretation")).toBeInTheDocument();
    expect(screen.getByText("命令")).toBeInTheDocument();
    expect(screen.getAllByText("工具").length).toBeGreaterThan(0);
    expect(screen.getByText("观测摘要")).toBeInTheDocument();
    expect(screen.getByText("发现 1")).toBeInTheDocument();
    expect(screen.getByText("假设 1")).toBeInTheDocument();
    expect(screen.queryByText("活跃节点")).not.toBeInTheDocument();
    expect(screen.queryByText("当前节点没有额外的高价值展示内容。")).not.toBeInTheDocument();

    const overviewHeading = screen.getByText("Basic");
    const highValueHeading = screen.getByText("Why");
    const actionsHeading = screen.getByText("会话动作");

    const overviewPosition = overviewHeading.compareDocumentPosition(highValueHeading);
    const highValuePosition = highValueHeading.compareDocumentPosition(actionsHeading);

    expect(overviewPosition & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(highValuePosition & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
