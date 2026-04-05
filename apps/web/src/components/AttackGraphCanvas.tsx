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
import type { SessionGraph, SessionGraphNode } from "../types/graphs";
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
  overlayTitle?: string | null;
  overlayCopy?: string | null;
};

type AttackCanvasNodeData = {
  label: string;
  nodeType: string;
  status: string | null;
  excerpt: string | null;
  isActive: boolean;
  isLatest: boolean;
  isSelected: boolean;
  isDimmed: boolean;
  emphasis: "goal" | "critical" | "result" | "supporting";
};

const NODE_WIDTH = 208;
const NODE_BASE_HEIGHT = 78;
const NODE_EXCERPT_HEIGHT = 18;
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

function truncateText(value: string | null, maxLength: number): string | null {
  if (!value) {
    return null;
  }

  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, maxLength - 1).trimEnd()}…`;
}

function buildNodeDisplay(node: SessionGraphNode): {
  title: string;
  excerpt: string | null;
  emphasis: AttackCanvasNodeData["emphasis"];
} {
  const summary = readString(node.data.summary);
  const command =
    readString(node.data.command) ??
    readString(node.data.tool_name) ??
    readString(node.data.tool) ??
    readString(node.data.intent);
  const observation =
    readString(node.data.stdout) ??
    readString(node.data.observation) ??
    readString(node.data.result_text) ??
    readString(node.data.evidence);

  switch (node.node_type) {
    case "goal":
      return { title: truncateText(node.label, 52) ?? node.label, excerpt: null, emphasis: "goal" };
    case "action":
      return {
        title: truncateText(command ?? node.label, 52) ?? node.label,
        excerpt: truncateText(summary, 68),
        emphasis: "supporting",
      };
    case "exploit":
      return {
        title: truncateText(command ?? node.label, 52) ?? node.label,
        excerpt: truncateText(summary, 68),
        emphasis: "critical",
      };
    case "observation":
      return {
        title: truncateText(node.label, 52) ?? node.label,
        excerpt: truncateText(summary ?? observation, 68),
        emphasis: "supporting",
      };
    case "hypothesis":
      return {
        title: truncateText(summary ?? node.label, 52) ?? node.label,
        excerpt: null,
        emphasis: "supporting",
      };
    case "outcome":
      return {
        title: truncateText(summary ?? node.label, 52) ?? node.label,
        excerpt: truncateText(summary && summary !== node.label ? summary : null, 68),
        emphasis: "result",
      };
    default:
      return {
        title: truncateText(node.label, 52) ?? node.label,
        excerpt: truncateText(summary, 68),
        emphasis:
          node.node_type === "surface" || node.node_type === "vulnerability"
            ? "critical"
            : "supporting",
      };
  }
}

function getSelectedNeighborhood(graph: SessionGraph, selectedNodeId: string | null): Set<string> {
  const ids = new Set<string>();
  if (!selectedNodeId) {
    return ids;
  }

  ids.add(selectedNodeId);
  for (const edge of graph.edges) {
    if (edge.source === selectedNodeId) {
      ids.add(edge.target);
    }
    if (edge.target === selectedNodeId) {
      ids.add(edge.source);
    }
  }

  return ids;
}

function getRelationLabel(relation: string): string {
  switch (relation) {
    case "depends_on":
      return "依赖";
    case "precedes":
      return "前置";
    case "supports":
      return "支持";
    case "contradicts":
      return "矛盾";
    case "validates":
      return "验证";
    case "causes":
      return "导致";
    case "attempts":
      return "尝试";
    case "enables":
      return "使能";
    case "branches_from":
      return "分支自";
    case "blocks":
      return "阻断";
    case "discovers":
      return "发现";
    case "confirms":
      return "确认";
    default:
      return relation;
  }
}

function getEdgeStroke(status: string | null, highlighted: boolean, dimmed: boolean): string {
  if (status === "failed" || status === "blocked") {
    return dimmed ? "rgba(150, 93, 93, 0.28)" : "rgba(150, 93, 93, 0.72)";
  }

  if (highlighted || status === "in_progress") {
    return dimmed ? "rgba(36, 88, 69, 0.32)" : "rgba(36, 88, 69, 0.78)";
  }

  if (status === "completed" || status === "done") {
    return dimmed ? "rgba(74, 89, 79, 0.18)" : "rgba(74, 89, 79, 0.44)";
  }

  return dimmed ? "rgba(74, 89, 79, 0.12)" : "rgba(74, 89, 79, 0.24)";
}

function AttackGraphFlowNode({ data }: NodeProps<AttackCanvasNodeData>) {
  return (
    <div
      className={`attack-graph-flow-node attack-graph-flow-node-${data.emphasis}${data.isActive ? " attack-graph-flow-node-active" : ""}${data.isLatest ? " attack-graph-flow-node-latest" : ""}${data.isSelected ? " attack-graph-flow-node-selected" : ""}${data.isDimmed ? " attack-graph-flow-node-dimmed" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="attack-graph-flow-handle" />
      <div className="attack-graph-flow-node-header">
        <span className="attack-graph-flow-node-type">{formatNodeTypeLabel(data.nodeType)}</span>
        <span className={`attack-graph-flow-node-status ${getNodeStatusTone(data.status)}`}>
          {formatNodeStatus(data.status)}
        </span>
      </div>
      <strong className="attack-graph-flow-node-title">{data.label}</strong>
      {data.excerpt ? <p className="attack-graph-flow-node-summary">{data.excerpt}</p> : null}
      <Handle type="source" position={Position.Right} className="attack-graph-flow-handle" />
    </div>
  );
}

