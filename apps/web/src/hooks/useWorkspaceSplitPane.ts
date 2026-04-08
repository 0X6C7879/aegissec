import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

export const WORKSPACE_SPLIT_PANE_STORAGE_KEY = "aegissec.workspace.chat-pane.ratio.v1";

const DEFAULT_RIGHT_RATIO = 0.74 / (1.72 + 0.74);
const MIN_LEFT_PANE_WIDTH = 480;
const MIN_RIGHT_PANE_WIDTH = 360;
const SPLITTER_SIZE = 12;
const KEYBOARD_STEP = 24;
const KEYBOARD_LARGE_STEP = 64;
const STACKED_MEDIA_QUERY = "(max-width: 1200px)";

type DragState = {
  pointerId: number;
  startClientX: number;
  startRightWidth: number;
};

type UseWorkspaceSplitPaneOptions = {
  controlledPaneId: string;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function readStoredRatio(): number {
  if (typeof window === "undefined") {
    return DEFAULT_RIGHT_RATIO;
  }

  const rawValue = window.localStorage.getItem(WORKSPACE_SPLIT_PANE_STORAGE_KEY);
  const parsedValue = rawValue ? Number.parseFloat(rawValue) : Number.NaN;

  return Number.isFinite(parsedValue) ? clamp(parsedValue, 0, 1) : DEFAULT_RIGHT_RATIO;
}

function isStackedViewport(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  if (typeof window.matchMedia === "function") {
    return window.matchMedia(STACKED_MEDIA_QUERY).matches;
  }

  return window.innerWidth <= 1200;
}

function measureWidth(element: HTMLElement): number {
  const measuredWidth = Math.round(element.getBoundingClientRect().width);
  const fallbackWidth = element.clientWidth || element.offsetWidth || window.innerWidth;

  return Math.max(0, measuredWidth || fallbackWidth);
}

function getMaxRightWidth(availableWidth: number): number {
  return Math.max(MIN_RIGHT_PANE_WIDTH, availableWidth - MIN_LEFT_PANE_WIDTH);
}

function getDefaultRightWidth(availableWidth: number): number {
  return clamp(availableWidth * DEFAULT_RIGHT_RATIO, MIN_RIGHT_PANE_WIDTH, getMaxRightWidth(availableWidth));
}

export function useWorkspaceSplitPane({ controlledPaneId }: UseWorkspaceSplitPaneOptions) {
  const containerRef = useRef<HTMLElement | null>(null);
  const [storedRatio, setStoredRatio] = useState<number>(() => readStoredRatio());
  const [containerWidth, setContainerWidth] = useState(() =>
    typeof window === "undefined" ? 0 : window.innerWidth,
  );
  const [isStacked, setIsStacked] = useState(() => isStackedViewport());
  const [dragState, setDragState] = useState<DragState | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(WORKSPACE_SPLIT_PANE_STORAGE_KEY, storedRatio.toString());
  }, [storedRatio]);

  useEffect(() => {
    const element = containerRef.current;

    if (!element) {
      return;
    }

    const syncWidth = () => {
      setContainerWidth(measureWidth(element));
    };

    syncWidth();

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(() => {
        syncWidth();
      });

      observer.observe(element);

      return () => {
        observer.disconnect();
      };
    }

    window.addEventListener("resize", syncWidth);

    return () => {
      window.removeEventListener("resize", syncWidth);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const syncStackedState = () => {
      setIsStacked(isStackedViewport());
    };

    syncStackedState();

    if (typeof window.matchMedia === "function") {
      const mediaQuery = window.matchMedia(STACKED_MEDIA_QUERY);

      if (typeof mediaQuery.addEventListener === "function") {
        mediaQuery.addEventListener("change", syncStackedState);

        return () => {
          mediaQuery.removeEventListener("change", syncStackedState);
        };
      }

      if (typeof mediaQuery.addListener === "function") {
        mediaQuery.addListener(syncStackedState);

        return () => {
          mediaQuery.removeListener(syncStackedState);
        };
      }
    }

    window.addEventListener("resize", syncStackedState);

    return () => {
      window.removeEventListener("resize", syncStackedState);
    };
  }, []);

  const availableWidth = Math.max(0, containerWidth - SPLITTER_SIZE);
  const isEnabled = !isStacked && availableWidth >= MIN_LEFT_PANE_WIDTH + MIN_RIGHT_PANE_WIDTH;
  const maxRightWidth = getMaxRightWidth(availableWidth);
  const rightPaneWidth = isEnabled
    ? clamp(availableWidth * storedRatio, MIN_RIGHT_PANE_WIDTH, maxRightWidth)
    : getDefaultRightWidth(Math.max(MIN_LEFT_PANE_WIDTH + MIN_RIGHT_PANE_WIDTH, availableWidth || MIN_LEFT_PANE_WIDTH + MIN_RIGHT_PANE_WIDTH));
  const leftPaneWidth = Math.max(MIN_LEFT_PANE_WIDTH, availableWidth - rightPaneWidth);

  const updateRightPaneWidth = useCallback(
    (nextRightPaneWidth: number) => {
      if (!isEnabled || availableWidth <= 0) {
        return;
      }

      const clampedRightPaneWidth = clamp(nextRightPaneWidth, MIN_RIGHT_PANE_WIDTH, maxRightWidth);
      setStoredRatio(clampedRightPaneWidth / availableWidth);
    },
    [availableWidth, isEnabled, maxRightWidth],
  );

  const handleSeparatorKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (!isEnabled) {
        return;
      }

      const step = event.shiftKey ? KEYBOARD_LARGE_STEP : KEYBOARD_STEP;

      switch (event.key) {
        case "ArrowLeft":
          event.preventDefault();
          updateRightPaneWidth(rightPaneWidth + step);
          break;
        case "ArrowRight":
          event.preventDefault();
          updateRightPaneWidth(rightPaneWidth - step);
          break;
        case "Home":
          event.preventDefault();
          updateRightPaneWidth(maxRightWidth);
          break;
        case "End":
          event.preventDefault();
          updateRightPaneWidth(MIN_RIGHT_PANE_WIDTH);
          break;
        default:
          break;
      }
    },
    [isEnabled, maxRightWidth, rightPaneWidth, updateRightPaneWidth],
  );

  const handleSeparatorPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!isEnabled) {
        return;
      }

      event.preventDefault();
      event.currentTarget.focus();
      setDragState({
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startRightWidth: rightPaneWidth,
      });
    },
    [isEnabled, rightPaneWidth],
  );

  useEffect(() => {
    if (!dragState || !isEnabled) {
      return;
    }

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerId !== dragState.pointerId) {
        return;
      }

      const deltaX = event.clientX - dragState.startClientX;
      updateRightPaneWidth(dragState.startRightWidth - deltaX);
    };

    const handlePointerRelease = (event: PointerEvent) => {
      if (event.pointerId !== dragState.pointerId) {
        return;
      }

      setDragState(null);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerRelease);
    window.addEventListener("pointercancel", handlePointerRelease);

    return () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerRelease);
      window.removeEventListener("pointercancel", handlePointerRelease);
    };
  }, [dragState, isEnabled, updateRightPaneWidth]);

  useEffect(() => {
    if (isEnabled) {
      return;
    }

    setDragState(null);
  }, [isEnabled]);

  const gridStyle = useMemo<CSSProperties | undefined>(() => {
    if (!isEnabled) {
      return undefined;
    }

    return {
      gridTemplateColumns: `minmax(0, 1fr) ${SPLITTER_SIZE}px minmax(${MIN_RIGHT_PANE_WIDTH}px, ${Math.round(rightPaneWidth)}px)`,
    };
  }, [isEnabled, rightPaneWidth]);

  const separatorProps = useMemo(
    () => ({
      "aria-controls": controlledPaneId,
      "aria-label": "调整图谱与聊天面板宽度",
      "aria-orientation": "vertical" as const,
      "aria-valuemax": Math.max(MIN_LEFT_PANE_WIDTH, Math.round(availableWidth - MIN_RIGHT_PANE_WIDTH)),
      "aria-valuemin": MIN_LEFT_PANE_WIDTH,
      "aria-valuenow": Math.round(leftPaneWidth),
      "aria-valuetext": `图谱区域 ${Math.round(leftPaneWidth)} 像素，聊天区域 ${Math.round(rightPaneWidth)} 像素`,
      onKeyDown: handleSeparatorKeyDown,
      onPointerDown: handleSeparatorPointerDown,
      role: "separator" as const,
      tabIndex: isEnabled ? 0 : -1,
    }),
    [
      availableWidth,
      controlledPaneId,
      handleSeparatorKeyDown,
      handleSeparatorPointerDown,
      isEnabled,
      leftPaneWidth,
      rightPaneWidth,
    ],
  );

  return {
    containerRef,
    gridStyle,
    isDragging: dragState !== null,
    isEnabled,
    separatorProps,
  };
}
