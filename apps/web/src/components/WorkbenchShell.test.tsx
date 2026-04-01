import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../store/uiStore";
import { WorkbenchShell } from "./WorkbenchShell";

function createMatchMedia(matches: boolean): (query: string) => MediaQueryList {
  return (query: string) =>
    ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as unknown as MediaQueryList;
}

function renderWorkbenchShell(initialEntry = "/sessions") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<WorkbenchShell />}>
          <Route path="/sessions" element={<div>workspace body</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkbenchShell", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", createMatchMedia(false));
  });

  it("applies theme and density attributes from the UI store", () => {
    useUiStore.setState({ themePreference: "light", uiDensity: "comfortable" });

    renderWorkbenchShell();

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).toHaveAttribute("data-ui-density", "comfortable");
  });

  it("persists drawer collapse state when toggled", async () => {
    const user = userEvent.setup();

    renderWorkbenchShell();
    await user.click(screen.getByRole("button", { name: "切换全局导航" }));

    expect(window.localStorage.getItem("aegissec.workbench.drawer.collapsed")).toBe("true");
    expect(document.querySelector(".workbench-shell")).toHaveClass("workbench-shell-collapsed");
  });
});
