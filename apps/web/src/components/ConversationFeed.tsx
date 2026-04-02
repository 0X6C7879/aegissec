import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatBytes } from "../lib/format";
import {
  extractSafeSessionSummary,
  isRecord,
  type SafeSessionSummaryEntry,
} from "../lib/sessionUtils";
import type { RuntimeExecutionRun } from "../types/runtime";
import type { SessionEventEntry, SessionMessage } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type ConversationFeedProps = {
  messages: SessionMessage[];
  events: SessionEventEntry[];
  runtimeRuns: RuntimeExecutionRun[];
  activeBranchId?: string | null;
  messageActionBusyId?: string | null;
  onEditMessage?: (message: SessionMessage) => void;
  onRegenerateMessage?: (message: SessionMessage) => void;
  onForkMessage?: (message: SessionMessage) => void;
  onRollbackMessage?: (message: SessionMessage) => void;
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
  toolName: string;
  command: string | null;
  status: string;
  exitCode: number | null;
  requestedTimeoutSeconds: number | null;
  stdout: string;
  stderr: string;
  artifacts: ToolArtifactChip[];
  arguments: Record<string, unknown> | null;
  result: unknown;
};

type ThoughtEntry =
  | {
      id: string;
      createdAt: string;
      kind: "summary";
      summary: SafeSessionSummaryEntry;
    }
  | {
      id: string;
      createdAt: string;
      kind: "trace";
      event: SessionEventEntry;
    };

type ConversationTurn = {
  id: string;
  createdAt: string;
  userMessage: SessionMessage;
  assistantMessages: SessionMessage[];
  thoughts: ThoughtEntry[];
  toolRuns: ToolDrawerRun[];
};

const THINK_BLOCK_PATTERN = /<think>([\s\S]*?)<\/think>/gi;

