import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SessionGraph } from "../types/graphs";
import { AttackGraphWorkbench } from "./AttackGraphWorkbench";

vi.mock("./AttackGraphCanvas", () => ({
  AttackGraphCanvas: ({
    graph,
    latestNodeId,
    onSelectNode,
  }: {
    graph: SessionGraph;
    latestNodeId: string | null;
    onSelectNode: (nodeId: string | null) => void;
  }) => (
    <div data-testid="attack-graph-canvas-mock">
      <output data-testid="attack-graph-focus-node">{latestNodeId ?? "none"}</output>
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
        id: "root-node",
        graph_type: "attack",
        node_type: "root",
        label: "授权验证目标",
        data: {
          goal: "确认授权范围内的主工作链与当前阻断点。",
          current_stage: "safe_validation",
          best_path_summary: "优先核实验证动作与阻断原因。",
          status: "running",
          session_id: "session-1",
          run_id: "run-1",
        },
      },
      {
        id: "task-node",
        graph_type: "attack",
        node_type: "task",
        label: "验证主路径",
        data: {
          title: "验证主路径",
          task_name: "safe_validation.validate_primary_path",
          summary: "确认低风险验证链条是否能够继续推进。",
          key_observation_summary: "入口与认证边界已经定位。",
          blocker: "需要额外审批才能继续执行高风险探测。",
          next_step: "保留当前证据并准备审批材料。",
          status: "completed",
        },
      },
      {
        id: "command-node",
        graph_type: "attack",
        node_type: "action",
        label: "探测入口命令",
        data: {
          status: "in_progress",
          summary: "命令执行后确认存在可利用入口。",
          command: "nmap -sV target.internal",
          tool_name: "shell_execute",
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
      {
        id: "analysis-node",
        graph_type: "attack",
        node_type: "action",
        label: "研判认证旁路",
        data: {
          status: "blocked",
          summary: "认证旁路假设需要更多证据支持。",
          thought: "先确认 token 校验顺序是否允许中间件旁路。",
          observation_summary: "中间件顺序异常，但还没有形成稳定利用链。",
          blocked_reason: "缺少足够上下文来确认是否可以继续 pivot。",
          related_findings: [
            {
              title: "中间件顺序异常",
              summary: "认证校验在路由处理后执行。",
            },
          ],
          related_hypotheses: [
            {
              summary: "请求可能绕过部分认证检查",
              status: "open",
            },
          ],
        },
      },
      {
        id: "outcome-node",
        graph_type: "attack",
        node_type: "outcome",
        label: "当前阶段结论",
        data: {
          status: "blocked",
          content: "当前工作链已经定位阻断点，但 exploit 尚未确认。",
          supporting_actions: ["nmap -sV target.internal", "研判认证旁路"],
        },
      },
      {
        id: "anchorless-node",
        graph_type: "attack",
        node_type: "action",
        label: "无锚点命令",
        data: {
          status: "completed",
          command: "curl -s https://target.internal/health",
          observation_summary: "健康检查接口返回 200。",
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
  it("passes the best-path anchor to the canvas instead of the most recent node", () => {
    const graph = createGraph();
    const rootNode = graph.nodes.find((node) => node.id === "root-node")!;
    const taskNode = graph.nodes.find((node) => node.id === "task-node")!;
    const commandNode = graph.nodes.find((node) => node.id === "command-node")!;
    const anchorlessNode = graph.nodes.find((node) => node.id === "anchorless-node")!;

    rootNode.data.best_path_summary = "探测入口命令";
    taskNode.data.current_action_summary = "探测入口命令";
    commandNode.data.status = "completed";
    commandNode.data.current = false;
    commandNode.data.collaboration_value = 92;
    commandNode.data.milestone_reasons = ["outcome", "finding"];
    commandNode.data.updated_at = "2026-04-04T05:00:00.000Z";
    anchorlessNode.data.updated_at = "2026-04-04T09:00:00.000Z";

    render(
      <AttackGraphWorkbench
        graph={graph}
        selectedNodeId={null}
        actionBusyId={null}
        onSelectNode={vi.fn()}
        onEditNode={vi.fn().mockResolvedValue(undefined)}
        onRegenerateNode={vi.fn().mockResolvedValue(undefined)}
        onForkNode={vi.fn().mockResolvedValue(undefined)}
        onRollbackNode={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByTestId("attack-graph-focus-node")).toHaveTextContent("command-node");
  });

  it("command action detail defaults to command and observation summary only", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);
    await user.click(screen.getByRole("button", { name: "探测入口命令" }));

    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getAllByText("Command").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Observation").length).toBeGreaterThan(0);
    expect(screen.getByText("nmap -sV target.internal")).toBeInTheDocument();
    expect(screen.getAllByText("识别到开放端口与服务版本。").length).toBeGreaterThan(0);
    expect(screen.queryByText("Why")).not.toBeInTheDocument();
    expect(screen.queryByText("Interpretation")).not.toBeInTheDocument();
  });

  it("command action debug metadata is hidden behind collapsed advanced disclosure", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);
    await user.click(screen.getByRole("button", { name: "探测入口命令" }));

    expect(screen.getByText("高级信息")).toBeInTheDocument();
    expect(screen.getByText("source_message_id")).not.toBeVisible();
    expect(screen.getByText("branch_id")).not.toBeVisible();
    expect(screen.getByText("generation_id")).not.toBeVisible();

    await user.click(screen.getByText("高级信息"));

    expect(screen.getByText("source_message_id")).toBeVisible();
    expect(screen.getByText("branch_id")).toBeVisible();
    expect(screen.getByText("generation_id")).toBeVisible();
  });

  it("non-command nodes render concise milestone summaries instead of field dumps", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);

    await user.click(screen.getByRole("button", { name: "授权验证目标" }));
    expect(screen.getByText("Goal")).toBeInTheDocument();
    expect(screen.getByText("优先核实验证动作与阻断原因。")).toBeInTheDocument();
    expect(screen.queryByText("Command")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭" }));
    await user.click(screen.getByRole("button", { name: "验证主路径" }));
    expect(screen.getByText("Focus")).toBeInTheDocument();
    expect(screen.getByText("保留当前证据并准备审批材料。")).toBeInTheDocument();
    expect(screen.queryByText("Command")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭" }));
    await user.click(screen.getByRole("button", { name: "研判认证旁路" }));
    expect(screen.getAllByText("Why").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Observation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Interpretation").length).toBeGreaterThan(0);
    expect(screen.queryByText("Command")).not.toBeInTheDocument();
  });

  it("conversation controls stay collapsed and show the missing-anchor hint only when opened", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);
    await user.click(screen.getByRole("button", { name: "无锚点命令" }));

    expect(screen.getByText("会话动作")).toBeInTheDocument();
    expect(screen.queryByText("该节点缺少会话锚点，无法直接对话操作。")).not.toBeVisible();

    await user.click(screen.getByText("会话动作"));

    expect(screen.getByText("该节点缺少会话锚点，无法直接对话操作。")).toBeVisible();
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新生成" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "分叉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "回滚" })).toBeDisabled();
  });

  it("raw payload stays collapsed until the user expands it", async () => {
    const user = userEvent.setup();

    render(<StatefulAttackGraphWorkbench />);
    await user.click(screen.getByRole("button", { name: "探测入口命令" }));

    expect(screen.getByText('"command": "nmap -sV target.internal"', { exact: false })).not.toBeVisible();

    await user.click(screen.getByText("Raw payload"));

    expect(screen.getByText('"command": "nmap -sV target.internal"', { exact: false })).toBeVisible();
  });
});
