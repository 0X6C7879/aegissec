import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { useUiStore } from "../store/uiStore";

if (!HTMLElement.prototype.scrollTo) {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
    writable: true,
  });
}

if (!HTMLElement.prototype.scrollBy) {
  Object.defineProperty(HTMLElement.prototype, "scrollBy", {
    configurable: true,
    value: vi.fn(),
    writable: true,
  });
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-ui-density");
  useUiStore.setState({
    includeDeleted: false,
    isEventPanelOpen: true,
    lastVisitedSessionId: null,
    themePreference: "dark",
    uiDensity: "compact",
    draftsBySession: {},
    eventsBySession: {},
  });
});
