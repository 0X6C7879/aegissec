import { useMemo } from "react";
import type { ChatGeneration, SessionMessage } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type GenerationStep = {
  id: string;
  generationId: string;
  messageId: string | null;
  sequence: number | null;
  kind: string | null;
  phase: string | null;
  status: string | null;
  state: string | null;
  label: string | null;
  safeSummary: string | null;
  deltaText: string | null;
  toolName: string | null;
  toolCallId: string | null;
  command: string | null;
  startedAt: string | null;
  endedAt: string | null;
};

type TimelineItem = {
  id: string;
  kind: "reasoning" | "tool" | "output" | "status";
  title: string;
  summary: string;
  status: string;
  phase: string | null;
  toolCallId: string | null;
  charCount: number;
};

type GenerationRunTimelineProps = {
  generation: ChatGeneration;
  assistantMessage: SessionMessage | null;
};

type GenerationQueuePanelProps = {
  activeGeneration: ChatGeneration | null;
  queuedGenerations: ChatGeneration[];
  messages: SessionMessage[];
  cancelDisabled?: boolean;
  onCancelGeneration?: (generationId: string) => void;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function toTimestamp(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function getGenerationRecord(generation: ChatGeneration): Record<string, unknown> {
  return generation as unknown as Record<string, unknown>;
}

function formatGenerationAction(action: string): string {
  switch (action) {
    case "reply":
      return "回复";
    case "edit":
      return "编辑后重答";
    case "regenerate":
      return "重新生成";
    case "fork":
      return "分叉生成";
    case "rollback":
      return "回溯生成";
    default:
      return "生成";
  }
}

function formatPhaseLabel(phase: string | null): string | null {
  if (!phase) {
    return null;
  }

  switch (phase) {
    case "reasoning":
      return "推理";
    case "tool":
      return "工具";
    case "output":
      return "输出";
    case "status":
      return "状态";
    default:
      return phase.replace(/[_.]+/g, " · ");
  }
}

function getGenerationQueuePosition(generation: ChatGeneration): number | null {
  const record = getGenerationRecord(generation);
  const metadata = readRecord(record.metadata);

  return readNumber(record.queue_position) ?? readNumber(metadata?.queue_position);
}

function getGenerationSteps(generation: ChatGeneration): GenerationStep[] {
  const record = getGenerationRecord(generation);
  const metadata = readRecord(record.metadata);
  const rawSteps = Array.isArray(record.steps)
    ? record.steps
    : Array.isArray(metadata?.steps)
      ? metadata.steps
      : [];

  return rawSteps
    .map((rawStep, index) => {
      if (!isRecord(rawStep)) {
        return null;
      }

      return {
        id: readString(rawStep.id) ?? `${generation.id}:step:${index}`,
        generationId: readString(rawStep.generation_id) ?? generation.id,
        messageId: readString(rawStep.message_id),
        sequence: readNumber(rawStep.sequence),
        kind: readString(rawStep.kind),
        phase: readString(rawStep.phase),
        status: readString(rawStep.status),
        state: readString(rawStep.state),
        label: readString(rawStep.label),
        safeSummary: readString(rawStep.safe_summary),
        deltaText: readString(rawStep.delta_text),
        toolName: readString(rawStep.tool_name),
        toolCallId: readString(rawStep.tool_call_id),
        command: readString(rawStep.command),
        startedAt: readString(rawStep.started_at),
        endedAt: readString(rawStep.ended_at),
      } satisfies GenerationStep;
    })
    .filter((step): step is GenerationStep => step !== null)
    .sort((left, right) => {
      if (left.sequence !== null && right.sequence !== null && left.sequence !== right.sequence) {
        return left.sequence - right.sequence;
      }

      const timestampDifference =
        toTimestamp(left.startedAt ?? left.endedAt) - toTimestamp(right.startedAt ?? right.endedAt);

      if (timestampDifference !== 0) {
        return timestampDifference;
      }

      return left.id.localeCompare(right.id);
    });
}

function classifyStep(step: GenerationStep): TimelineItem["kind"] {
  if (step.toolName || step.command || step.toolCallId) {
    return "tool";
  }

  const normalizedKind = step.kind?.toLowerCase() ?? "";
  const normalizedPhase = step.phase?.toLowerCase() ?? "";

  if (
    step.deltaText ||
    normalizedKind.includes("output") ||
    normalizedKind.includes("delta") ||
    normalizedPhase.includes("output") ||
    normalizedPhase.includes("response")
  ) {
    return "output";
  }

  if (normalizedKind.includes("status") || normalizedPhase.includes("status")) {
    return "status";
  }

  return "reasoning";
}

function buildTimelineSummary(
  kind: TimelineItem["kind"],
  step: GenerationStep,
  assistantMessage: SessionMessage | null,
): string {
  if (step.safeSummary) {
    return step.safeSummary;
  }

  if (kind === "tool") {
    if (step.command) {
      return `执行命令：${step.command}`;
    }

    if (step.toolName) {
      return `调用工具：${step.toolName}`;
    }

    return "执行工具步骤。";
  }

  if (kind === "output") {
    const outputLength = step.deltaText?.length ?? assistantMessage?.content.trim().length ?? 0;
    return outputLength > 0
      ? `正在整理回复，当前可见输出约 ${outputLength} 字。`
      : "正在整理可见回复。";
  }

  if (kind === "status") {
    return step.state ? `运行状态更新：${step.state.replace(/[_.]+/g, " · ")}` : "运行状态已更新。";
  }

  if (step.phase) {
    return `已记录 ${formatPhaseLabel(step.phase) ?? step.phase} 阶段的可见摘要。`;
  }

  return "已记录当前步骤的可见摘要。";
}

function buildTimelineTitle(kind: TimelineItem["kind"], step: GenerationStep): string {
  if (step.label) {
    return step.label;
  }

  if (kind === "tool") {
    return step.toolName ? `工具 · ${step.toolName}` : "工具调用";
  }

  if (kind === "output") {
    return "回复输出";
  }

  if (kind === "status") {
    return "运行状态";
  }

  return formatPhaseLabel(step.phase) ?? "推理摘要";
}

function buildTimelineItems(
  generation: ChatGeneration,
  assistantMessage: SessionMessage | null,
): TimelineItem[] {
  const timelineItems: TimelineItem[] = [];

  for (const step of getGenerationSteps(generation)) {
    const kind = classifyStep(step);
    const summary = buildTimelineSummary(kind, step, assistantMessage);
    const title = buildTimelineTitle(kind, step);
    const status = step.status ?? generation.status;
    const charCount = step.deltaText?.length ?? 0;
    const previousItem = timelineItems[timelineItems.length - 1] ?? null;

    if (
      kind === "tool" &&
      previousItem?.kind === "tool" &&
      previousItem.toolCallId === step.toolCallId
    ) {
      previousItem.summary = summary;
      previousItem.status = status;
      previousItem.phase = formatPhaseLabel(step.phase) ?? previousItem.phase;
      continue;
    }

    if (kind === "output" && previousItem?.kind === "output") {
      previousItem.charCount += charCount;
      previousItem.status = status;
      previousItem.summary =
        previousItem.charCount > 0
          ? `正在整理回复，累计追加约 ${previousItem.charCount} 字。`
          : summary;
      continue;
    }

    timelineItems.push({
      id: step.id,
      kind,
      title,
      summary,
      status,
      phase: formatPhaseLabel(step.phase),
      toolCallId: step.toolCallId,
      charCount,
    });
  }

  if (timelineItems.length > 0) {
    return timelineItems;
  }

  if (generation.error_message) {
    return [
      {
        id: `${generation.id}:fallback:error`,
        kind: "status",
        title: "运行状态",
        summary: generation.error_message,
        status: generation.status,
        phase: null,
        toolCallId: null,
        charCount: 0,
      },
    ];
  }

  if (generation.reasoning_summary) {
    return [
      {
        id: `${generation.id}:fallback:summary`,
        kind: "reasoning",
        title: "推理摘要",
        summary: generation.reasoning_summary,
        status: generation.status,
        phase: null,
        toolCallId: null,
        charCount: 0,
      },
    ];
  }

  return [
    {
      id: `${generation.id}:fallback:status`,
      kind: "status",
      title: "运行状态",
      summary:
        generation.status === "queued"
          ? "当前生成已进入队列，等待前序回复完成。"
          : generation.status === "running"
            ? "当前生成正在进行中。"
            : generation.status === "completed"
              ? "当前生成已完成。"
              : generation.status === "cancelled"
                ? "当前生成已取消。"
                : "当前生成状态已更新。",
      status: generation.status,
      phase: null,
      toolCallId: null,
      charCount: 0,
    },
  ];
}

function getToneClasses(kind: TimelineItem["kind"]): string {
  switch (kind) {
    case "tool":
      return "border-[rgba(57,84,72,0.12)] bg-white/70";
    case "output":
      return "border-[rgba(36,88,69,0.14)] bg-[var(--accent-soft)]/60";
    case "status":
      return "border-[rgba(154,114,39,0.16)] bg-[var(--surface-subtle)]";
    default:
      return "border-[rgba(57,84,72,0.12)] bg-white/80";
  }
}

function getPromptPreview(message: SessionMessage | null): string {
  if (!message) {
    return "等待当前上下文完成后继续处理。";
  }

  const normalized = message.content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 72) {
    return normalized;
  }

  return `${normalized.slice(0, 71).trimEnd()}…`;
}

function GenerationQueueItem({
  generation,
  title,
  subtitle,
  prompt,
  cancelDisabled = false,
  onCancelGeneration,
}: {
  generation: ChatGeneration;
  title: string;
  subtitle: string;
  prompt: string;
  cancelDisabled?: boolean;
  onCancelGeneration?: (generationId: string) => void;
}) {
  return (
    <article className="grid gap-3 rounded-[18px] border border-[var(--panel-border)] bg-white/75 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm text-[var(--text-strong)]">{title}</strong>
            <span className="management-token-chip">
              {formatGenerationAction(generation.action)}
            </span>
          </div>
          <p className="m-0 text-xs text-[var(--text-secondary)]">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={generation.status} />
          {typeof onCancelGeneration === "function" ? (
            <button
              className="inline-button"
              type="button"
              disabled={cancelDisabled}
              onClick={() => onCancelGeneration(generation.id)}
            >
              取消
            </button>
          ) : null}
        </div>
      </div>
      <p className="m-0 text-sm leading-6 text-[var(--text-primary)]">{prompt}</p>
    </article>
  );
}

export function GenerationQueuePanel({
  activeGeneration,
  queuedGenerations,
  messages,
  cancelDisabled = false,
  onCancelGeneration,
}: GenerationQueuePanelProps) {
  const messagesById = useMemo(
    () => new Map(messages.map((message) => [message.id, message] as const)),
    [messages],
  );

  if (!activeGeneration && queuedGenerations.length === 0) {
    return null;
  }

  return (
    <section className="w-full max-w-[920px] rounded-[22px] border border-[rgba(57,84,72,0.14)] bg-[radial-gradient(circle_at_top_right,rgba(86,135,108,0.1),transparent_42%),rgba(250,248,242,0.9)] p-4 shadow-[0_18px_42px_rgba(41,43,38,0.06)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="grid gap-1">
          <span className="text-[0.76rem] font-extrabold uppercase tracking-[0.08em] text-[var(--text-muted)]">
            生成队列
          </span>
          <p className="m-0 text-sm leading-6 text-[var(--text-secondary)]">
            当前回复与排队中的生成会统一显示在这里，可按条取消。
          </p>
        </div>
        <span className="assistant-tool-group-count">
          {(activeGeneration ? 1 : 0) + queuedGenerations.length} 项
        </span>
      </div>

      <div className="grid gap-3">
        {activeGeneration ? (
          <GenerationQueueItem
            generation={activeGeneration}
            title="当前执行"
            subtitle="助手正在生成这条回复。"
            prompt={getPromptPreview(
              activeGeneration.user_message_id
                ? (messagesById.get(activeGeneration.user_message_id) ?? null)
                : null,
            )}
            cancelDisabled={cancelDisabled}
            onCancelGeneration={onCancelGeneration}
          />
        ) : null}

        {queuedGenerations.map((generation, index) => {
          const queuePosition = getGenerationQueuePosition(generation) ?? index + 1;

          return (
            <GenerationQueueItem
              key={generation.id}
              generation={generation}
              title={`排队 #${queuePosition}`}
              subtitle="等待当前运行结束后自动开始。"
              prompt={getPromptPreview(
                generation.user_message_id
                  ? (messagesById.get(generation.user_message_id) ?? null)
                  : null,
              )}
              cancelDisabled={cancelDisabled}
              onCancelGeneration={onCancelGeneration}
            />
          );
        })}
      </div>
    </section>
  );
}

export function GenerationRunTimeline({
  generation,
  assistantMessage,
}: GenerationRunTimelineProps) {
  const timelineItems = useMemo(
    () => buildTimelineItems(generation, assistantMessage),
    [assistantMessage, generation],
  );

  return (
    <section className="rounded-[22px] border border-[rgba(57,84,72,0.14)] bg-[radial-gradient(circle_at_top_right,rgba(86,135,108,0.1),transparent_40%),rgba(250,248,242,0.9)] p-4 shadow-[0_18px_42px_rgba(41,43,38,0.06)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="grid gap-1">
          <span className="text-[0.76rem] font-extrabold uppercase tracking-[0.08em] text-[var(--text-muted)]">
            运行时间线
          </span>
          <p className="m-0 text-sm leading-6 text-[var(--text-secondary)]">
            只展示可见的阶段摘要、工具标签与输出进度，不暴露隐藏推理内容。
          </p>
        </div>
        <span className="assistant-tool-group-count">{timelineItems.length} 项</span>
      </div>

      <ol className="m-0 grid list-none gap-3 p-0">
        {timelineItems.map((item) => (
          <li
            key={item.id}
            className={`grid gap-3 rounded-[18px] border p-4 ${getToneClasses(item.kind)}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="grid gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <strong className="text-sm text-[var(--text-strong)]">{item.title}</strong>
                  {item.phase ? <span className="management-token-chip">{item.phase}</span> : null}
                </div>
                <p className="m-0 text-sm leading-6 text-[var(--text-primary)]">{item.summary}</p>
              </div>
              <StatusBadge status={item.status} />
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
