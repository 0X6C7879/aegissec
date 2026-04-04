import { useEffect, useMemo, useRef, useState } from "react";
import dagre from "dagre";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import type { SessionGraph } from "../types/graphs";
import {
  buildAttackGraphAutoFocusSignature,
  getAttackGraphAutoFocusNodeId,
  shouldAutoFocusAttackGraph,
} from "./AttackGraphCanvas.utils";

type AttackGraphCanvasProps = {
  graph: SessionGraph;
  selectedNodeId: string | null;
  latestNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
};

type AttackCanvasNodeData = {
  label: string;
  nodeType: string;
  status: string | null;
  summary: string | null;
  isActive: boolean;
  isLatest: boolean;
  isSelected: boolean;
};

const NODE_WIDTH = 280;
const NODE_BASE_HEIGHT = 118;
const NODE_SUMMARY_HEIGHT = 42;
const AUTO_FOCUS_DURATION_MS = 280;
const AUTO_FOCUS_ZOOM = 0.85;

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function formatNodeTypeLabel(nodeType: string): string {
  switch (nodeType) {
    case "goal":
      return "目标";
    case "surface":
      return "攻击面";
    case "observation":
      return "观测";
    case "hypothesis":
      return "假设";
    case "action":
      return "动作";
    case "vulnerability":
      return "漏洞";
    case "exploit":
      return "验证";
    case "pivot":
      return "横向路径";
    case "outcome":
      return "结果";
    default:
      return nodeType;
  }
}

function formatNodeStatus(status: string | null): string {
  switch (status) {
    case "completed":
      return "已完成";
    case "in_progress":
      return "进行中";
    case "blocked":
      return "已阻塞";
    case "failed":
      return "异常";
    case "ready":
      return "就绪";
    case "pending":
      return "待执行";
    case "done":
      return "已完成";
    default:
      return status ?? "未标记";
  }
}

