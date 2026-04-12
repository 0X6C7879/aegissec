import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { generateClientId } from "../lib/uuid";
import type { SessionContextWindowUsage } from "../types/sessions";

type ContextWindowPopoverProps = {
  usage: SessionContextWindowUsage | null;
  loading: boolean;
  compacting: boolean;
  manualCompactDisabled: boolean;
  onManualCompact?: () => Promise<void>;
};

function formatTokenCount(value: number): string {
  if (!Number.isFinite(value)) {
    return "0";
  }

  if (Math.abs(value) >= 1000) {
    return `${(value / 1000).toFixed(1)}K`;
  }

  return String(value);
}

function formatShareRatio(value: number): string {
  if (!Number.isFinite(value)) {
    return "0%";
  }

  return `${Math.round(value * 100)}%`;
}

function formatDateTime(value: string | null): string | null {
  if (!value) {
    return null;
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return null;
  }

  return timestamp.toLocaleString("zh-CN", { hour12: false });
}

export function ContextWindowPopover({
  usage,
  loading,
  compacting,
  manualCompactDisabled,
  onManualCompact,
}: ContextWindowPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const popoverId = useMemo(() => `context-window-popover-${generateClientId()}`, []);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent): void {
      if (!shellRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  const usageRatio = usage?.usage_ratio ?? 0;
  const autoCompactThreshold = usage?.auto_compact_threshold_ratio ?? 0.8;
  const isWarning = usage !== null && usageRatio >= autoCompactThreshold;
  const canCompact = !manualCompactDisabled && usage?.can_manual_compact !== false && !compacting;
  const blockingReason = manualCompactDisabled
    ? "当前生成仍在进行，暂不支持手动压缩。"
    : usage?.blocking_reason ?? null;
  const usedTokensLabel = usage ? formatTokenCount(usage.used_tokens) : "--";
  const totalTokensLabel = usage ? formatTokenCount(usage.context_window_tokens) : "--";
  const normalizedUsageRatio = Math.max(0, Math.min(usageRatio, 1));
  const ringOpacity = usage ? 0.24 + normalizedUsageRatio * 0.58 : loading ? 0.16 : 0.12;
  const ringStyle = {
    "--context-usage-ratio": normalizedUsageRatio.toFixed(3),
    "--context-ring-opacity": ringOpacity.toFixed(3),
  } as CSSProperties;
  const progressWidth = `${Math.max(0, Math.min(usageRatio, 1)) * 100}%`;
  const lastCompactedLabel = formatDateTime(usage?.last_compacted_at ?? null);

  return (
    <div ref={shellRef} className="context-window-shell">
      <button
        type="button"
        className={`context-window-trigger context-window-trigger-compact${isWarning ? " context-window-trigger-warning" : ""}`}
        aria-label="上下文窗口"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={popoverId}
        title="上下文窗口"
        onClick={() => setIsOpen((currentValue) => !currentValue)}
      >
        <span className="context-window-trigger-ring" style={ringStyle} aria-hidden="true" />
      </button>

      {isOpen ? (
        <div id={popoverId} className="context-window-popover" role="dialog" aria-label="上下文窗口">
          <div className="context-window-popover-header">
            <div>
              <strong className="context-window-popover-title">上下文窗口</strong>
              <p className="context-window-popover-copy">{usage?.model ?? "当前会话"}</p>
            </div>
            <span className="context-window-popover-metric">
              {usedTokensLabel} / {totalTokensLabel}
            </span>
          </div>

          <div className="context-window-progress-shell" aria-hidden="true">
            <div className="context-window-progress-track">
              <div className="context-window-progress-bar" style={{ width: progressWidth }} />
            </div>
            <span className="context-window-progress-label">{formatShareRatio(usageRatio)}</span>
          </div>

          {usage ? (
            <>
              <div className="context-window-meta-grid">
                <div className="context-window-meta-card">
                  <span className="context-window-meta-label">响应预留</span>
                  <strong>{formatTokenCount(usage.reserved_response_tokens)}</strong>
                </div>
                <div className="context-window-meta-card">
                  <span className="context-window-meta-label">最近压缩</span>
                  <strong>{lastCompactedLabel ?? "暂无"}</strong>
                </div>
              </div>

              <div className="context-window-breakdown">
                {usage.breakdown.map((item) => (
                  <div key={item.key} className="context-window-breakdown-row">
                    <span className="context-window-breakdown-label">{item.label}</span>
                    <span className="context-window-breakdown-value">
                      {formatTokenCount(item.estimated_tokens)} · {formatShareRatio(item.share_ratio)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="context-window-empty-copy">{loading ? "正在读取上下文使用情况…" : "暂无上下文数据。"}</p>
          )}

          {blockingReason ? <p className="context-window-blocking-copy">{blockingReason}</p> : null}

          <div className="context-window-popover-footer">
            <button
              type="button"
              className="context-window-compact-action"
              disabled={!canCompact || !onManualCompact}
              onClick={() => {
                if (!onManualCompact || !canCompact) {
                  return;
                }

                void onManualCompact();
              }}
            >
              {compacting ? "压缩中..." : "压缩对话"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
