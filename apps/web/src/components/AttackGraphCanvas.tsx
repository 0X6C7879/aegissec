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
  displayExcerpt,
  displayImportance,
  displayTitle,
  formatAttackNodeStatus,
  formatAttackNodeType,
  getAttackGraphAutoFocusNodeId,
  getAttackNodeStatusTone,
  getAttackRelationLabel,
  readBoolean,
  readString,
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
const NODE_BASE_HEIGHT = 62;
const NODE_EXCERPT_HEIGHT = 12;
const AUTO_FOCUS_DURATION_MS = 280;
const AUTO_FOCUS_ZOOM = 0.85;

function buildNodeDisplay(node: SessionGraphNode): {
  title: string;
  excerpt: string | null;
  emphasis: AttackCanvasNodeData["emphasis"];
} {
  return {
    title: displayTitle(node),
    excerpt: displayExcerpt(node),
    emphasis: displayImportance(node),
  };
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
        <span className="attack-graph-flow-node-type">{formatAttackNodeType(data.nodeType)}</span>
        <span className={`attack-graph-flow-node-status ${getAttackNodeStatusTone(data.status)}`}>
          {formatAttackNodeStatus(data.status)}
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

// eslint-disable-next-line react-refresh/only-export-components
export function buildAutoLayout(
  graph: SessionGraph,
  selectedNodeId: string | null,
  latestNodeId: string | null,
  hoveredEdgeId: string | null = null,
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
    const sourceNode = nodeMap.get(edge.source);
    const edgeStatus = readString(edge.data.status) ?? readString(targetNode?.data.status) ?? null;
    const isSelectedEdge =
      selectedNodeId !== null && (edge.source === selectedNodeId || edge.target === selectedNodeId);
    const isActiveEdge = activeNodeIds.has(edge.source) || activeNodeIds.has(edge.target);
    const isHoveredEdge = hoveredEdgeId === edge.id;
    const touchesOutcome = sourceNode?.node_type === "outcome" || targetNode?.node_type === "outcome";
    const isPathStateEdge = edgeStatus === "failed" || edgeStatus === "blocked";
    const isSuccessEdge = touchesOutcome && (edgeStatus === "completed" || edgeStatus === "done");
    const showLabel = isHoveredEdge || isSelectedEdge || isActiveEdge || isPathStateEdge || isSuccessEdge;
    const isDimmed = Boolean(selectedNodeId) && !isSelectedEdge;
    const stroke = getEdgeStroke(edgeStatus, isSelectedEdge || isActiveEdge || isHoveredEdge, isDimmed);

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      animated: readString(edge.data.status) === "in_progress",
      label: showLabel ? getAttackRelationLabel(edge.relation) : undefined,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: stroke,
      },
      style: {
        stroke,
        strokeOpacity: isDimmed ? 0.52 : 1,
        strokeWidth: isSelectedEdge || isActiveEdge || isHoveredEdge ? 1.9 : 1.1,
      },
      labelStyle: {
        fill: isHoveredEdge || isSelectedEdge || isActiveEdge ? "var(--text-primary)" : "var(--text-secondary)",
        fontSize: 10,
        fontWeight: 600,
      },
      labelBgStyle: {
        fill: "transparent",
        fillOpacity: 0,
        stroke: "transparent",
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
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const flowGraph = useMemo(
    () => buildAutoLayout(graph, selectedNodeId, latestNodeId, hoveredEdgeId),
    [graph, hoveredEdgeId, latestNodeId, selectedNodeId],
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
        onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
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
