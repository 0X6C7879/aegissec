export type SlashActionInvocation = {
  tool_name: string;
  arguments: Record<string, unknown>;
  mcp_server_id: string | null;
  mcp_tool_name: string | null;
};

export type SlashAction = {
  id: string;
  trigger: string;
  type: string;
  source: string;
  display_text: string;
  invocation: SlashActionInvocation;
};

export type SlashCatalogItem = {
  id: string;
  trigger: string;
  title: string;
  type: string;
  source: string;
  disabled?: boolean | null;
  description?: string | null;
  badge?: string | null;
  keybind?: string | null;
  action: SlashAction;
};

export function isUiOnlySlashAction(action: SlashAction): boolean {
  return action.type === "builtin" && action.source === "ui";
}
