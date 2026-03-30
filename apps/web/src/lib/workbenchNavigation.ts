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
    label: "对话",
    shortLabel: "SE",
    description: "保留工作流、事件轨迹与工具输出。",
    to: "/sessions",
    isActive: (pathname) => pathname.startsWith("/sessions"),
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