function getNodeStatusTone(status: string | null): string {
  switch (status) {
    case "completed":
    case "done":
      return "tone-success";
    case "in_progress":
    case "running":
      return "tone-connected";
    case "blocked":
    case "needs_approval":
      return "tone-warning";
    case "failed":
    case "error":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

function AttackGraphFlowNode({ data }: NodeProps<AttackCanvasNodeData>) {
  return (
    <div
      className={`attack-graph-flow-node${data.isActive ? " attack-graph-flow-node-active" : ""}${data.isLatest ? " attack-graph-flow-node-latest" : ""}${data.isSelected ? " attack-graph-flow-node-selected" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="attack-graph-flow-handle" />
      <div className="attack-graph-flow-node-header">
        <span className="attack-graph-flow-node-type">{formatNodeTypeLabel(data.nodeType)}</span>
        <span className={`management-status-badge ${getNodeStatusTone(data.status)}`}>
          {formatNodeStatus(data.status)}
        </span>
      </div>
      <strong className="attack-graph-flow-node-title">{data.label}</strong>
      {data.summary ? <p className="attack-graph-flow-node-summary">{data.summary}</p> : null}
      <div className="attack-graph-flow-node-flags">
        {data.isActive ? <span className="attack-graph-flow-flag">当前</span> : null}
        {data.isLatest ? <span className="attack-graph-flow-flag">最新</span> : null}
      </div>
      <Handle type="source" position={Position.Right} className="attack-graph-flow-handle" />
    </div>
  );
}

const attackGraphNodeTypes = {
  attackNode: AttackGraphFlowNode,
};

function buildAutoLayout(
  graph: SessionGraph,
  selectedNodeId: string | null,
  latestNodeId: string | null,
): {
  nodes: Node<AttackCanvasNodeData>[];
  edges: Edge[];
} {
  const layoutGraph = new dagre.graphlib.Graph();
  layoutGraph.setDefaultEdgeLabel(() => ({}));
  layoutGraph.setGraph({
    rankdir: "LR",
    ranksep: 84,
    nodesep: 32,
    marginx: 24,
    marginy: 24,
  });

  for (const node of graph.nodes) {
    const summary = readString(node.data.summary);
    layoutGraph.setNode(node.id, {
      width: NODE_WIDTH,
      height: NODE_BASE_HEIGHT + (summary ? NODE_SUMMARY_HEIGHT : 0),
    });
  }

  for (const edge of graph.edges) {
    layoutGraph.setEdge(edge.source, edge.target);
  }

  dagre.layout(layoutGraph);

  const nodes = graph.nodes.map<Node<AttackCanvasNodeData>>((node) => {
    const layoutNode = layoutGraph.node(node.id);
    const summary = readString(node.data.summary);
    const height = NODE_BASE_HEIGHT + (summary ? NODE_SUMMARY_HEIGHT : 0);

    return {
      id: node.id,
      type: "attackNode",
      position: {
        x: layoutNode.x - NODE_WIDTH / 2,
        y: layoutNode.y - height / 2,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: node.label,
        nodeType: node.node_type,
        status: readString(node.data.status),
        summary,
        isActive: readBoolean(node.data.current) || readBoolean(node.data.active),
        isLatest: latestNodeId === node.id,
        isSelected: selectedNodeId === node.id,
      },
      draggable: false,
      selectable: true,
    };
  });

  const edges = graph.edges.map<Edge>((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    animated: readString(edge.data.status) === "in_progress",
    label: edge.relation,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: "var(--accent)",
    },
    style: {
      stroke: "var(--accent)",
      strokeOpacity: 0.52,
      strokeWidth: 1.6,
    },
    labelStyle: {
      fill: "var(--text-secondary)",
      fontSize: 12,
      fontWeight: 600,
    },
    labelBgPadding: [8, 4],
    labelBgBorderRadius: 999,
    labelBgStyle: {
      fill: "rgba(255, 255, 252, 0.92)",
      fillOpacity: 0.96,
      stroke: "rgba(74, 89, 79, 0.14)",
    },
  }));

  return { nodes, edges };
}

function AttackGraphViewportController({
  nodes,
  focusNodeId,
  autoFocusSignature,
  hasUserInteracted,
}: {
  nodes: Node<AttackCanvasNodeData>[];
  focusNodeId: string | null;
  autoFocusSignature: string;
  hasUserInteracted: boolean;
}) {
  const { fitView, setCenter, viewportInitialized } = useReactFlow<AttackCanvasNodeData>();
  const lastAutoFocusSignatureRef = useRef<string | null>(null);

  useEffect(() => {
    if (!viewportInitialized) {
      return;
    }

    const shouldAutoFocus = shouldAutoFocusAttackGraph({
      hasUserInteracted,
      nextSignature: autoFocusSignature,
      previousSignature: lastAutoFocusSignatureRef.current,
    });

    if (!shouldAutoFocus) {
      return;
    }

    lastAutoFocusSignatureRef.current = autoFocusSignature;
    const focusNode = focusNodeId ? nodes.find((node) => node.id === focusNodeId) ?? null : null;

    if (!focusNode) {
      fitView({ padding: 0.18, minZoom: 0.55, duration: AUTO_FOCUS_DURATION_MS });
      return;
    }

    fitView({
      padding: 0.28,
      minZoom: 0.55,
      maxZoom: AUTO_FOCUS_ZOOM,
      duration: AUTO_FOCUS_DURATION_MS,
      nodes: [{ id: focusNode.id }],
    });

    const targetWidth = typeof focusNode.width === "number" ? focusNode.width : NODE_WIDTH;
    const targetHeight =
      typeof focusNode.height === "number"
        ? focusNode.height
        : NODE_BASE_HEIGHT + (focusNode.data.summary ? NODE_SUMMARY_HEIGHT : 0);

    setCenter(focusNode.position.x + targetWidth / 2, focusNode.position.y + targetHeight / 2, {
      zoom: AUTO_FOCUS_ZOOM,
      duration: AUTO_FOCUS_DURATION_MS,
    });
  }, [autoFocusSignature, fitView, focusNodeId, hasUserInteracted, nodes, setCenter, viewportInitialized]);

  return null;
}

export function AttackGraphCanvas({
  graph,
  selectedNodeId,
  latestNodeId,
  onSelectNode,
}: AttackGraphCanvasProps) {
  const flowGraph = useMemo(
    () => buildAutoLayout(graph, selectedNodeId, latestNodeId),
    [graph, latestNodeId, selectedNodeId],
  );
  const [hasUserInteracted, setHasUserInteracted] = useState(false);
  const programmaticMoveRef = useRef(false);
  const focusNodeId = useMemo(
    () => getAttackGraphAutoFocusNodeId(graph, latestNodeId),
    [graph, latestNodeId],
  );
  const autoFocusSignature = useMemo(
    () => buildAttackGraphAutoFocusSignature(graph, focusNodeId),
    [focusNodeId, graph],
  );

  const handleMoveStart = useMemo(
    () => () => {
      if (programmaticMoveRef.current) {
        return;
      }

      setHasUserInteracted(true);
    },
    [],
  );

  useEffect(() => {
    if (autoFocusSignature.length === 0) {
      programmaticMoveRef.current = false;
      return;
    }

    programmaticMoveRef.current = true;

    const releaseTimer = window.setTimeout(() => {
      programmaticMoveRef.current = false;
    }, AUTO_FOCUS_DURATION_MS + 32);

    return () => {
      window.clearTimeout(releaseTimer);
    };
  }, [autoFocusSignature]);

  return (
    <div className="attack-graph-canvas-shell" data-testid="attack-graph-canvas">
      <ReactFlow
        nodes={flowGraph.nodes}
        edges={flowGraph.edges}
        nodeTypes={attackGraphNodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18, minZoom: 0.55 }}
        minZoom={0.3}
        maxZoom={1.8}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable
        onNodeClick={(_event, node) => onSelectNode(node.id)}
        onMoveStart={handleMoveStart}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed } }}
      >
        <AttackGraphViewportController
          nodes={flowGraph.nodes}
          focusNodeId={focusNodeId}
          autoFocusSignature={autoFocusSignature}
          hasUserInteracted={hasUserInteracted}
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1.2}
          color="rgba(36, 88, 69, 0.12)"
        />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