function toTimestamp(value: string): number {
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function parseAssistantContent(content: string): {
  visibleContent: string;
  thinkingBlocks: string[];
} {
  const thinkingBlocks: string[] = [];

  const visibleContent = content
    .replace(THINK_BLOCK_PATTERN, (_match, innerContent: string) => {
      const cleanedContent = innerContent.trim();
      if (cleanedContent.length > 0) {
        thinkingBlocks.push(cleanedContent);
      }
      return " ";
    })
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { visibleContent, thinkingBlocks };
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

  return "系统提示";
}

function renderTraceMeta(payload: unknown): string[] {
  if (!isRecord(payload)) {
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

function getToolSummary(run: ToolDrawerRun): string {
  if (run.command === null) {
    if (run.toolName === "list_available_skills" && isRecord(run.result)) {
      const skills = run.result.skills;
      if (Array.isArray(skills)) {
        return `${skills.length} skills`;
      }
    }

    if (
      run.toolName === "read_skill_content" &&
      isRecord(run.result) &&
      isRecord(run.result.skill)
    ) {
      const skill = run.result.skill;
      if (typeof skill.directory_name === "string" && skill.directory_name.length > 0) {
        return skill.directory_name;
      }
      if (typeof skill.name === "string" && skill.name.length > 0) {
        return skill.name;
      }
    }

    return "";
  }

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
    toolName: "execute_kali_command",
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
    arguments: null,
    result: {
      status: run.status,
      exit_code: run.exit_code,
      stdout: run.stdout,
      stderr: run.stderr,
      artifacts: run.artifacts.map((artifact) => artifact.relative_path),
    },
  };
}

function toToolDrawerRunFromEvent(event: SessionEventEntry): ToolDrawerRun | null {
  if (!event.type.startsWith("tool.call.") || !isRecord(event.payload)) {
    return null;
  }

  const toolName =
    typeof event.payload.tool === "string" && event.payload.tool.length > 0
      ? event.payload.tool
      : typeof event.payload.command === "string" && event.payload.command.length > 0
        ? "execute_kali_command"
        : null;

  if (toolName === null) {
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
        : event.type === "tool.call.finished"
          ? "completed"
          : "running";

  return {
    id: event.id,
    toolCallId: typeof event.payload.tool_call_id === "string" ? event.payload.tool_call_id : null,
    runtimeRunId: typeof event.payload.run_id === "string" ? event.payload.run_id : null,
    createdAt:
      typeof event.payload.created_at === "string" ? event.payload.created_at : event.createdAt,
    toolName,
    command: typeof event.payload.command === "string" ? event.payload.command : null,
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
    arguments: isRecord(event.payload.arguments) ? event.payload.arguments : null,
    result: event.payload.result,
  };
}

function hasMatchingRun(candidate: ToolDrawerRun, runtimeRuns: ToolDrawerRun[]): boolean {
  if (candidate.command === null) {
    return false;
  }

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

type ToolRunTerminalFailure = {
  createdAt: string;
  error: string;
};

function getLatestToolRunTerminalFailure(
  events: SessionEventEntry[],
): ToolRunTerminalFailure | null {
  let latestFailure: ToolRunTerminalFailure | null = null;

  events.forEach((event) => {
    if (!isRecord(event.payload)) {
      return;
    }

    const isAssistantError = event.type === "assistant.trace" && event.payload.status === "error";
    const isSessionError =
      event.type === "session.updated" &&
      event.payload.status === "error" &&
      typeof event.payload.error === "string";

    if (!isAssistantError && !isSessionError) {
      return;
    }

    const errorMessage =
      typeof event.payload.error === "string" && event.payload.error.length > 0
        ? event.payload.error
        : "Request ended before this tool call reported a result.";

    if (
      latestFailure === null ||
      toTimestamp(event.createdAt) >= toTimestamp(latestFailure.createdAt)
    ) {
      latestFailure = {
        createdAt: event.createdAt,
        error: errorMessage,
      };
    }
  });

  return latestFailure;
}

function buildToolEventRuns(events: SessionEventEntry[]): ToolDrawerRun[] {
  const runsByCorrelation = new Map<string, ToolDrawerRun>();

  [...events]
    .map((event) => toToolDrawerRunFromEvent(event))
    .filter((run): run is ToolDrawerRun => run !== null)
    .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt))
    .forEach((run) => {
      const correlationKey =
        run.toolCallId ?? run.runtimeRunId ?? `${run.command}:${run.createdAt}:${run.id}`;
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
        toolName: run.toolName || currentValue.toolName,
        command: run.command ?? currentValue.command,
        requestedTimeoutSeconds:
          run.requestedTimeoutSeconds ?? currentValue.requestedTimeoutSeconds,
        stdout: run.stdout || currentValue.stdout,
        stderr: run.stderr || currentValue.stderr,
        artifacts: run.artifacts.length > 0 ? run.artifacts : currentValue.artifacts,
        arguments: run.arguments ?? currentValue.arguments,
        result: run.result ?? currentValue.result,
      });
    });

  const terminalFailure = getLatestToolRunTerminalFailure(events);

  return [...runsByCorrelation.values()].map((run) => {
    if (
      terminalFailure === null ||
      run.status !== "running" ||
      toTimestamp(run.createdAt) > toTimestamp(terminalFailure.createdAt)
    ) {
      return run;
    }

    return {
      ...run,
      status: "failed",
      stderr: run.stderr || terminalFailure.error,
    };
  });
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
  const segments = command
    .trim()
    .split(/\s+/)
    .filter((value) => value.length > 0);
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

function getToolLabel(run: ToolDrawerRun): string {
  if (run.command !== null) {
    return "shell";
  }

  if (run.toolName === "list_available_skills" || run.toolName === "read_skill_content") {
    return "skill";
  }

  return run.toolName;
}

function getToolIntent(run: ToolDrawerRun): string {
  if (run.command !== null) {
    return getShellIntent(run.command);
  }

  if (run.toolName === "list_available_skills") {
    return "List loaded skills";
  }

  if (run.toolName === "read_skill_content") {
    const requestedSkill =
      run.arguments && typeof run.arguments.skill_name_or_id === "string"
        ? run.arguments.skill_name_or_id
        : null;
    return requestedSkill ? `Read skill ${requestedSkill}` : "Read skill instructions";
  }

  return `Call ${run.toolName}`;
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

function formatToolPayload(value: unknown): string | null {
  if (value === undefined || value === null) {
    return null;
  }

  if (typeof value === "string") {
    const trimmedValue = value.trim();
    return trimmedValue.length > 0 ? trimmedValue : null;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getSkillContentFromToolResult(result: unknown): { title: string; content: string } | null {
  if (!isRecord(result) || !isRecord(result.skill)) {
    return null;
  }

  const skill = result.skill;
  if (typeof skill.content !== "string" || skill.content.trim().length === 0) {
    return null;
  }

  const title =
    typeof skill.directory_name === "string" && skill.directory_name.length > 0
      ? skill.directory_name
      : typeof skill.name === "string" && skill.name.length > 0
        ? skill.name
        : "skill";

  return { title, content: skill.content };
}

function buildThoughtEntries(events: SessionEventEntry[]): ThoughtEntry[] {
  const summaryEntries = events
    .map((event) => {
      const summary = extractSafeSessionSummary(event.type, event.payload);
      if (!summary) {
        return null;
      }

      return {
        id: event.id,
        createdAt: event.createdAt,
        kind: "summary" as const,
        summary,
      };
    })
    .filter(
      (
        entry,
      ): entry is {
        id: string;
        createdAt: string;
        kind: "summary";
        summary: SafeSessionSummaryEntry;
      } => entry !== null,
    );

  const traceEntries = events
    .filter(
      (event) =>
        event.type === "assistant.trace" &&
        isRecord(event.payload) &&
        event.payload.status === "error",
    )
    .map(
      (event) =>
        ({
          id: event.id,
          createdAt: event.createdAt,
          kind: "trace" as const,
          event,
        }) satisfies ThoughtEntry,
    );

  return [...summaryEntries, ...traceEntries].sort(
    (left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt),
  );
}

function buildTurns(
  messages: SessionMessage[],
  thoughts: ThoughtEntry[],
  toolRuns: ToolDrawerRun[],
): {
  turns: ConversationTurn[];
  orphanMessages: SessionMessage[];
  orphanThoughts: ThoughtEntry[];
  orphanToolRuns: ToolDrawerRun[];
} {
  const sortedMessages = [...messages].sort(
    (left, right) => toTimestamp(left.created_at) - toTimestamp(right.created_at),
  );
  const userMessages = sortedMessages.filter((message) => message.role === "user");

  if (userMessages.length === 0) {
    return {
      turns: [],
      orphanMessages: sortedMessages,
      orphanThoughts: thoughts,
      orphanToolRuns: toolRuns,
    };
  }

  const firstUserTimestamp = toTimestamp(userMessages[0].created_at);
  const turns = userMessages.map((userMessage, index) => {
    const nextUserMessage = userMessages[index + 1] ?? null;
    const start = toTimestamp(userMessage.created_at);
    const end = nextUserMessage
      ? toTimestamp(nextUserMessage.created_at)
      : Number.POSITIVE_INFINITY;

    return {
      id: userMessage.id,
      createdAt: userMessage.created_at,
      userMessage,
      assistantMessages: sortedMessages.filter((message) => {
        if (message.role === "user") {
          return false;
        }

        const timestamp = toTimestamp(message.created_at);
        return timestamp >= start && timestamp < end;
      }),
      thoughts: thoughts.filter((entry) => {
        const timestamp = toTimestamp(entry.createdAt);
        return timestamp >= start && timestamp < end;
      }),
      toolRuns: toolRuns.filter((run) => {
        const timestamp = toTimestamp(run.createdAt);
        return timestamp >= start && timestamp < end;
      }),
    } satisfies ConversationTurn;
  });

  return {
    turns,
    orphanMessages: sortedMessages.filter(
      (message) => message.role !== "user" && toTimestamp(message.created_at) < firstUserTimestamp,
    ),
    orphanThoughts: thoughts.filter((entry) => toTimestamp(entry.createdAt) < firstUserTimestamp),
    orphanToolRuns: toolRuns.filter((run) => toTimestamp(run.createdAt) < firstUserTimestamp),
  };
}

export function ConversationFeed({
  messages,
  events,
  runtimeRuns,
  activeBranchId,
  messageActionBusyId,
  onEditMessage,
  onRegenerateMessage,
  onForkMessage,
  onRollbackMessage,
}: ConversationFeedProps) {
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);
  const [expandedToolGroupIds, setExpandedToolGroupIds] = useState<string[]>([]);
  const [expandedToolRunIds, setExpandedToolRunIds] = useState<string[]>([]);

  const sortedRuntimeRuns = useMemo(
    () =>
      [...runtimeRuns]
        .map((run) => toToolDrawerRun(run))
        .sort((left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt)),
    [runtimeRuns],
  );

  const fallbackToolRuns = useMemo(
    () => buildToolEventRuns(events).filter((run) => !hasMatchingRun(run, sortedRuntimeRuns)),
    [events, sortedRuntimeRuns],
  );

  const toolRuns = useMemo(
    () =>
      [...sortedRuntimeRuns, ...fallbackToolRuns].sort(
        (left, right) => toTimestamp(left.createdAt) - toTimestamp(right.createdAt),
      ),
    [fallbackToolRuns, sortedRuntimeRuns],
  );

  const thoughts = useMemo(() => buildThoughtEntries(events), [events]);

  const { turns, orphanMessages, orphanThoughts, orphanToolRuns } = useMemo(
    () => buildTurns(messages, thoughts, toolRuns),
    [messages, thoughts, toolRuns],
  );

  const lastItemSignature = useMemo(() => {
    const lastMessage = messages[messages.length - 1];
    const lastEvent = events[events.length - 1];
    const lastRun = runtimeRuns[runtimeRuns.length - 1];

    return [
      messages.length,
      lastMessage?.id ?? "none",
      lastMessage?.content.length ?? 0,
      events.length,
      lastEvent?.id ?? "none",
      lastEvent?.summary.length ?? 0,
      runtimeRuns.length,
      lastRun?.id ?? "none",
      lastRun?.stdout.length ?? 0,
      lastRun?.stderr.length ?? 0,
    ].join(":");
  }, [events, messages, runtimeRuns]);

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
    const validTurnIds = new Set(
      turns.filter((turn) => turn.toolRuns.length > 0).map((turn) => turn.id),
    );
    const validRunIds = new Set(toolRuns.map((run) => run.id));

    setExpandedToolGroupIds((currentValue) => {
      const nextValue = currentValue.filter((id) => validTurnIds.has(id));
      const lastTurn = turns[turns.length - 1];

      if (
        lastTurn &&
        lastTurn.toolRuns.length > 0 &&
        !nextValue.includes(lastTurn.id) &&
        (lastTurn.assistantMessages.length === 0 ||
          lastTurn.toolRuns.some((run) => run.status === "running"))
      ) {
        nextValue.push(lastTurn.id);
      }

      return nextValue;
    });

    setExpandedToolRunIds((currentValue) => currentValue.filter((id) => validRunIds.has(id)));
  }, [toolRuns, turns]);

  function toggleToolGroup(turnId: string): void {
    setExpandedToolGroupIds((currentValue) =>
      currentValue.includes(turnId)
        ? currentValue.filter((value) => value !== turnId)
        : [...currentValue, turnId],
    );
  }

  function scrollToolRunIntoView(runId: string): void {
    window.requestAnimationFrame(() => {
      const currentFeed = feedRef.current;
      if (!currentFeed) {
        return;
      }

      const drawer = currentFeed.querySelector<HTMLElement>(`[data-tool-run-id="${runId}"]`);
      if (!drawer) {
        return;
      }

      const composer = currentFeed.parentElement?.querySelector(".workbench-composer-shell");
      const feedRect = currentFeed.getBoundingClientRect();
      const drawerRect = drawer.getBoundingClientRect();
      const composerTop =
        composer instanceof HTMLElement ? composer.getBoundingClientRect().top : feedRect.bottom;
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

  function toggleToolRun(runId: string): void {
    const isOpening = !expandedToolRunIds.includes(runId);

    setExpandedToolRunIds((currentValue) =>
      currentValue.includes(runId)
        ? currentValue.filter((value) => value !== runId)
        : [...currentValue, runId],
    );

    if (isOpening) {
      scrollToolRunIntoView(runId);
    }
  }

  function renderMessageBubble(message: SessionMessage) {
    const assistantContent =
      message.role === "assistant" || message.role === "system"
        ? parseAssistantContent(message.content)
        : null;
    const isBusy = messageActionBusyId === message.id;
    const canEdit = message.role === "user" && typeof onEditMessage === "function";
    const canRegenerate = message.role === "assistant" && typeof onRegenerateMessage === "function";
    const canFork = typeof onForkMessage === "function";
    const canRollback = typeof onRollbackMessage === "function";

    return (
      <article key={message.id} className={`chat-bubble chat-bubble-${message.role}`}>
        <div className="assistant-tool-run-heading">
          <strong className="assistant-tool-run-label">{message.role}</strong>
          <div className="assistant-tool-run-side">
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {message.status && message.status !== "completed" ? <StatusBadge status={message.status} /> : null}
          </div>
        </div>
        {message.role === "user"
          ? renderUserMessage(message.content)
          : renderAssistantMessage(assistantContent?.visibleContent ?? message.content)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {canEdit || canRegenerate || canFork || canRollback ? (
          <div className="management-action-row">
            {canEdit ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onEditMessage?.(message)}
              >
                编辑
              </button>
            ) : null}
            {canRegenerate ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onRegenerateMessage?.(message)}
              >
                重试
              </button>
            ) : null}
            {canFork ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onForkMessage?.(message)}
              >
                分叉
              </button>
            ) : null}
            {canRollback ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onRollbackMessage?.(message)}
              >
                回溯到此
              </button>
            ) : null}
          </div>
        ) : null}
      </article>
    );
  }

  function renderThinkingBlock(messageId: string, content: string, index: number) {
    return (
      <article
        key={`${messageId}-thinking-${index}`}
        className="assistant-reasoning-item assistant-reasoning-item-connected"
      >
        <span className="assistant-reasoning-label">模型思路</span>
        <div className="assistant-reasoning-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      </article>
    );
  }

  function renderThought(entry: ThoughtEntry) {
    if (entry.kind === "summary") {
      return (
        <article
          key={entry.id}
          className={`assistant-reasoning-item assistant-reasoning-item-${entry.summary.tone}`}
        >
          <span className="assistant-reasoning-label">{entry.summary.label}</span>
          <p className="assistant-reasoning-text">{entry.summary.summary}</p>
        </article>
      );
    }

    const meta = renderTraceMeta(entry.event.payload);

    return (
      <article key={entry.id} className="assistant-reasoning-item assistant-reasoning-item-error">
        <div className="assistant-reasoning-header">
          <span className="assistant-reasoning-label">{getTraceLabel(entry.event)}</span>
          {meta[0] ? <span className="assistant-reasoning-meta">{meta[0]}</span> : null}
        </div>
        <p className="assistant-reasoning-text">{entry.event.summary}</p>
        {meta.length > 1 ? (
          <div className="assistant-reasoning-tags">
            {meta.slice(1).map((value) => (
              <span key={value} className="assistant-reasoning-tag">
                {value}
              </span>
            ))}
          </div>
        ) : null}
      </article>
    );
  }

  function renderToolRun(run: ToolDrawerRun) {
    const combinedOutput = getCombinedToolOutput(run);
    const toolSummary = getToolSummary(run);
    const isOpen = expandedToolRunIds.includes(run.id);
    const requestPayload = formatToolPayload(run.arguments);
    const genericResultPayload = formatToolPayload(run.result);
    const skillContent = getSkillContentFromToolResult(run.result);

    return (
      <article
        key={run.id}
        className={`assistant-tool-run${isOpen ? " assistant-tool-run-open" : ""}`}
        data-tool-run-id={run.id}
      >
        <button
          className="assistant-tool-run-toggle"
          type="button"
          onClick={() => toggleToolRun(run.id)}
        >
          <div className="assistant-tool-run-copy">
            <div className="assistant-tool-run-heading">
              <strong className="assistant-tool-run-label">{getToolLabel(run)}</strong>
              <span className="assistant-tool-run-intent">{getToolIntent(run)}</span>
            </div>
            {toolSummary ? <span className="assistant-tool-run-meta">{toolSummary}</span> : null}
          </div>
          <div className="assistant-tool-run-side">
            <StatusBadge status={run.status} />
            <span className="drawer-chevron" aria-hidden="true" />
          </div>
        </button>

        {isOpen ? (
          <div className="assistant-tool-run-body">
            {run.command !== null ? (
              <div className="shell-terminal-block">
                <pre className="shell-terminal-code">{`$ ${run.command}${combinedOutput ? `\n\n${combinedOutput}` : "\n\n# 无输出"}`}</pre>
              </div>
            ) : null}

            {requestPayload && run.command === null ? (
              <div className="shell-terminal-block">
                <pre className="shell-terminal-code">{requestPayload}</pre>
              </div>
            ) : null}

            {skillContent ? (
              <div className="shell-terminal-block">
                <pre className="shell-terminal-code">{skillContent.content}</pre>
              </div>
            ) : null}

            {run.command === null && !skillContent && genericResultPayload ? (
              <div className="shell-terminal-block">
                <pre className="shell-terminal-code">{genericResultPayload}</pre>
              </div>
            ) : null}

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
  }

  const hasContent =
    turns.length > 0 ||
    orphanMessages.length > 0 ||
    orphanThoughts.length > 0 ||
    orphanToolRuns.length > 0;

  if (!hasContent) {
    return (
      <section ref={feedRef} className="conversation-feed conversation-feed-empty">
        <div className="conversation-feed-empty-card">
          <p className="conversation-feed-empty-title">从这里开始新的对话</p>
          <p className="conversation-feed-empty-copy">
            发送第一条提示后，这里会按正常聊天顺序展示消息、思路摘要与执行过程。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section ref={feedRef} className="conversation-feed conversation-feed-threaded">
      {orphanMessages.map((message) => renderMessageBubble(message))}
      {orphanThoughts.map((entry) => renderThought(entry))}
      {orphanToolRuns.length > 0 ? (
        <section className="assistant-tool-group assistant-tool-group-orphan">
          <div className="assistant-tool-group-header">
            <span className="assistant-tool-group-title">执行过程</span>
            <span className="assistant-tool-group-count">{orphanToolRuns.length} 步</span>
          </div>
          <div className="assistant-tool-group-list">
            {orphanToolRuns.map((run) => renderToolRun(run))}
          </div>
        </section>
      ) : null}

      {turns.map((turn) => {
        const isToolGroupOpen = expandedToolGroupIds.includes(turn.id);
        const inlineThinkingBlocks = turn.assistantMessages.flatMap((message) => {
          const parsedContent = parseAssistantContent(message.content);
          return parsedContent.thinkingBlocks.map((content, index) =>
            renderThinkingBlock(message.id, content, index),
          );
        });

        return (
          <article key={turn.id} className="chat-turn">
            {renderMessageBubble(turn.userMessage)}

            {inlineThinkingBlocks.length > 0 ||
            turn.thoughts.length > 0 ||
            turn.toolRuns.length > 0 ||
            turn.assistantMessages.length > 0 ? (
              <section className="assistant-turn-card">
                {inlineThinkingBlocks.length > 0 || turn.thoughts.length > 0 ? (
                  <section className="assistant-reasoning-panel">
                    <div className="assistant-section-header">
                      <span className="assistant-section-kicker">思考过程</span>
                      <span className="assistant-section-count">
                        {inlineThinkingBlocks.length + turn.thoughts.length}
                      </span>
                    </div>
                    <div className="assistant-reasoning-list">
                      {inlineThinkingBlocks}
                      {turn.thoughts.map((entry) => renderThought(entry))}
                    </div>
                  </section>
                ) : null}

                {turn.toolRuns.length > 0 ? (
                  <section
                    className={`assistant-tool-group${isToolGroupOpen ? " assistant-tool-group-open" : ""}`}
                  >
                    <button
                      className="assistant-tool-group-toggle"
                      type="button"
                      onClick={() => toggleToolGroup(turn.id)}
                    >
                      <div className="assistant-tool-group-header">
                        <span className="assistant-tool-group-title">执行过程</span>
                        <span className="assistant-tool-group-count">
                          {turn.toolRuns.length} 步
                        </span>
                      </div>
                      <span className="drawer-chevron" aria-hidden="true" />
                    </button>

                    {isToolGroupOpen ? (
                      <div className="assistant-tool-group-list">
                        {turn.toolRuns.map((run) => renderToolRun(run))}
                      </div>
                    ) : null}
                  </section>
                ) : null}

                {turn.assistantMessages.length > 0 ? (
                  turn.assistantMessages.map((message) => renderMessageBubble(message))
                ) : turn.thoughts.length > 0 || turn.toolRuns.length > 0 ? (
                  <article className="chat-bubble chat-bubble-assistant">
                    {renderAssistantMessage("")}
                  </article>
                ) : null}
              </section>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
