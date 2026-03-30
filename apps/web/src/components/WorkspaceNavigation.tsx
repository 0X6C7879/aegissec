import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  {
    to: "/sessions",
    label: "Sessions",
    description: "Retained chat and event history",
  },
  {
    to: "/runtime",
    label: "Runtime",
    description: "Controlled container status and execution runs",
  },
] as const;

export function WorkspaceNavigation() {
  return (
    <nav className="workspace-nav" aria-label="Workspace navigation">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `workspace-nav-link${isActive ? " workspace-nav-link-active" : ""}`
          }
        >
          <span className="workspace-nav-label">{item.label}</span>
          <span className="workspace-nav-description">{item.description}</span>
        </NavLink>
      ))}
    </nav>
  );
}
