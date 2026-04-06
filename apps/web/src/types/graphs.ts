export type GraphType = "task" | "causal" | "evidence" | "attack" | (string & {});

export type AttackGraphNodeType =
  | "root"
  | "goal"
  | "task"
  | "surface"
  | "observation"
  | "hypothesis"
  | "action"
  | "vulnerability"
  | "exploit"
  | "pivot"
  | "outcome"
  | (string & {});

export type AttackGraphNodeData = {
  summary?: string;
  status?: string;
  sequence?: number;
  current?: boolean;
  source_message_id?: string;
  branch_id?: string;
  generation_id?: string;
  source_graphs?: string[];
  provenance?: Record<string, unknown>;
  relation_context?: Record<string, unknown> | string;
} & Record<string, unknown>;

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

export type AttackGraphNode = Omit<SessionGraphNode, "graph_type" | "node_type" | "data"> & {
  graph_type: "attack";
  node_type: AttackGraphNodeType;
  data: AttackGraphNodeData;
};

export type AttackGraphEdge = Omit<SessionGraphEdge, "graph_type"> & {
  graph_type: "attack";
};

export type AttackGraph = Omit<SessionGraph, "graph_type" | "nodes" | "edges"> & {
  graph_type: "attack";
  nodes: AttackGraphNode[];
  edges: AttackGraphEdge[];
};