const attackGraphNodeTypes = {
  attackNode: AttackGraphFlowNode,
};

export function buildAutoLayout(
  graph: SessionGraph,
  selectedNodeId: string | null,
  latestNodeId: string | null,
): {
  nodes: Node<AttackCanvasNodeData>[];
  edges: Edge[];
} {
  const selectedNeighborhood = getSelectedNeighborhood(graph, selectedNodeId);
  const activeNodeIds = new Set(
    graph.nodes
      .filter(
        (node) =>
          readBoolean(node.data.current) || readBoolean(node.data.active) || node.id === latestNodeId,
      )
      .map((node) => node.id),
  );
  const nodeMap = new Map(graph.nodes.map((node) => [node.id, node]));

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
    const { excerpt } = buildNodeDisplay(node);
    layoutGraph.setNode(node.id, {
      width: NODE_WIDTH,
      height: NODE_BASE_HEIGHT + (excerpt ? NODE_EXCERPT_HEIGHT : 0),
    });
  }

  for (const edge of graph.edges) {
    layoutGraph.setEdge(edge.source, edge.target);
  }

  dagre.layout(layoutGraph);

  const nodes = graph.nodes.map<Node<AttackCanvasNodeData>>((node) => {
    const layoutNode = layoutGraph.node(node.id);
    const { title, excerpt, emphasis } = buildNodeDisplay(node);
    const height = NODE_BASE_HEIGHT + (excerpt ? NODE_EXCERPT_HEIGHT : 0);
    const isSelected = selectedNodeId === node.id;
    const isNeighborhoodNode = selectedNeighborhood.has(node.id);
    const isDimmed = Boolean(selectedNodeId) && !isNeighborhoodNode;

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
        label: title,
        nodeType: node.node_type,
        status: readString(node.data.status),
        excerpt,
        isActive: readBoolean(node.data.current) || readBoolean(node.data.active),
        isLatest: latestNodeId === node.id,
        isSelected,
        isDimmed,
        emphasis,
      },
      draggable: false,
      selectable: true,
    };
  });

  const edges = graph.edges.map<Edge>((edge) => {
    const targetNode = nodeMap.get(edge.target);
    const edgeStatus = readString(edge.data.status) ?? readString(targetNode?.data.status) ?? null;
    const isSelectedEdge =
      selectedNodeId !== null && (edge.source === selectedNodeId || edge.target === selectedNodeId);
    const isActiveEdge = activeNodeIds.has(edge.source) || activeNodeIds.has(edge.target);
    const showLabel = isSelectedEdge || isActiveEdge || edgeStatus === "failed" || edgeStatus === "blocked";
    const isDimmed = Boolean(selectedNodeId) && !isSelectedEdge;
    const stroke = getEdgeStroke(edgeStatus, isSelectedEdge || isActiveEdge, isDimmed);

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      animated: readString(edge.data.status) === "in_progress",
      label: showLabel ? getRelationLabel(edge.relation) : undefined,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: stroke,
      },
      style: {
        stroke,
        strokeOpacity: isDimmed ? 0.52 : 1,
        strokeWidth: isSelectedEdge || isActiveEdge ? 2 : 1.25,
      },
      labelStyle: {
        fill: "var(--text-secondary)",
        fontSize: 11,
        fontWeight: 600,
      },
      labelBgPadding: [8, 4],
      labelBgBorderRadius: 999,
      labelBgStyle: {
        fill: showLabel ? "rgba(255, 255, 252, 0.92)" : "transparent",
        fillOpacity: showLabel ? 0.96 : 0,
        stroke: showLabel ? "rgba(74, 89, 79, 0.1)" : "transparent",
      },
    };
  });

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
    const focusNode = focusNodeId ? (nodes.find((node) => node.id === focusNodeId) ?? null) : null;

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
        : NODE_BASE_HEIGHT + (focusNode.data.excerpt ? NODE_EXCERPT_HEIGHT : 0);

    setCenter(focusNode.position.x + targetWidth / 2, focusNode.position.y + targetHeight / 2, {
      zoom: AUTO_FOCUS_ZOOM,
      duration: AUTO_FOCUS_DURATION_MS,
    });
  }, [
    autoFocusSignature,
    fitView,
    focusNodeId,
    hasUserInteracted,
    nodes,
    setCenter,
    viewportInitialized,
  ]);

  return null;
}

export function AttackGraphCanvas({
  graph,
  selectedNodeId,
  latestNodeId,
  onSelectNode,
  overlayTitle = null,
  overlayCopy = null,
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
      {overlayTitle ? (
        <div className="attack-graph-canvas-overlay" data-testid="attack-graph-canvas-overlay">
          <strong className="attack-graph-canvas-overlay-title">{overlayTitle}</strong>
          {overlayCopy ? <p className="attack-graph-canvas-overlay-copy">{overlayCopy}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
