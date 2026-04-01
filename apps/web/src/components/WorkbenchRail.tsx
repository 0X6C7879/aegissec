import { NavLink } from "react-router-dom";
import { WORKBENCH_NAV_ITEMS } from "../lib/workbenchNavigation";

type WorkbenchRailProps = {
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapsed: () => void;
  onDismissMobile: () => void;
};

export function WorkbenchRail({
  collapsed,
  mobileOpen,
  onToggleCollapsed,
  onDismissMobile,
}: WorkbenchRailProps) {
  const drawerClassName = `workspace-drawer${collapsed ? " workspace-drawer-collapsed" : ""}`;

  function renderNavIcon(itemId: string) {
    switch (itemId) {
      case "sessions":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M5 6.5a2.5 2.5 0 0 1 2.5-2.5h9A2.5 2.5 0 0 1 19 6.5v6A2.5 2.5 0 0 1 16.5 15H11l-3.6 3.2A.75.75 0 0 1 6.15 17.65V15.6A2.52 2.52 0 0 1 5 13.5z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        );
      case "projects":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M4.5 7.5A2.5 2.5 0 0 1 7 5h10a2.5 2.5 0 0 1 2.5 2.5v9A2.5 2.5 0 0 1 17 19H7a2.5 2.5 0 0 1-2.5-2.5z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <path
              d="M8 9.5h8M8 13h5M8 16h4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        );
      case "history":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 6.5A5.5 5.5 0 1 0 17.5 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
            <path
              d="M12 9v3l2 1.5M17 5v3h3"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        );
      case "skills":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 3l1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7zM6 15l.9 2.1L9 18l-2.1.9L6 21l-.9-2.1L3 18l2.1-.9zM18 14l1.2 2.8L22 18l-2.8 1.2L18 22l-1.2-2.8L14 18l2.8-1.2z"
              fill="currentColor"
            />
          </svg>
        );
      case "runtime":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M7 5.5h10A2.5 2.5 0 0 1 19.5 8v8A2.5 2.5 0 0 1 17 18.5H7A2.5 2.5 0 0 1 4.5 16V8A2.5 2.5 0 0 1 7 5.5Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <path
              d="M9 9.5h6M9 12h4M9 14.5h3"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        );
      case "mcp":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M7 7h4v4H7zM13 13h4v4h-4zM15 5h2a2 2 0 0 1 2 2v2M9 19H7a2 2 0 0 1-2-2v-2M11 9h2m-6 6h6m2 0h2m-8-4v4m8-8v6"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        );
      case "settings":
        return (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 8.5A3.5 3.5 0 1 0 12 15.5A3.5 3.5 0 1 0 12 8.5Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <path
              d="M19.4 15a1 1 0 0 0 .2 1.1l.1.1a1 1 0 0 1 0 1.4l-1.2 1.2a1 1 0 0 1-1.4 0l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V20a1 1 0 0 1-1 1h-1.7a1 1 0 0 1-1-1v-.2a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a1 1 0 0 1-1.4 0l-1.2-1.2a1 1 0 0 1 0-1.4l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H4a1 1 0 0 1-1-1v-1.7a1 1 0 0 1 1-1h.2a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a1 1 0 0 1 0-1.4l1.2-1.2a1 1 0 0 1 1.4 0l.1.1a1 1 0 0 0 1.1.2 1 1 0 0 0 .6-.9V4a1 1 0 0 1 1-1h1.7a1 1 0 0 1 1 1v.2a1 1 0 0 0 .6.9 1 1 0 0 0 1.1-.2l.1-.1a1 1 0 0 1 1.4 0l1.2 1.2a1 1 0 0 1 0 1.4l-.1.1a1 1 0 0 0-.2 1.1 1 1 0 0 0 .9.6h.2a1 1 0 0 1 1 1v1.7a1 1 0 0 1-1 1h-.2a1 1 0 0 0-.9.6Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        );
      default:
        return null;
    }
  }

  return (
    <div className={drawerClassName}>
      <div className="workspace-drawer-header">
        <button
          className="workspace-drawer-collapse"
          type="button"
          aria-controls="workbench-shell-drawer"
          aria-expanded={mobileOpen || !collapsed}
          aria-label="切换全局导航"
          title={mobileOpen ? "关闭导航" : collapsed ? "展开导航" : "收起导航"}
          onClick={onToggleCollapsed}
        >
          <span className="workspace-drawer-collapse-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </button>
        <button
          className="workspace-drawer-mobile-close"
          type="button"
          onClick={onDismissMobile}
          aria-label="关闭导航抽屉"
        >
          关闭
        </button>
      </div>

      <div className="workspace-drawer-section">
        <nav className="workspace-drawer-nav" aria-label="工作台导航">
          {WORKBENCH_NAV_ITEMS.map((item) => (
            <NavLink
              key={item.id}
              to={item.to}
              className={({ isActive }) =>
                `workspace-drawer-item${isActive ? " workspace-drawer-item-active" : ""}`
              }
              title={collapsed ? item.label : undefined}
              data-label={item.label}
              onClick={onDismissMobile}
            >
              <span
                className={`workspace-drawer-item-mark workspace-drawer-item-mark-${item.id}`}
                aria-hidden="true"
              >
                {renderNavIcon(item.id)}
              </span>
              <span className="workspace-drawer-item-copy">
                <span className="workspace-drawer-item-label">{item.label}</span>
              </span>
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
