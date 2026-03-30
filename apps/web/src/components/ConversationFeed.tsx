import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatBytes } from "../lib/format";
import { isRecord } from "../lib/sessionUtils";
import type { RuntimeExecutionRun } from "../types/runtime";
import type { SessionEventEntry, SessionMessage } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type ConversationFeedProps = {
  messages: SessionMessage[];
  events: SessionEventEntry[];
  runtimeRuns: RuntimeExecutionRun[];
};

type ToolArtifactChip = {
  id: string;
  relativePath: string;
};

type ToolDrawerRun = {
  id: string;
  toolCallId: string | null;
  runtimeRunId: string | null;
  createdAt: string;
  command: string;
  status: string;
  exitCode: number | null;
  requestedTimeoutSeconds: number | null;
  stdout: string;
  stderr: string;
  artifacts: ToolArtifactChip[];
};

type FeedItem =
  | { id: string; createdAt: string; kind: "message"; order: number; message: SessionMessage }
  | { id: string; createdAt: string; kind: "trace"; order: number; event: SessionEventEntry }
  | { id: string; createdAt: string; kind: "tool"; order: number; run: ToolDrawerRun };

function getFeedItemPriority(item: FeedItem): number {
  if (item.kind === "message") {
    return item.message.role === "user" ? 0 : 3;
  }

  if (item.kind === "trace") {
    return 1;
  }

  return 2;
}

