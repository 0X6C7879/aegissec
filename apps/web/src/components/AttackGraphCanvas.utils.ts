import type { SessionGraph } from "../types/graphs";

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

export function getAttackGraphAutoFocusNodeId(
  graph: SessionGraph,
  latestNodeId: string | null,
): string | null {
  const activeNode = graph.nodes.find(
    (node) => readBoolean(node.data.current) || readBoolean(node.data.active),
  );

  if (activeNode) {
    return activeNode.id;
  }

  if (latestNodeId && graph.nodes.some((node) => node.id === latestNodeId)) {
    return latestNodeId;
  }

  return graph.nodes[0]?.id ?? null;
}

export function buildAttackGraphAutoFocusSignature(
  graph: SessionGraph,
  focusNodeId: string | null,
): string {
  const nodeSignature = graph.nodes
    .map((node) => {
      const status = readString(node.data.status) ?? "";
      const current = readBoolean(node.data.current) || readBoolean(node.data.active) ? "1" : "0";
      const updatedAt = readString(node.data.updated_at) ?? readString(node.data.ended_at) ?? "";

      return `${node.id}:${status}:${current}:${updatedAt}`;
    })
    .join("|");
  const edgeSignature = graph.edges.map((edge) => edge.id).join("|");

  return `${graph.workflow_run_id}:${focusNodeId ?? "none"}:${nodeSignature}:${edgeSignature}`;
}

export function shouldAutoFocusAttackGraph(options: {
  hasUserInteracted: boolean;
  nextSignature: string;
  previousSignature: string | null;
}): boolean {
  const { hasUserInteracted, nextSignature, previousSignature } = options;

  if (hasUserInteracted || nextSignature.length === 0) {
    return false;
  }

  return nextSignature !== previousSignature;
}
