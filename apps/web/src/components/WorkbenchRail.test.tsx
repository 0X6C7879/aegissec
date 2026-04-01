import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { WORKBENCH_NAV_ITEMS } from "../lib/workbenchNavigation";
import { WorkbenchRail } from "./WorkbenchRail";

describe("WorkbenchRail", () => {
  it("renders the complete navigation set and marks the active route", () => {
    render(
      <MemoryRouter initialEntries={["/skills"]}>
        <WorkbenchRail
          collapsed={false}
          mobileOpen={false}
          onToggleCollapsed={vi.fn()}
          onDismissMobile={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("navigation", { name: "工作台导航" })).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(WORKBENCH_NAV_ITEMS.length);
    expect(screen.getByRole("link", { name: /Skills/i })).toHaveClass(
      "workspace-drawer-item-active",
    );
  });

  it("dismisses the mobile drawer when a navigation link is clicked", async () => {
    const user = userEvent.setup();
    const onDismissMobile = vi.fn();

    render(
      <MemoryRouter initialEntries={["/sessions"]}>
        <WorkbenchRail
          collapsed={false}
          mobileOpen={true}
          onToggleCollapsed={vi.fn()}
          onDismissMobile={onDismissMobile}
        />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("link", { name: /MCP/i }));
    expect(onDismissMobile).toHaveBeenCalledTimes(1);
  });
});
