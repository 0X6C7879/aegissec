import { useEffect, useMemo, useRef, useState } from "react";
import dagre from "dagre";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Panel,
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
  getBestExecutionPathContext,
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

const NODE_WIDTH = 256;
const NODE_BASE_HEIGHT = 112;
const NODE_EXCERPT_HEIGHT = 30;
const AUTO_FOCUS_DURATION_MS = 280;
const AUTO_FOCUS_ZOOM = 0.88;

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

function getEdgeStroke(status: string | null, highlighted: boolean, dimmed: boolean): string {
  if (status === "failed" || status === "blocked") {
    return dimmed ? "rgba(255, 107, 107, 0.26)" : "rgba(255, 107, 107, 0.78)";
  }

  if (highlighted || status === "in_progress") {
    return dimmed ? "rgba(0, 217, 255, 0.24)" : "rgba(0, 217, 255, 0.86)";
  }

  if (status === "completed" || status === "done") {
    return dimmed ? "rgba(120, 138, 163, 0.18)" : "rgba(120, 138, 163, 0.42)";
  }

  return dimmed ? "rgba(91, 104, 125, 0.14)" : "rgba(91, 104, 125, 0.3)";
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

function AttackGraphCanvasControls() {
  const { fitView, zoomIn, zoomOut } = useReactFlow();

  return (
    <Panel position="bottom-right" className="attack-graph-controls">
      <button
        className="attack-graph-control-button"
        type="button"
        aria-label="缩小画布"
        title="缩小画布"
        onClick={() => {
          void zoomOut({ duration: 140 });
        }}
      >
        <span aria-hidden="true">−</span>
      </button>
      <button
        className="attack-graph-control-button"
        type="button"
        aria-label="放大画布"
        title="放大画布"
        onClick={() => {
          void zoomIn({ duration: 140 });
        }}
      >
        <span aria-hidden="true">+</span>
      </button>
      <button
        className="attack-graph-control-button attack-graph-control-button-fit"
        type="button"
        aria-label="适配视图"
        title="适配视图"
        onClick={() => {
          void fitView({ padding: 0.11, duration: 180 });
        }}
      >
        <span aria-hidden="true">□</span>
      </button>
    </Panel>
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
  const selectedPathContext = getBestExecutionPathContext(graph, selectedNodeId, latestNodeId);
  const hasFocusedContext = selectedPathContext.nodeIds.size > 0;
  const activeNodeIds = new Set(
    graph.nodes
      .filter(
        (node) =>
          readBoolean(node.data.current) ||
          readBoolean(node.data.active) ||
          node.id === latestNodeId,
      )
      .map((node) => node.id),
  );
  const nodeMap = new Map(graph.nodes.map((node) => [node.id, node]));

  const layoutGraph = new dagre.graphlib.Graph();
  layoutGraph.setDefaultEdgeLabel(() => ({}));
  layoutGraph.setGraph({
    rankdir: "LR",
    ranksep: 88,
    nodesep: 34,
    marginx: 28,
    marginy: 28,
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
    const isContextNode = selectedPathContext.nodeIds.has(node.id);
    const isDimmed = hasFocusedContext && !isContextNode;

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
    const isSelectedEdge = selectedPathContext.edgeIds.has(edge.id);
    const isActiveEdge = activeNodeIds.has(edge.source) || activeNodeIds.has(edge.target);
    const isHoveredEdge = hoveredEdgeId === edge.id;
    const touchesOutcome =
      sourceNode?.node_type === "outcome" || targetNode?.node_type === "outcome";
    const isPathStateEdge = edgeStatus === "failed" || edgeStatus === "blocked";
    const isSuccessEdge = touchesOutcome && (edgeStatus === "completed" || edgeStatus === "done");
    const showLabel =
      isHoveredEdge || isSelectedEdge || isActiveEdge || isPathStateEdge || isSuccessEdge;
    const isDimmed = hasFocusedContext && !isSelectedEdge;
    const stroke = getEdgeStroke(
      edgeStatus,
      isSelectedEdge || isActiveEdge || isHoveredEdge,
      isDimmed,
    );

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
        fill:
          isHoveredEdge || isSelectedEdge || isActiveEdge
            ? "var(--text-primary)"
            : "var(--text-secondary)",
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
  autoFocusSignature,
  hasUserInteracted,
}: {
  nodes: Node<AttackCanvasNodeData>[];
  autoFocusSignature: string;
  hasUserInteracted: boolean;
}) {
  const { fitView, viewportInitialized } = useReactFlow<AttackCanvasNodeData>();
  const lastAutoFocusSignatureRef = useRef<string | null>(null);

  useEffect(() => {
    if (!viewportInitialized || nodes.length === 0) {
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
    const frameId = window.requestAnimationFrame(() => {
      fitView({
        padding: 0.11,
        minZoom: 0.52,
        maxZoom: AUTO_FOCUS_ZOOM,
        duration: AUTO_FOCUS_DURATION_MS,
      });
    });

    return () => {
      window.cancelAnimationFrame(frameId);
    };
  }, [autoFocusSignature, fitView, hasUserInteracted, nodes, viewportInitialized]);

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
        fitViewOptions={{ padding: 0.11, minZoom: 0.52, maxZoom: AUTO_FOCUS_ZOOM }}
        minZoom={0.45}
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
          autoFocusSignature={autoFocusSignature}
          hasUserInteracted={hasUserInteracted}
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={28}
          size={1.1}
          color="rgba(0, 217, 255, 0.09)"
        />
        <AttackGraphCanvasControls />
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
