export type GraphType = "task" | "causal" | "evidence" | (string & {});

export type SessionGraphNode = {
  id: string;
  graph_type: GraphType;
  node_type: string;
  label: string;
  data: Record<string, unknown>;
};

export type SessionGraphEdge = {
  id: string;
  graph_type: GraphType;
  source: string;
  target: string;
  relation: string;
  data: Record<string, unknown>;
};

export type SessionGraph = {
  session_id: string;
  workflow_run_id: string;
  graph_type: GraphType;
  current_stage: string | null;
  nodes: SessionGraphNode[];
  edges: SessionGraphEdge[];
};
