import { useEffect, useRef, useState } from "react";
import { Outlet } from "react-router-dom";
import { useUiStore } from "../store/uiStore";
import { WorkbenchRail } from "./WorkbenchRail";

const DRAWER_STORAGE_KEY = "aegissec.workbench.drawer.collapsed";

function getStoredDrawerState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.localStorage.getItem(DRAWER_STORAGE_KEY) === "true";
}

export function WorkbenchShell() {
  const [isDrawerCollapsed, setIsDrawerCollapsed] = useState<boolean>(() => getStoredDrawerState());
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState(false);
  const lastDrawerToggleAtRef = useRef(0);
  const themePreference = useUiStore((state) => state.themePreference);
  const uiDensity = useUiStore((state) => state.uiDensity);

  useEffect(() => {
    window.localStorage.setItem(DRAWER_STORAGE_KEY, String(isDrawerCollapsed));
  }, [isDrawerCollapsed]);

  useEffect(() => {
    document.documentElement.dataset.theme = themePreference;
    document.documentElement.dataset.uiDensity = uiDensity;
  }, [themePreference, uiDensity]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 1120px)");
    const handleViewportModeChange = () => {
      setIsMobileDrawerOpen(false);
    };

    mediaQuery.addEventListener("change", handleViewportModeChange);
    return () => {
      mediaQuery.removeEventListener("change", handleViewportModeChange);
    };
  }, []);

  function handleToggleCollapsed(): void {
    const now = performance.now();
    if (now - lastDrawerToggleAtRef.current < 200) {
      return;
    }

    lastDrawerToggleAtRef.current = now;
    setIsDrawerCollapsed((currentValue) => {
      const nextValue = !currentValue;
      window.localStorage.setItem(DRAWER_STORAGE_KEY, String(nextValue));
      return nextValue;
    });
  }

  function handleDrawerControl(): void {
    if (window.matchMedia("(max-width: 1120px)").matches) {
      setIsMobileDrawerOpen((currentValue) => !currentValue);
      return;
    }

    handleToggleCollapsed();
  }

  return (
    <div
      className={`workbench-shell${isDrawerCollapsed ? " workbench-shell-collapsed" : ""}${isMobileDrawerOpen ? " workbench-shell-mobile-open" : ""}`}
    >
      <button
        className="workbench-shell-backdrop"
        type="button"
        aria-label="关闭导航抽屉"
        onClick={() => setIsMobileDrawerOpen(false)}
      />

      <aside className="workbench-shell-drawer" id="workbench-shell-drawer">
        <WorkbenchRail
          collapsed={isDrawerCollapsed}
          mobileOpen={isMobileDrawerOpen}
          onToggleCollapsed={handleDrawerControl}
          onDismissMobile={() => setIsMobileDrawerOpen(false)}
        />
      </aside>

      <div className="workbench-shell-main">
        <button
          className="workbench-shell-menu-button"
          type="button"
          aria-controls="workbench-shell-drawer"
          aria-expanded={isMobileDrawerOpen}
          aria-label={isMobileDrawerOpen ? "关闭导航" : "打开导航"}
          onClick={() => setIsMobileDrawerOpen((currentValue) => !currentValue)}
        >
          <span className="workbench-shell-menu-icon" aria-hidden="true" />
        </button>

        <div className="workbench-shell-stage">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
