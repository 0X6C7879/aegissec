import type { SkillCompatibilityScope, SkillCompatibilitySource } from "./skills";

export type MCPTransport = "stdio" | "http" | (string & {});

export type MCPServerStatus = "inactive" | "connected" | "error" | (string & {});

export type MCPCapabilityKind =
  | "tool"
  | "resource"
  | "resource_template"
  | "prompt"
  | (string & {});

export type MCPCapability = {
  kind: MCPCapabilityKind;
  name: string;
  title: string | null;
  description: string | null;
  uri: string | null;
  metadata: Record<string, unknown>;
  input_schema: Record<string, unknown>;
  raw_payload: Record<string, unknown>;
};

export type MCPServer = {
  id: string;
  name: string;
  source: SkillCompatibilitySource;
  scope: SkillCompatibilityScope;
  transport: MCPTransport;
  enabled: boolean;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  timeout_ms: number;
  status: MCPServerStatus;
  last_error: string | null;
  config_path: string;
  imported_at: string;
  capabilities: MCPCapability[];
};
