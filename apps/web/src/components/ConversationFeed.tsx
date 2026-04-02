import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatBytes } from "../lib/format";
import type { RuntimeExecutionRun } from "../types/runtime";
import type {
  AssistantTranscriptSegment,
  ChatGeneration,
  SessionEventEntry,
  SessionMessage,
} from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type TranscriptToolBlock = {
  type: "tool";
  key: string;
  call: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  error: AssistantTranscriptSegment | null;
};

type TranscriptReasoningBlock = {
  type: "reasoning";
  key: string;
  segments: AssistantTranscriptSegment[];
};

type TranscriptOutputBlock = {
  type: "output";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptErrorBlock = {
  type: "error";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptRenderableBlock =
  | TranscriptToolBlock
  | TranscriptReasoningBlock
  | TranscriptOutputBlock
  | TranscriptErrorBlock;

type ConversationFeedProps = {
  messages: SessionMessage[];
  generations: ChatGeneration[];
  events: SessionEventEntry[];
  runtimeRuns: RuntimeExecutionRun[];
  activeGeneration?: ChatGeneration | null;
  queuedGenerations?: ChatGeneration[];
  activeBranchId?: string | null;
  messageActionBusyId?: string | null;
  cancelGenerationBusy?: boolean;
  onCancelGeneration?: (generationId: string) => void;
  onEditMessage?: (message: SessionMessage) => void;
  onRegenerateMessage?: (message: SessionMessage) => void;
  onForkMessage?: (message: SessionMessage) => void;
  onRollbackMessage?: (message: SessionMessage) => void;
};

type GenerationRun = {
  id: string;
  generation: ChatGeneration;
  assistantMessage: SessionMessage | null;
};

type ConversationTurn = {
  id: string;
  userMessage: SessionMessage;
  generationRuns: GenerationRun[];
  supplementalMessages: SessionMessage[];
};

function toTimestamp(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareMessages(left: SessionMessage, right: SessionMessage): number {
  const leftSequence = typeof left.sequence === "number" ? left.sequence : Number.MAX_SAFE_INTEGER;
  const rightSequence = typeof right.sequence === "number" ? right.sequence : Number.MAX_SAFE_INTEGER;

  if (leftSequence !== rightSequence) {
    return leftSequence - rightSequence;
  }

  const timestampDifference = toTimestamp(left.created_at) - toTimestamp(right.created_at);
  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function compareGenerations(left: ChatGeneration, right: ChatGeneration): number {
  const timestampDifference =
    toTimestamp(left.started_at ?? left.created_at) - toTimestamp(right.started_at ?? right.created_at);

  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function compareTranscriptSegments(
  left: AssistantTranscriptSegment,
  right: AssistantTranscriptSegment,
): number {
  if (left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }

  const timestampDifference = toTimestamp(left.recorded_at) - toTimestamp(right.recorded_at);
  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function renderUserMessage(content: string) {
  return <div className="chat-bubble-plain">{content}</div>;
}

function renderMarkdownMessage(content: string) {
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

function readGenerationAssistantMessageId(generation: ChatGeneration): string | null {
  return generation.assistant_message_id ?? null;
}

function readGenerationUserMessageId(generation: ChatGeneration): string | null {
  return generation.user_message_id ?? null;
}

function buildConversationRows(
  messages: SessionMessage[],
  generations: ChatGeneration[],
): {
  turns: ConversationTurn[];
  orphanMessages: SessionMessage[];
  orphanGenerationRuns: GenerationRun[];
} {
  const sortedMessages = [...messages].sort(compareMessages);
  const sortedGenerations = [...generations].sort(compareGenerations);
  const userMessages = sortedMessages.filter((message) => message.role === "user");
  const nonUserMessages = sortedMessages.filter((message) => message.role !== "user");
  const messageById = new Map(sortedMessages.map((message) => [message.id, message] as const));
  const matchedMessageIds = new Set<string>();
  const matchedGenerationIds = new Set<string>();

  if (userMessages.length === 0) {
    return {
      turns: [],
      orphanMessages: nonUserMessages,
      orphanGenerationRuns: sortedGenerations.map((generation) => ({
        id: generation.id,
        generation,
        assistantMessage:
          (readGenerationAssistantMessageId(generation)
            ? messageById.get(readGenerationAssistantMessageId(generation)!)
            : null) ?? null,
      })),
    };
  }

  const turns = userMessages.map((userMessage, index) => {
    const nextUserMessage = userMessages[index + 1] ?? null;
    const turnStart = toTimestamp(userMessage.created_at);
    const turnEnd = nextUserMessage ? toTimestamp(nextUserMessage.created_at) : Number.POSITIVE_INFINITY;
    const turnMessages = nonUserMessages.filter((message) => {
      const timestamp = toTimestamp(message.created_at);
      return timestamp >= turnStart && timestamp < turnEnd;
    });
    const turnMessageIds = new Set(turnMessages.map((message) => message.id));

    const generationRuns = sortedGenerations
      .filter((generation) => {
        if (matchedGenerationIds.has(generation.id)) {
          return false;
        }

        const generationUserMessageId = readGenerationUserMessageId(generation);
        if (generationUserMessageId && generationUserMessageId === userMessage.id) {
          return true;
        }

        const assistantMessageId = readGenerationAssistantMessageId(generation);
        if (assistantMessageId && turnMessageIds.has(assistantMessageId)) {
          return true;
        }

        const timestamp = toTimestamp(generation.started_at ?? generation.created_at);
        return !generationUserMessageId && timestamp >= turnStart && timestamp < turnEnd;
      })
      .map((generation) => {
        matchedGenerationIds.add(generation.id);
        const assistantMessage =
          (readGenerationAssistantMessageId(generation)
            ? messageById.get(readGenerationAssistantMessageId(generation)!)
            : turnMessages.find((message) => message.generation_id === generation.id)) ?? null;

        if (assistantMessage) {
          matchedMessageIds.add(assistantMessage.id);
        }

        return {
          id: generation.id,
          generation,
          assistantMessage,
        } satisfies GenerationRun;
      });

    const supplementalMessages = turnMessages.filter((message) => !matchedMessageIds.has(message.id));
    supplementalMessages.forEach((message) => {
      matchedMessageIds.add(message.id);
    });

    return {
      id: userMessage.id,
      userMessage,
      generationRuns,
      supplementalMessages,
    } satisfies ConversationTurn;
  });

  const orphanMessages = nonUserMessages.filter((message) => !matchedMessageIds.has(message.id));
  const orphanGenerationRuns = sortedGenerations
    .filter((generation) => !matchedGenerationIds.has(generation.id))
    .map((generation) => ({
      id: generation.id,
      generation,
      assistantMessage:
        (readGenerationAssistantMessageId(generation)
          ? messageById.get(readGenerationAssistantMessageId(generation)!)
          : null) ?? null,
    }));

  return { turns, orphanMessages, orphanGenerationRuns };
}

function getGenerationActionLabel(action: string): string {
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

function mergeGenerations(
  generations: ChatGeneration[],
  activeGeneration: ChatGeneration | null,
  queuedGenerations: ChatGeneration[],
): ChatGeneration[] {
  const mergedById = new Map<string, ChatGeneration>();

  generations.forEach((generation) => {
    mergedById.set(generation.id, generation);
  });

  if (activeGeneration) {
    mergedById.set(activeGeneration.id, activeGeneration);
  }

  queuedGenerations.forEach((generation) => {
    mergedById.set(generation.id, generation);
  });

  return [...mergedById.values()].sort(compareGenerations);
}

function formatMessageRole(role: string): string {
  switch (role) {
    case "user":
      return "你";
    case "assistant":
      return "助手";
    case "system":
      return "系统";
    case "tool":
      return "工具";
    default:
      return role;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readFirstString(
  value: Record<string, unknown> | undefined,
  keys: readonly string[],
): string | null {
  if (!value) {
    return null;
  }

  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }

  return null;
}

function readNestedRecord(
  value: Record<string, unknown> | undefined,
  key: string,
): Record<string, unknown> | undefined {
  const candidate = value?.[key];
  return isRecord(candidate) ? candidate : undefined;
}

function humanizeIdentifier(value: string): string {
  return value
    .split(/[/_\-\s.]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function truncateCommand(value: string, maxLength = 64): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1).trimEnd()}…` : value;
}

function readSegmentCommand(segment: AssistantTranscriptSegment): string | null {
  const metadata = segment.metadata;
  const argumentsRecord = readNestedRecord(metadata, "arguments");
  const shellLikeTool =
    segment.tool_name === "execute_kali_command" ||
    segment.tool_name === "bash" ||
    segment.tool_name === "sh" ||
    segment.tool_name === "zsh";
  return (
    readFirstString(metadata, ["command"]) ??
    readFirstString(argumentsRecord, ["command"]) ??
    (shellLikeTool && segment.kind === "tool_call" && segment.text?.trim()
      ? segment.text.trim()
      : null)
  );
}

function readSkillPayload(segment: AssistantTranscriptSegment): Record<string, unknown> | null {
  const metadata = segment.metadata;
  const result = readNestedRecord(metadata, "result");
  const skill = readNestedRecord(result, "skill");
  return skill ?? null;
}

function inferSkillTitle(segment: AssistantTranscriptSegment): string | null {
  const skill = readSkillPayload(segment);
  const metadata = segment.metadata;
  const argumentsRecord = readNestedRecord(metadata, "arguments");
  const rawName =
    readFirstString(skill ?? undefined, ["title", "name", "directory_name", "id"]) ??
    readFirstString(argumentsRecord, ["skill_name_or_id"]);
  return rawName ? humanizeIdentifier(rawName) : null;
}

function inferToolBlockTitle(
  call: AssistantTranscriptSegment | null,
  result: AssistantTranscriptSegment | null,
  error: AssistantTranscriptSegment | null,
): { eyebrow: string; title: string; summary: string | null } {
  const reference = result ?? error ?? call;
  const toolName = reference?.tool_name ?? "";
  const commandSource = call ?? reference;
  const command = commandSource ? readSegmentCommand(commandSource) : null;
  const skillTitle = reference ? inferSkillTitle(reference) : null;
  const skillPayload = reference ? readSkillPayload(reference) : null;
  const skillSummary =
    readFirstString(skillPayload ?? undefined, ["description"]) ??
    (result?.text?.trim() && !/^Read skill content/i.test(result.text) ? result.text.trim() : null);

  if (toolName === "execute_kali_command" || command) {
    return {
      eyebrow: "Shell",
      title: command ?? "Shell",
      summary:
        result?.status && result.status !== "running"
          ? `状态：${result.status}`
          : error?.text?.trim() ?? null,
    };
  }

  if (toolName === "read_skill_content") {
    return {
      eyebrow: "技能",
      title: skillTitle ?? "Skill",
      summary: skillSummary,
    };
  }

  if (toolName === "list_available_skills") {
    return {
      eyebrow: "技能",
      title: "技能目录",
      summary: result?.text?.trim() ?? null,
    };
  }

  return {
    eyebrow: "工具",
    title: skillTitle ?? (toolName ? humanizeIdentifier(toolName) : "Tool"),
    summary: result?.text?.trim() ?? error?.text?.trim() ?? null,
  };
}

function buildTranscriptBlocks(segments: AssistantTranscriptSegment[]): TranscriptRenderableBlock[] {
  const ordered = [...segments].sort(compareTranscriptSegments);
  const blocks: TranscriptRenderableBlock[] = [];

  for (let index = 0; index < ordered.length; index += 1) {
    const segment = ordered[index]!;

    if (segment.kind === "reasoning" || segment.kind === "status") {
      const reasoningSegments: AssistantTranscriptSegment[] = [];
      let cursor = index;
      while (
        cursor < ordered.length &&
        (ordered[cursor]!.kind === "reasoning" || ordered[cursor]!.kind === "status")
      ) {
        if (ordered[cursor]!.text?.trim()) {
          reasoningSegments.push(ordered[cursor]!);
        }
        cursor += 1;
      }
      if (reasoningSegments.length > 0) {
        blocks.push({
          type: "reasoning",
          key: reasoningSegments.map((item) => item.id).join(":"),
          segments: reasoningSegments,
        });
      }
      index = cursor - 1;
      continue;
    }

    if (segment.kind === "tool_call") {
      let result: AssistantTranscriptSegment | null = null;
      let error: AssistantTranscriptSegment | null = null;
      let cursor = index + 1;

      while (cursor < ordered.length) {
        const nextSegment = ordered[cursor]!;
        if (nextSegment.tool_call_id !== segment.tool_call_id) {
          break;
        }
        if (nextSegment.kind === "tool_result" && result === null) {
          result = nextSegment;
          cursor += 1;
          continue;
        }
        if (nextSegment.kind === "error" && error === null) {
          error = nextSegment;
          cursor += 1;
          continue;
        }
        break;
      }

      blocks.push({
        type: "tool",
        key: `tool:${segment.tool_call_id ?? segment.id}`,
        call: segment,
        result,
        error,
      });
      index = cursor - 1;
      continue;
    }

    if (segment.kind === "tool_result") {
      blocks.push({
        type: "tool",
        key: `tool-result:${segment.tool_call_id ?? segment.id}`,
        call: null,
        result: segment,
        error: null,
      });
      continue;
    }

    if (segment.kind === "error" && segment.tool_call_id) {
      blocks.push({
        type: "tool",
        key: `tool-error:${segment.tool_call_id}`,
        call: null,
        result: null,
        error: segment,
      });
      continue;
    }

    if (segment.kind === "output") {
      blocks.push({ type: "output", key: segment.id, segment });
      continue;
    }

    if (segment.kind === "error") {
      blocks.push({ type: "error", key: segment.id, segment });
    }
  }

  return blocks;
}

function buildGenerationStatusText(generation: ChatGeneration): string {
  if (generation.status === "queued") {
    return generation.queue_position && generation.queue_position > 1
      ? `已进入队列，前方还有 ${generation.queue_position - 1} 条等待。`
      : "已进入队列，等待开始。";
  }

  if (generation.status === "running") {
    return "正在持续更新当前回复。";
  }

  if (generation.status === "failed") {
    return generation.error_message?.trim() || "当前生成失败。";
  }

  if (generation.status === "cancelled") {
    return "当前生成已停止。";
  }

  return "当前生成已完成。";
}

function mapGenerationStepToTranscriptSegment(
  step: NonNullable<ChatGeneration["steps"]>[number],
): AssistantTranscriptSegment {
  const kind: AssistantTranscriptSegment["kind"] =
    step.kind === "tool"
      ? step.phase === "tool_result" ||
        step.status === "completed" ||
        step.status === "failed" ||
        step.status === "cancelled" ||
        step.ended_at !== null
        ? "tool_result"
        : "tool_call"
      : step.kind === "output"
        ? "output"
        : step.kind === "reasoning"
          ? "reasoning"
          : step.status === "failed" || step.phase === "failed"
            ? "error"
            : "status";

  const text =
    kind === "output"
      ? step.delta_text || step.safe_summary || null
      : step.safe_summary || step.delta_text || null;

  return {
    id: step.id,
    sequence: step.sequence,
    kind,
    status: step.status,
    title: step.label ?? null,
    text,
    tool_name: step.tool_name ?? null,
    tool_call_id: step.tool_call_id ?? null,
    recorded_at: step.started_at,
    updated_at: step.ended_at ?? step.started_at,
    metadata: step.metadata,
  };
}

function buildTranscriptFromGeneration(
  generation: ChatGeneration,
  assistantMessage: SessionMessage | null,
): AssistantTranscriptSegment[] {
  const segments = (generation.steps ?? [])
    .map((step) => mapGenerationStepToTranscriptSegment(step))
    .sort(compareTranscriptSegments);

  if (segments.length === 0 && generation.reasoning_summary?.trim()) {
    segments.push({
      id: `${generation.id}:reasoning-summary`,
      sequence: 1,
      kind: "reasoning",
      status: generation.status,
      title: "思路摘要",
      text: generation.reasoning_summary,
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    });
  }

  const assistantContent = assistantMessage?.content.trim() ?? "";
  if (
    assistantContent &&
    !segments.some(
      (segment) =>
        (segment.kind === "output" || segment.kind === "error") &&
        (segment.text ?? "").trim() === assistantContent,
    )
  ) {
    segments.push({
      id: `${generation.id}:assistant-output`,
      sequence: (segments[segments.length - 1]?.sequence ?? 0) + 1,
      kind: generation.status === "failed" ? "error" : "output",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "正文输出",
      text: assistantMessage?.content ?? "",
      recorded_at: assistantMessage?.created_at ?? generation.updated_at,
      updated_at: assistantMessage?.completed_at ?? generation.updated_at,
    });
  }

  if (
    generation.status !== "completed" &&
    !segments.some((segment) => segment.kind === "status" || segment.kind === "error")
  ) {
    segments.push({
      id: `${generation.id}:status`,
      sequence: (segments[segments.length - 1]?.sequence ?? 0) + 1,
      kind: generation.status === "failed" ? "error" : "status",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "运行状态",
      text: buildGenerationStatusText(generation),
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    });
  }

  if (segments.length > 0) {
    return segments.sort(compareTranscriptSegments);
  }

  return [
    {
      id: `${generation.id}:fallback-status`,
      sequence: 1,
      kind: generation.status === "failed" ? "error" : "status",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "运行状态",
      text: generation.error_message?.trim() || buildGenerationStatusText(generation),
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    },
  ];
}

function buildAssistantTranscript(
  message: SessionMessage,
  generation: ChatGeneration | null,
): AssistantTranscriptSegment[] {
  const transcript = [...message.assistant_transcript].sort(compareTranscriptSegments);
  const content = message.content.trim();

  if (
    content &&
    !transcript.some(
      (segment) =>
        (segment.kind === "output" || segment.kind === "error") &&
        (segment.text ?? "").trim() === content,
    )
  ) {
    transcript.push({
      id: `${message.id}:content`,
      sequence: (transcript[transcript.length - 1]?.sequence ?? 0) + 1,
      kind: message.status === "failed" ? "error" : "output",
      status: message.status ?? generation?.status ?? null,
      title: message.status === "failed" ? "执行异常" : "正文输出",
      text: message.content,
      recorded_at: message.created_at,
      updated_at: message.completed_at ?? message.created_at,
    });
  }

  if (transcript.length > 0) {
    return transcript.sort(compareTranscriptSegments);
  }

  if (generation) {
    return buildTranscriptFromGeneration(generation, message);
  }

  if (message.error_message?.trim()) {
    return [
      {
        id: `${message.id}:error`,
        sequence: 1,
        kind: "error",
        status: message.status ?? "failed",
        title: "执行异常",
        text: message.error_message,
        recorded_at: message.created_at,
        updated_at: message.completed_at ?? message.created_at,
      },
    ];
  }

  return [];
}

function readArtifactLabels(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((entry) => {
    if (typeof entry === "string" && entry.trim()) {
      return [entry];
    }

    if (typeof entry === "object" && entry !== null) {
      const candidate = entry as Record<string, unknown>;
      const label =
        (typeof candidate.relative_path === "string" && candidate.relative_path.trim()) ||
        (typeof candidate.name === "string" && candidate.name.trim()) ||
        (typeof candidate.path === "string" && candidate.path.trim()) ||
        null;

      return label ? [label] : [];
    }

    return [];
  });
}

function hasStructuredMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(value && Object.keys(value).length > 0);
}

function AssistantReasoningStream({
  segments,
}: {
  segments: AssistantTranscriptSegment[];
}) {
  const preview = segments
    .map((segment) => segment.text?.trim() ?? "")
    .filter(Boolean)
    .slice(0, 2)
    .join(" · ");

  return (
    <details className="assistant-reasoning-stream">
      <summary className="assistant-reasoning-toggle">
        <span className="assistant-reasoning-preview">{preview || "查看思路"}</span>
        <span className="assistant-reasoning-toggle-label">展开</span>
      </summary>
      <div className="assistant-reasoning-body">
        {segments.map((segment) => (
          <div key={segment.id} className="assistant-reasoning-line">
            {segment.text?.trim() ? renderMarkdownMessage(segment.text) : null}
          </div>
        ))}
      </div>
    </details>
  );
}

function AssistantShellBlock({
  call,
  result,
  error,
}: {
  call: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  error: AssistantTranscriptSegment | null;
}) {
  const reference = result ?? error ?? call;
  if (!reference) {
    return null;
  }

  const metadata = reference.metadata;
  const command = readSegmentCommand(reference) ?? "Shell";
  const stdout = readFirstString(metadata, ["stdout"]);
  const stderr = readFirstString(metadata, ["stderr"]);
  const exitCode = metadata?.exit_code ?? readNestedRecord(metadata, "result")?.exit_code ?? null;
  const artifacts = readArtifactLabels(metadata?.artifacts);
  const resultPayload = isRecord(metadata?.result) ? metadata.result : null;
  const status = error ? "failed" : (result?.status ?? call?.status ?? null);

  return (
    <details className="assistant-tool-block assistant-shell-block">
      <summary className="assistant-tool-summary">
        <div className="assistant-tool-summary-copy">
          <span className="assistant-tool-eyebrow">Shell</span>
          <strong className="assistant-tool-title" title={command}>
            {truncateCommand(command, 72)}
          </strong>
        </div>
        <div className="assistant-tool-summary-side">
          {status ? <StatusBadge status={status} /> : null}
        </div>
      </summary>
      <div className="assistant-tool-body">
        <div className="assistant-tool-detail-group">
          <span className="assistant-tool-detail-label">command</span>
          <pre className="assistant-terminal-output">{command}</pre>
        </div>
        {stdout !== null ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">stdout</span>
            <pre className="assistant-terminal-output">{stdout || "(empty)"}</pre>
          </div>
        ) : null}
        {stderr !== null ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">stderr</span>
            <pre className="assistant-terminal-output assistant-terminal-output-error">
              {stderr || "(empty)"}
            </pre>
          </div>
        ) : null}
        {exitCode !== null ? (
          <p className="assistant-tool-inline-meta">exit_code: {String(exitCode)}</p>
        ) : null}
        {resultPayload ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">result</span>
            <pre className="assistant-terminal-output">{JSON.stringify(resultPayload, null, 2)}</pre>
          </div>
        ) : null}
        {artifacts.length > 0 ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">artifacts</span>
            <div className="chat-bubble-artifacts assistant-transcript-artifacts">
              {artifacts.map((artifact) => (
                <span key={`${reference.id}:${artifact}`} className="chat-artifact-chip">
                  {artifact}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {error?.text?.trim() ? <p className="assistant-tool-error-copy">{error.text.trim()}</p> : null}
      </div>
    </details>
  );
}

function AssistantSkillBlock({
  call,
  result,
  error,
}: {
  call: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  error: AssistantTranscriptSegment | null;
}) {
  const reference = result ?? error ?? call;
  if (!reference) {
    return null;
  }

  const { eyebrow, title, summary } = inferToolBlockTitle(call, result, error);
  const skillPayload = readSkillPayload(reference);
  const skillContent = readFirstString(skillPayload ?? undefined, ["content"]);
  const skillDescription = readFirstString(skillPayload ?? undefined, ["description"]);
  const metadata = reference.metadata;

  return (
    <details className="assistant-tool-block assistant-skill-block">
      <summary className="assistant-tool-summary">
        <div className="assistant-tool-summary-copy">
          <span className="assistant-tool-eyebrow">{eyebrow}</span>
          <strong className="assistant-tool-title">{title}</strong>
          {summary ? <span className="assistant-tool-summary-text">{summary}</span> : null}
        </div>
        <div className="assistant-tool-summary-side">
          {reference.status ? <StatusBadge status={reference.status} /> : null}
        </div>
      </summary>
      <div className="assistant-tool-body">
        {skillDescription ? <p className="assistant-tool-inline-copy">{skillDescription}</p> : null}
        {skillContent ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">skill</span>
            <pre className="assistant-terminal-output">{skillContent}</pre>
          </div>
        ) : null}
        {!skillContent && hasStructuredMetadata(metadata) ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">details</span>
            <pre className="assistant-terminal-output">{JSON.stringify(metadata, null, 2)}</pre>
          </div>
        ) : null}
        {error?.text?.trim() ? <p className="assistant-tool-error-copy">{error.text.trim()}</p> : null}
      </div>
    </details>
  );
}

function AssistantErrorBlock({ segment }: { segment: AssistantTranscriptSegment }) {
  return (
    <details className="assistant-error-block" open>
      <summary className="assistant-error-summary">
        <strong className="assistant-error-title">执行失败</strong>
        {segment.status ? <StatusBadge status={segment.status} /> : null}
      </summary>
      {segment.text?.trim() ? <p className="assistant-error-copy">{segment.text.trim()}</p> : null}
      {hasStructuredMetadata(segment.metadata) ? (
        <pre className="assistant-terminal-output assistant-terminal-output-error">
          {JSON.stringify(segment.metadata, null, 2)}
        </pre>
      ) : null}
    </details>
  );
}

function AssistantPrimaryOutput({
  segment,
  isFinal,
}: {
  segment: AssistantTranscriptSegment;
  isFinal: boolean;
}) {
  return (
    <div className={`assistant-output-block${isFinal ? " assistant-output-block-final" : ""}`}>
      {segment.text?.trim() ? renderMarkdownMessage(segment.text) : renderMarkdownMessage("")}
    </div>
  );
}

function AssistantToolInvocationBlock({
  call,
  result,
  error,
}: {
  call: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  error: AssistantTranscriptSegment | null;
}) {
  const reference = result ?? error ?? call;
  if (!reference) {
    return null;
  }

  const command = call ? readSegmentCommand(call) : readSegmentCommand(reference);
  if (reference.tool_name === "execute_kali_command" || command) {
    return <AssistantShellBlock call={call} result={result} error={error} />;
  }

  return <AssistantSkillBlock call={call} result={result} error={error} />;
}

export function ConversationFeed(props: ConversationFeedProps) {
  const {
    messages,
    generations,
    activeGeneration = null,
    queuedGenerations = [],
    activeBranchId,
    messageActionBusyId,
    cancelGenerationBusy = false,
    onCancelGeneration,
    onEditMessage,
    onRegenerateMessage,
    onForkMessage,
    onRollbackMessage,
  } = props;
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);
  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);

  const mergedGenerations = useMemo(
    () => mergeGenerations(generations, activeGeneration, queuedGenerations),
    [activeGeneration, generations, queuedGenerations],
  );

  const { turns, orphanMessages, orphanGenerationRuns } = useMemo(
    () => buildConversationRows(messages, mergedGenerations),
    [messages, mergedGenerations],
  );

  const lastItemSignature = useMemo(() => {
    const lastMessage = messages[messages.length - 1] ?? null;
    const lastGeneration = mergedGenerations[mergedGenerations.length - 1] ?? null;

    return [
      messages.length,
      lastMessage?.id ?? "none",
      lastMessage?.content.length ?? 0,
      mergedGenerations.length,
      lastGeneration?.id ?? "none",
      lastGeneration?.status ?? "none",
      lastGeneration?.updated_at ?? "none",
      queuedGenerations.length,
    ].join(":");
  }, [mergedGenerations, messages, queuedGenerations.length]);

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

  function renderTranscriptSegments(segments: AssistantTranscriptSegment[]) {
    if (segments.length === 0) {
      return renderMarkdownMessage("");
    }

    const blocks = buildTranscriptBlocks(segments);
    const lastOutputBlockIndex = (() => {
      for (let index = blocks.length - 1; index >= 0; index -= 1) {
        if (blocks[index]?.type === "output") {
          return index;
        }
      }
      return -1;
    })();

    return (
      <div className="assistant-transcript">
        {blocks.map((block, index) => {
          if (block.type === "reasoning") {
            return <AssistantReasoningStream key={block.key} segments={block.segments} />;
          }

          if (block.type === "tool") {
            return (
              <AssistantToolInvocationBlock
                key={block.key}
                call={block.call}
                result={block.result}
                error={block.error}
              />
            );
          }

          if (block.type === "error") {
            return <AssistantErrorBlock key={block.key} segment={block.segment} />;
          }

          return (
            <AssistantPrimaryOutput
              key={block.key}
              segment={block.segment}
              isFinal={index === lastOutputBlockIndex}
            />
          );
        })}
      </div>
    );
  }

  function renderOverflowActions(message: SessionMessage) {
    const isBusy = messageActionBusyId === message.id;
    const secondaryActions: Array<{ label: string; onSelect: () => void }> = [];

    if (message.role === "user" && typeof onEditMessage === "function") {
      secondaryActions.push({
        label: "编辑",
        onSelect: () => onEditMessage(message),
      });
    }

    if (message.role === "assistant" && typeof onRegenerateMessage === "function") {
      secondaryActions.push({
        label: "重试",
        onSelect: () => onRegenerateMessage(message),
      });
    }

    if (typeof onForkMessage === "function") {
      secondaryActions.push({
        label: "分叉",
        onSelect: () => onForkMessage(message),
      });
    }

    const canRollback = typeof onRollbackMessage === "function";
    const hasVisibleActions = canRollback || secondaryActions.length > 0;

    if (!hasVisibleActions) {
      return null;
    }

    const isMenuOpen = openActionMenuId === message.id;

    return (
      <div className="chat-bubble-action-shell">
        {canRollback ? (
          <button
            className="chat-bubble-rollback-button"
            type="button"
            disabled={isBusy}
            onClick={() => onRollbackMessage?.(message)}
          >
            回溯到此
          </button>
        ) : null}
        {secondaryActions.length > 0 ? (
          <div className="chat-bubble-overflow-shell">
            <button
              className="chat-bubble-overflow-button"
              type="button"
              aria-label="打开消息操作"
              aria-expanded={isMenuOpen}
              onClick={() =>
                setOpenActionMenuId((currentValue) =>
                  currentValue === message.id ? null : message.id,
                )
              }
            >
              ···
            </button>
            {isMenuOpen ? (
              <div className="chat-bubble-overflow-menu">
                {secondaryActions.map((action) => (
                  <button
                    key={action.label}
                    type="button"
                    disabled={isBusy}
                    onClick={() => {
                      setOpenActionMenuId(null);
                      action.onSelect();
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  function renderAssistantBubble(message: SessionMessage, generation: ChatGeneration | null = null) {
    const transcript = buildAssistantTranscript(message, generation);
    const generationStatus = generation?.status ?? message.status ?? null;
    const canCancelGeneration =
      generation !== null &&
      typeof onCancelGeneration === "function" &&
      (generation.status === "queued" || generation.status === "running");

    return (
      <article key={message.id} className="chat-bubble chat-bubble-assistant chat-bubble-assistant-transcript">
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {generation && generation.action !== "reply" ? (
              <span className="management-token-chip">{getGenerationActionLabel(generation.action)}</span>
            ) : null}
            {generation?.status === "queued" && generation.queue_position ? (
              <span className="management-token-chip">排队 #{generation.queue_position}</span>
            ) : null}
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {generationStatus && generationStatus !== "completed" ? (
              <StatusBadge status={generationStatus} />
            ) : null}
            {canCancelGeneration ? (
              <button
                className="chat-bubble-cancel-button"
                type="button"
                disabled={cancelGenerationBusy}
                onClick={() => onCancelGeneration?.(generation.id)}
              >
                取消
              </button>
            ) : null}
          </div>
        </div>
        {renderTranscriptSegments(transcript)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {renderOverflowActions(message)}
      </article>
    );
  }

  function renderAssistantPlaceholder(generation: ChatGeneration) {
    const transcript = buildTranscriptFromGeneration(generation, null);
    const canCancelGeneration =
      typeof onCancelGeneration === "function" &&
      (generation.status === "queued" || generation.status === "running");

    return (
      <article
        key={generation.id}
        className="chat-bubble chat-bubble-assistant chat-bubble-assistant-transcript"
      >
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">助手</strong>
          <div className="chat-bubble-meta-actions">
            {generation.action !== "reply" ? (
              <span className="management-token-chip">{getGenerationActionLabel(generation.action)}</span>
            ) : null}
            {generation.status === "queued" && generation.queue_position ? (
              <span className="management-token-chip">排队 #{generation.queue_position}</span>
            ) : null}
            <StatusBadge status={generation.status} />
            {canCancelGeneration ? (
              <button
                className="chat-bubble-cancel-button"
                type="button"
                disabled={cancelGenerationBusy}
                onClick={() => onCancelGeneration?.(generation.id)}
              >
                取消
              </button>
            ) : null}
          </div>
        </div>
        {renderTranscriptSegments(transcript)}
      </article>
    );
  }

  function renderMessageBubble(message: SessionMessage) {
    if (message.role === "assistant" || message.role === "system") {
      return renderAssistantBubble(message, null);
    }

    return (
      <article key={message.id} className={`chat-bubble chat-bubble-${message.role}`}>
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {message.status && message.status !== "completed" ? (
              <StatusBadge status={message.status} />
            ) : null}
          </div>
        </div>
        {renderUserMessage(message.content)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {renderOverflowActions(message)}
      </article>
    );
  }

  function renderGenerationRun(run: GenerationRun) {
    return run.assistantMessage
      ? renderAssistantBubble(run.assistantMessage, run.generation)
      : renderAssistantPlaceholder(run.generation);
  }

  const hasContent =
    turns.length > 0 ||
    orphanMessages.length > 0 ||
    orphanGenerationRuns.length > 0 ||
    activeGeneration !== null ||
    queuedGenerations.length > 0;

  if (!hasContent) {
    return (
      <section ref={feedRef} className="conversation-feed conversation-feed-empty">
        <div className="conversation-feed-empty-card">
          <p className="conversation-feed-empty-title">从这里开始新的对话</p>
          <p className="conversation-feed-empty-copy">
            发送第一条提示后，这里会按对话顺序展示消息，并把助手回复整理成连续转录。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section ref={feedRef} className="conversation-feed conversation-feed-threaded">
      {orphanGenerationRuns.map((run) => renderGenerationRun(run))}
      {orphanMessages.map((message) => renderMessageBubble(message))}

      {turns.map((turn) => (
        <article key={turn.id} className="chat-turn">
          {renderMessageBubble(turn.userMessage)}
          {turn.generationRuns.map((run) => renderGenerationRun(run))}
          {turn.supplementalMessages.map((message) => renderMessageBubble(message))}
        </article>
      ))}
    </section>
  );
}