function toTimestamp(value: string): number {
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function byCreatedAt(left: FeedItem, right: FeedItem): number {
  const timestampDiff = toTimestamp(left.createdAt) - toTimestamp(right.createdAt);
  if (timestampDiff !== 0) {
    return timestampDiff;
  }

  const priorityDiff = getFeedItemPriority(left) - getFeedItemPriority(right);
  if (priorityDiff !== 0) {
    return priorityDiff;
  }

  return left.order - right.order;
}

function getTraceLabel(event: SessionEventEntry): string {
  if (event.type === "assistant.trace") {
    return "请求异常";
  }

  if (event.type === "tool.call.failed") {
    return "工具异常";
  }

  if (event.type === "session.deleted" || event.type === "session.restored") {
    return "会话事件";
  }

  return "提示";
}

function renderTraceMeta(payload: unknown): string[] {
  if (!isRecord(payload)) {
    return [];
  }

  if (typeof payload.phase === "string") {
    if (typeof payload.error === "string") {
      return [`异常 · ${payload.error}`];
    }

    return [];
  }

  const meta: string[] = [];
  if (typeof payload.status === "string") {
    meta.push(`状态 · ${payload.status}`);
  }
  if (typeof payload.command === "string") {
    meta.push(`命令 · ${payload.command}`);
  }
  if (typeof payload.error === "string") {
    meta.push(`异常 · ${payload.error}`);
  }
  return meta;
}

function renderUserMessage(content: string) {
  return <div className="chat-bubble-plain">{content}</div>;
}

function renderAssistantMessage(content: string) {
  if (!content.trim()) {
    return (
      <span className="chat-bubble-streaming-indicator" role="status" aria-live="polite">
        <span />
        <span />
        <span />
      </span>
    );
  }

  return (
    <div className="chat-bubble-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function getToolSummary(run: ToolDrawerRun): string {
  const successStatuses = new Set(["completed", "success", "succeeded"]);
  const timeoutStatuses = new Set(["timeout", "timed_out"]);
  const meta: string[] = [];

  if (!successStatuses.has(run.status) && run.exitCode !== null) {
    meta.push(`退出码 ${run.exitCode}`);
  }

  if (timeoutStatuses.has(run.status) && run.requestedTimeoutSeconds !== null) {
    meta.push(`超时 ${run.requestedTimeoutSeconds}s`);
  }

  return meta.join(" · ");
}

function getStringArray(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function toToolArtifacts(paths: string[], baseId: string): ToolArtifactChip[] {
  return paths.map((relativePath, index) => ({
    id: `${baseId}-${index}`,
    relativePath,
  }));
}

function toToolDrawerRun(run: RuntimeExecutionRun): ToolDrawerRun {
  return {
    id: run.id,
    toolCallId: null,
    runtimeRunId: run.id,
    createdAt: run.created_at,
    command: run.command,
    status: run.status,
    exitCode: run.exit_code,
    requestedTimeoutSeconds: run.requested_timeout_seconds,
    stdout: run.stdout,
    stderr: run.stderr,
    artifacts: run.artifacts.map((artifact) => ({
      id: artifact.id,
      relativePath: artifact.relative_path,
    })),
  };
}

function toToolDrawerRunFromEvent(event: SessionEventEntry): ToolDrawerRun | null {
  if (!event.type.startsWith("tool.call.") || !isRecord(event.payload)) {
    return null;
  }

  if (typeof event.payload.command !== "string" || event.payload.command.length === 0) {
    return null;
  }

  const requestedTimeoutSeconds =
    typeof event.payload.requested_timeout_seconds === "number"
      ? event.payload.requested_timeout_seconds
      : typeof event.payload.timeout_seconds === "number"
        ? event.payload.timeout_seconds
        : null;

  const status =
    typeof event.payload.status === "string"
      ? event.payload.status
      : event.type === "tool.call.failed"
        ? "failed"
        : "running";

  return {
    id: event.id,
    toolCallId: typeof event.payload.tool_call_id === "string" ? event.payload.tool_call_id : null,
    runtimeRunId: typeof event.payload.run_id === "string" ? event.payload.run_id : null,
    createdAt: typeof event.payload.created_at === "string" ? event.payload.created_at : event.createdAt,
    command: event.payload.command,
    status,
    exitCode: typeof event.payload.exit_code === "number" ? event.payload.exit_code : null,
    requestedTimeoutSeconds,
    stdout: typeof event.payload.stdout === "string" ? event.payload.stdout : "",
    stderr:
      typeof event.payload.stderr === "string"
        ? event.payload.stderr
        : typeof event.payload.error === "string"
          ? event.payload.error
          : "",
    artifacts: toToolArtifacts(getStringArray(event.payload, "artifact_paths"), event.id),
  };
}

function hasMatchingRun(candidate: ToolDrawerRun, runtimeRuns: ToolDrawerRun[]): boolean {
  if (candidate.runtimeRunId) {
    return runtimeRuns.some((run) => run.runtimeRunId === candidate.runtimeRunId);
  }

  const candidateTimestamp = toTimestamp(candidate.createdAt);

  return runtimeRuns.some((run) => {
    if (run.command !== candidate.command) {
      return false;
    }

    return Math.abs(toTimestamp(run.createdAt) - candidateTimestamp) <= 60_000;
  });
}

function buildToolEventRuns(events: SessionEventEntry[]): ToolDrawerRun[] {
  const runsByCorrelation = new Map<string, ToolDrawerRun>();

  [...events]
    .map((event) => toToolDrawerRunFromEvent(event))
    .filter((run): run is ToolDrawerRun => run !== null)
    .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt))
    .forEach((run) => {
      const correlationKey = run.toolCallId ?? run.runtimeRunId ?? `${run.command}:${run.createdAt}:${run.id}`;
      const currentValue = runsByCorrelation.get(correlationKey);
      if (!currentValue) {
        runsByCorrelation.set(correlationKey, run);
        return;
      }

      runsByCorrelation.set(correlationKey, {
        ...currentValue,
        ...run,
        toolCallId: run.toolCallId ?? currentValue.toolCallId,
        runtimeRunId: run.runtimeRunId ?? currentValue.runtimeRunId,
        requestedTimeoutSeconds: run.requestedTimeoutSeconds ?? currentValue.requestedTimeoutSeconds,
        stdout: run.stdout || currentValue.stdout,
        stderr: run.stderr || currentValue.stderr,
        artifacts: run.artifacts.length > 0 ? run.artifacts : currentValue.artifacts,
      });
    });

  return [...runsByCorrelation.values()];
}

function extractOptionValue(command: string, flags: string[]): string | null {
  const segments = command.trim().split(/\s+/);
  for (let index = 0; index < segments.length; index += 1) {
    if (flags.includes(segments[index]) && segments[index + 1]) {
      return segments[index + 1];
    }
  }

  return null;
}

function extractCommandTarget(command: string): string | null {
  const segments = command.trim().split(/\s+/).filter((value) => value.length > 0);
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (!segment.startsWith("-")) {
      return segment;
    }
  }

  return null;
}

function getShellIntent(command: string): string {
  if (command.includes("nmap")) {
    const target = extractCommandTarget(command) ?? "目标主机";
    const port = extractOptionValue(command, ["-p", "--port"]);
    return port ? `扫描 ${target} 的 ${port} 端口` : `扫描 ${target} 的开放端口`;
  }

  if (/(^|\s)(curl|wget)(\s|$)/.test(command)) {
    return "请求目标资源并查看响应";
  }

  if (/(^|\s)(cat|grep|tail|head)(\s|$)/.test(command)) {
    return "查看并筛选命令输出";
  }

  if (/(^|\s)(ls|find)(\s|$)/.test(command)) {
    return "查看目录或搜索目标文件";
  }

  if (/(^|\s)(mkdir|touch|printf|echo)(\s|$)/.test(command)) {
    return "准备输出目录或写入结果文件";
  }

  if (/python\d*\s+-m\s+http\.server/.test(command)) {
    return "启动临时 HTTP 服务";
  }

  return "执行 shell 命令并返回结果";
}

