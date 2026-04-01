export type WorkbenchNavItem = {
  id: string;
  label: string;
  shortLabel: string;
  description: string;
  to: string;
  isActive: (pathname: string) => boolean;
};

export const WORKBENCH_NAV_ITEMS: WorkbenchNavItem[] = [
  {
    id: "sessions",
    label: "Workspace",
    shortLabel: "WS",
    description: "主工作台：聊天、推进与按需展开的执行视图。",
    to: "/sessions",
    isActive: (pathname) => pathname.startsWith("/sessions"),
  },
  {
    id: "runtime",
    label: "Runtime",
    shortLabel: "RT",
    description: "查看容器状态、执行命令并对照会话策略。",
    to: "/runtime",
    isActive: (pathname) => pathname.startsWith("/runtime"),
  },
  {
    id: "skills",
    label: "Skills",
    shortLabel: "SK",
    description: "浏览技能卡片、描述与调用入口。",
    to: "/skills",
    isActive: (pathname) => pathname.startsWith("/skills"),
  },
  {
    id: "mcp",
    label: "MCP",
    shortLabel: "MC",
    description: "管理服务器连接、能力开关与可用性。",
    to: "/mcp",
    isActive: (pathname) => pathname.startsWith("/mcp"),
  },
  {
    id: "settings",
    label: "设置",
    shortLabel: "ST",
    description: "统一配置模型参数与工作台基础选项。",
    to: "/settings",
    isActive: (pathname) => pathname.startsWith("/settings"),
  },
];