function getCombinedToolOutput(run: ToolDrawerRun): string {
  const parts: string[] = [];
  if (run.stdout.trim()) {
    parts.push(run.stdout.trim());
  }
  if (run.stderr.trim()) {
    parts.push(`[stderr]\n${run.stderr.trim()}`);
  }
  return parts.join("\n\n");
}

function shouldAutoOpenDrawer(item: FeedItem): boolean {
  if (item.kind === "trace") {
    return (
      item.event.type === "assistant.trace" &&
      isRecord(item.event.payload) &&
      item.event.payload.status === "error"
    );
  }

  return false;
}

export function ConversationFeed({ messages, events, runtimeRuns }: ConversationFeedProps) {
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);
  const [openDrawerIds, setOpenDrawerIds] = useState<string[]>([]);

  const items = useMemo(() => {
    const sortedMessages = [...messages].sort(
      (left, right) => toTimestamp(left.created_at) - toTimestamp(right.created_at),
    );
    const sortedRuntimeRuns = [...runtimeRuns]
      .map((run) => toToolDrawerRun(run))
      .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt));
    const filteredTraceEvents = [...events]
      .filter((event) => {
        if (event.type !== "assistant.trace") {
          return false;
        }

        return isRecord(event.payload) && event.payload.status === "error";
      })
      .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt));
    const fallbackToolRuns = buildToolEventRuns(events).filter(
      (run) => !hasMatchingRun(run, sortedRuntimeRuns),
    );

    let order = 0;
    return [
      ...sortedMessages.map((message) => ({
        id: message.id,
        createdAt: message.created_at,
        kind: "message" as const,
        order: order++,
        message,
      })),
      ...filteredTraceEvents.map((event) => ({
        id: event.id,
        createdAt: event.createdAt,
        kind: "trace" as const,
        order: order++,
        event,
      })),
      ...[...sortedRuntimeRuns, ...fallbackToolRuns]
        .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt))
        .map((run) => ({
          id: run.id,
          createdAt: run.createdAt,
          kind: "tool" as const,
          order: order++,
          run,
        })),
    ].sort(byCreatedAt);
  }, [events, messages, runtimeRuns]);

  const lastItemSignature = useMemo(() => {
    const lastItem = items[items.length - 1];
    if (!lastItem) {
      return null;
    }

    if (lastItem.kind === "message") {
      return `${lastItem.id}:${lastItem.message.content.length}:${lastItem.message.attachments.length}`;
    }

    if (lastItem.kind === "tool") {
      return `${lastItem.id}:${lastItem.run.stdout.length}:${lastItem.run.stderr.length}`;
    }

    return `${lastItem.id}:${lastItem.event.summary.length}`;
  }, [items]);

  useEffect(() => {
    const feedElement = feedRef.current;
    if (!feedElement || !lastItemSignature) {
      return;
    }

    if (lastItemSignature === previousLastItemSignature.current) {
      return;
    }

    const distanceToBottom =
      feedElement.scrollHeight - feedElement.clientHeight - feedElement.scrollTop;
    const shouldStickToBottom =
      previousLastItemSignature.current === null || distanceToBottom <= 72;

    if (!shouldStickToBottom) {
      previousLastItemSignature.current = lastItemSignature;
      return;
    }

    feedElement.scrollTo({
      top: feedElement.scrollHeight,
      behavior: previousLastItemSignature.current === null ? "auto" : "smooth",
    });
    previousLastItemSignature.current = lastItemSignature;
  }, [lastItemSignature]);

  useEffect(() => {
    const itemIds = new Set(items.map((item) => item.id));

    setOpenDrawerIds((currentValue) => {
      const nextValue = currentValue.filter((id) => itemIds.has(id));
      const knownIds = new Set(nextValue);

      items.forEach((item) => {
        if (shouldAutoOpenDrawer(item) && !knownIds.has(item.id)) {
          nextValue.push(item.id);
          knownIds.add(item.id);
        }
      });

      return nextValue;
    });
  }, [items]);

  function toggleDrawer(id: string): void {
    setOpenDrawerIds((currentValue) =>
      currentValue.includes(id) ? currentValue.filter((value) => value !== id) : [...currentValue, id],
    );
  }

  function scrollShellDrawerIntoView(drawerId: string): void {
    window.requestAnimationFrame(() => {
      const currentFeed = feedRef.current;
      if (!currentFeed) {
        return;
      }

      const drawer = currentFeed.querySelector<HTMLElement>(`[data-tool-drawer-id="${drawerId}"]`);
      if (!drawer) {
        return;
      }

      const composer = currentFeed.parentElement?.querySelector(".workbench-composer-shell");
      const feedRect = currentFeed.getBoundingClientRect();
      const drawerRect = drawer.getBoundingClientRect();
      const composerTop = composer instanceof HTMLElement ? composer.getBoundingClientRect().top : feedRect.bottom;
      const visibleTop = feedRect.top + 12;
      const visibleBottom = Math.min(feedRect.bottom, composerTop) - 18;

      if (drawerRect.bottom > visibleBottom) {
        currentFeed.scrollBy({
          top: drawerRect.bottom - visibleBottom + 18,
          behavior: "smooth",
        });
        return;
      }

      if (drawerRect.top < visibleTop) {
        currentFeed.scrollBy({
          top: drawerRect.top - visibleTop - 18,
          behavior: "smooth",
        });
      }
    });
  }

  function handleToolDrawerClick(drawerId: string): void {
    const isOpening = !openDrawerIds.includes(drawerId);
    toggleDrawer(drawerId);

    if (isOpening) {
      scrollShellDrawerIntoView(drawerId);
    }
  }

  if (items.length === 0) {
    return <section ref={feedRef} className="conversation-feed conversation-feed-empty" />;
  }

  return (
    <section ref={feedRef} className="conversation-feed">
      {items.map((item) => {
        if (item.kind === "message") {
          const { message } = item;
          const isUserMessage = message.role === "user";

          return (
            <article key={item.id} className={`chat-bubble chat-bubble-${message.role}`}>
              {isUserMessage ? renderUserMessage(message.content) : renderAssistantMessage(message.content)}
              {message.attachments.length > 0 ? (
                <div className="chat-bubble-artifacts">
                  {message.attachments.map((attachment) => (
                    <span key={attachment.id} className="chat-artifact-chip">
                      {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
                    </span>
                  ))}
                </div>
              ) : null}
            </article>
          );
        }

        if (item.kind === "trace") {
          const meta = renderTraceMeta(item.event.payload);
          const isOpen = openDrawerIds.includes(item.id);
          return (
            <article key={item.id} className={`drawer-card trace-card${isOpen ? " drawer-card-open" : ""}`}>
              <button className="drawer-summary" type="button" onClick={() => toggleDrawer(item.id)}>
                <div className="drawer-summary-copy">
                  <div className="drawer-summary-heading">
                    <span className="trace-card-label">{getTraceLabel(item.event)}</span>
                    <span className="drawer-summary-inline">{item.event.summary}</span>
                  </div>
                  {meta[0] ? <span className="drawer-summary-text">{meta[0]}</span> : null}
                </div>
                <div className="drawer-summary-side">
                  <span className="drawer-chevron" aria-hidden="true" />
                </div>
              </button>
              {isOpen ? (
                <div className="drawer-body">
                  <p className="trace-card-summary">{item.event.summary}</p>
                  {meta.length > 0 ? (
                    <div className="trace-card-tags">
                      {meta.map((value) => (
                        <span key={value} className="trace-card-tag">
                          {value}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </article>
          );
        }

        const { run } = item;
        const combinedOutput = getCombinedToolOutput(run);
        const toolSummary = getToolSummary(run);

        const isOpen = openDrawerIds.includes(item.id);

        return (
          <article
            key={item.id}
            className={`drawer-card tool-card shell-drawer${isOpen ? " drawer-card-open" : ""}`}
            data-tool-drawer-id={item.id}
          >
            <button
              className="drawer-summary shell-drawer-toggle"
              type="button"
              onClick={() => handleToolDrawerClick(item.id)}
            >
              <div className="drawer-summary-copy shell-drawer-copy">
                <div className="drawer-summary-heading shell-drawer-heading">
                  <strong className="shell-drawer-label">shell</strong>
                  <span className="shell-drawer-intent">{getShellIntent(run.command)}</span>
                </div>
                {toolSummary ? (
                  <span className="drawer-summary-text shell-drawer-meta-text">{toolSummary}</span>
                ) : null}
              </div>
              <div className="drawer-summary-side tool-card-status shell-drawer-side">
                <StatusBadge status={run.status} />
                <span className="drawer-chevron" aria-hidden="true" />
              </div>
            </button>
            {isOpen ? (
              <div className="drawer-body shell-drawer-body">
                <div className="shell-terminal-block">
                  <pre className="shell-terminal-code">{`$ ${run.command}${combinedOutput ? `\n\n${combinedOutput}` : "\n\n# 无输出"}`}</pre>
                </div>

                {run.artifacts.length > 0 ? (
                  <div className="chat-bubble-artifacts shell-drawer-artifacts">
                    {run.artifacts.map((artifact) => (
                      <span key={artifact.id} className="chat-artifact-chip">
                        {artifact.relativePath}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
