import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
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

type TranscriptOutputBlock = {
  type: "output";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptReasoningBlock = {
  type: "reasoning";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptErrorBlock = {
  type: "error";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptCueBlock = {
  type: "cue";
  key: string;
  segment: AssistantTranscriptSegment;
};

type TranscriptRenderableBlock =
  | TranscriptToolBlock
  | TranscriptReasoningBlock
  | TranscriptOutputBlock
  | TranscriptErrorBlock
  | TranscriptCueBlock;

type PresentShellTextValue = {
  present: true;
  text: string;
};

type TranscriptToolPair = {
  anchorId: string;
  call: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  error: AssistantTranscriptSegment | null;
};

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
  onEditMessage?: (message: SessionMessage, content: string) => Promise<void> | void;
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

type ConversationEventNoteEntry = {
  id: string;
  summary: string;
};

function isVisibleCompactionEvent(event: SessionEventEntry): boolean {
  const payload = event.payload as Record<string, unknown> | null;
  const rawMode = payload && typeof payload["mode"] === "string" ? payload["mode"] : undefined;
  const eventMode = typeof rawMode === "string" ? rawMode.toLowerCase() : undefined;
  return (
    ((event.type === "session.compaction.completed" && eventMode !== "automatic") ||
      event.type === "session.compaction.failed") &&
    event.summary.trim().length > 0
  );
}

function buildVisibleCompactionNotes(events: SessionEventEntry[]): ConversationEventNoteEntry[] {
  return events
    .filter(isVisibleCompactionEvent)
    .map((event) => ({ id: event.id, summary: event.summary.trim() }));
}

function ConversationEventNote({ summary }: { summary: string }) {
  return <p className="conversation-event-note">{summary}</p>;
}

function toTimestamp(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareMessages(left: SessionMessage, right: SessionMessage): number {
  const leftSequence = typeof left.sequence === "number" ? left.sequence : Number.MAX_SAFE_INTEGER;
  const rightSequence =
    typeof right.sequence === "number" ? right.sequence : Number.MAX_SAFE_INTEGER;

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
    toTimestamp(left.started_at ?? left.created_at) -
    toTimestamp(right.started_at ?? right.created_at);

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

function normalizeForBoilerplateCheck(content: string | null | undefined): string | null {
  if (!content) {
    return null;
  }

  const normalized = content
    .replace(/<\/?think\b[^>]*>/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  return normalized.length > 0 ? normalized : null;
}

function normalizeMarkdownSpacing(content: string | null | undefined): string | null {
  if (content === null || content === undefined) {
    return null;
  }

  return content.length > 0 ? content : null;
}

function normalizeAssistantPrimaryText(content: string | null | undefined): string | null {
  return normalizeMarkdownSpacing(content) ?? normalizeForBoilerplateCheck(content);
}

function hasAuthoritativeAssistantPrimarySegment(
  segments: AssistantTranscriptSegment[],
  assistantContent: string | null | undefined,
): boolean {
  const normalizedAssistantContent = normalizeAssistantPrimaryText(assistantContent);
  if (!normalizedAssistantContent) {
    return false;
  }

  return segments.some((segment) => {
    if (segment.kind !== "output" && segment.kind !== "error") {
      return false;
    }

    const normalizedSegmentText = normalizeAssistantPrimaryText(segment.text);
    return (
      normalizedSegmentText !== null &&
      (normalizedSegmentText === normalizedAssistantContent ||
        normalizedSegmentText.endsWith(normalizedAssistantContent))
    );
  });
}

function findEquivalentToolSegmentIndex(
  segments: AssistantTranscriptSegment[],
  candidate: AssistantTranscriptSegment,
): number {
  if (
    !candidate.tool_call_id ||
    (candidate.kind !== "tool_call" &&
      candidate.kind !== "tool_result" &&
      candidate.kind !== "error")
  ) {
    return -1;
  }

  return segments.findIndex(
    (segment) => segment.kind === candidate.kind && segment.tool_call_id === candidate.tool_call_id,
  );
}

function mergeMissingGenerationToolSegments(
  transcript: AssistantTranscriptSegment[],
  generation: ChatGeneration | null,
): AssistantTranscriptSegment[] {
  if (!generation?.steps?.length) {
    return transcript;
  }

  const merged = [...transcript];
  let nextSequence = merged[merged.length - 1]?.sequence ?? 0;
  const generationSegments = generation.steps
    .map((step) => mapGenerationStepToTranscriptSegment(step))
    .filter(
      (segment) =>
        segment.kind === "tool_call" ||
        segment.kind === "tool_result" ||
        (segment.kind === "error" && segment.tool_call_id),
    )
    .sort(compareTranscriptSegments);

  for (const segment of generationSegments) {
    const existingIndex = findEquivalentToolSegmentIndex(merged, segment);
    if (existingIndex >= 0) {
      const existingSegment = merged[existingIndex];
      if (existingSegment) {
        merged[existingIndex] =
          mergeOrPickToolSegment(existingSegment, segment, { preserveStableFields: true }) ??
          existingSegment;
      }
      continue;
    }

    nextSequence += 1;
    merged.push({
      ...segment,
      sequence: nextSequence,
    });
  }

  return merged;
}

const markdownComponents = {
  think: ({ children }: { children?: ReactNode }) => (
    <div className="assistant-inline-think">{children}</div>
  ),
} as Components;

function readSegmentState(segment: AssistantTranscriptSegment): string | null {
  const state = readSegmentMetadata(segment)?.state;
  return typeof state === "string" && state.trim().length > 0 ? state : null;
}

function isTranscriptLifecycleNoise(segment: AssistantTranscriptSegment): boolean {
  const state = readSegmentState(segment);
  return (
    state === "generation.started" ||
    state === "generation.completed" ||
    state === "generation.cancelled" ||
    state === "skill.autoroute.started"
  );
}

function shouldRenderReasoningSegment(segment: AssistantTranscriptSegment): boolean {
  const normalized = normalizeForBoilerplateCheck(segment.text);
  return normalized !== null;
}

function shouldRenderStatusSegment(segment: AssistantTranscriptSegment): boolean {
  if (segment.kind !== "status") {
    return false;
  }
  const normalized = normalizeForBoilerplateCheck(segment.text);
  return normalized !== null && !isTranscriptLifecycleNoise(segment);
}

function renderAssistantMarkdownMessage(content: string) {
  const normalized = normalizeMarkdownSpacing(content);
  return renderMarkdownMessage(normalized ?? "");
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
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
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
    const turnEnd = nextUserMessage
      ? toTimestamp(nextUserMessage.created_at)
      : Number.POSITIVE_INFINITY;
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

    const supplementalMessages = turnMessages.filter(
      (message) => !matchedMessageIds.has(message.id),
    );
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

function hasOwnKey(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function readSegmentMetadata(
  segment: AssistantTranscriptSegment | null,
): Record<string, unknown> | undefined {
  if (!segment) {
    return undefined;
  }

  if (isRecord(segment.metadata)) {
    return segment.metadata;
  }

  const legacySegment = segment as unknown as Record<string, unknown>;
  return isRecord(legacySegment.metadata_payload) ? legacySegment.metadata_payload : undefined;
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

function readPathValue(
  value: Record<string, unknown> | undefined,
  path: readonly string[],
): { found: boolean; value: unknown } {
  if (!value || path.length === 0) {
    return { found: false, value: undefined };
  }

  let current: unknown = value;
  for (const key of path) {
    if (!isRecord(current) || !hasOwnKey(current, key)) {
      return { found: false, value: undefined };
    }
    current = current[key];
  }

  return { found: true, value: current };
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

const SHELL_CANDIDATE_RECORD_PATHS = [
  ["result"],
  ["output"],
  ["result", "output"],
  ["execution"],
  ["result", "execution"],
  ["payload"],
  ["result", "payload"],
  ["data"],
  ["result", "data"],
  ["arguments"],
  ["result", "arguments"],
] as const;

const SHELL_FALLBACK_FIELD_NAMES = ["text", "safe_summary", "summary", "message", "error"] as const;

const SHELL_ARTIFACT_FIELD_NAMES = ["artifacts", "artifact_paths"] as const;

function collectShellCandidateRecords(
  segment: AssistantTranscriptSegment | null,
): Record<string, unknown>[] {
  const metadata = readSegmentMetadata(segment);
  if (!metadata) {
    return [];
  }

  const records: Record<string, unknown>[] = [];
  const seenRecords = new Set<Record<string, unknown>>();

  for (const path of SHELL_CANDIDATE_RECORD_PATHS) {
    const candidate = readPathValue(metadata, path);
    if (!candidate.found || !isRecord(candidate.value) || seenRecords.has(candidate.value)) {
      continue;
    }

    seenRecords.add(candidate.value);
    records.push(candidate.value);
  }

  if (!seenRecords.has(metadata)) {
    seenRecords.add(metadata);
    records.push(metadata);
  }

  return records;
}

function coerceShellDisplayValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }

  if (value === null || value === undefined) {
    return "";
  }

  if (Array.isArray(value) || isRecord(value)) {
    return JSON.stringify(value, null, 2) ?? "";
  }

  return String(value);
}

function readShellDisplayField(
  segment: AssistantTranscriptSegment | null,
  fieldNames: readonly string[],
): PresentShellTextValue | null {
  let firstPresentValue: PresentShellTextValue | null = null;

  for (const record of collectShellCandidateRecords(segment)) {
    for (const fieldName of fieldNames) {
      if (!hasOwnKey(record, fieldName)) {
        continue;
      }

      const candidateValue = {
        present: true,
        text: coerceShellDisplayValue(record[fieldName]),
      } satisfies PresentShellTextValue;

      if (firstPresentValue === null) {
        firstPresentValue = candidateValue;
      }

      if (candidateValue.text.trim().length > 0) {
        return candidateValue;
      }
    }
  }

  return firstPresentValue;
}

function readPrioritizedShellDisplayField(
  segments: readonly (AssistantTranscriptSegment | null)[],
  fieldNames: readonly string[],
): PresentShellTextValue | null {
  for (const segment of segments) {
    const value = readShellDisplayField(segment, fieldNames);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function isGenericShellSummary(candidate: string): boolean {
  const normalized = candidate.replace(/\s+/g, " ").trim().toLowerCase();
  if (normalized.length === 0) {
    return false;
  }

  return (
    /^命令已完成[，,]?状态[:：]?\s*[\w-]+[。.]?$/.test(normalized) ||
    /^工具执行完成[，,]?状态[:：]?\s*[\w-]+[。.]?$/.test(normalized) ||
    /^命令执行(?:完成|结束)[。.]?$/.test(normalized) ||
    /^tool(?: execution)? completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/.test(normalized) ||
    /^command completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/.test(normalized) ||
    /^completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/.test(normalized)
  );
}

function isUsableShellFallbackText(
  candidate: string,
  command: string,
  excludedText: string | null,
): boolean {
  const normalizedCandidate = candidate.trim();
  if (normalizedCandidate.length === 0) {
    return true;
  }

  const normalizedCommand = command.trim();
  const normalizedExcludedText = excludedText?.trim() ?? null;

  return !(
    normalizedCandidate === normalizedCommand ||
    (normalizedExcludedText !== null && normalizedCandidate === normalizedExcludedText) ||
    isGenericShellSummary(normalizedCandidate)
  );
}

function readSegmentCommand(segment: AssistantTranscriptSegment): string | null {
  const shellLikeTool =
    segment.tool_name === "execute_kali_command" ||
    segment.tool_name === "bash" ||
    segment.tool_name === "sh" ||
    segment.tool_name === "zsh";
  const command = readShellDisplayField(segment, ["command"])?.text.trim() ?? null;

  return (
    (command && command.length > 0 ? command : null) ??
    (shellLikeTool && segment.kind === "tool_call" && segment.text?.trim()
      ? segment.text.trim()
      : null)
  );
}

function readSkillPayload(segment: AssistantTranscriptSegment): Record<string, unknown> | null {
  const metadata = readSegmentMetadata(segment);
  const result = readNestedRecord(metadata, "result");
  const skill = readNestedRecord(result, "skill");
  return skill ?? null;
}

function inferSkillTitle(segment: AssistantTranscriptSegment): string | null {
  const skill = readSkillPayload(segment);
  const metadata = readSegmentMetadata(segment);
  const argumentsRecord = readNestedRecord(metadata, "arguments");
  const rawName =
    readFirstString(skill ?? undefined, ["title", "name", "directory_name", "id"]) ??
    readFirstString(argumentsRecord, ["skill_name_or_id"]);
  return rawName ? humanizeIdentifier(rawName) : null;
}

function readShellFallbackOutput(
  segment: AssistantTranscriptSegment | null,
  command: string,
  excludedText: string | null = null,
): PresentShellTextValue | null {
  if (!segment) {
    return null;
  }

  for (const record of collectShellCandidateRecords(segment)) {
    for (const fieldName of SHELL_FALLBACK_FIELD_NAMES) {
      if (!hasOwnKey(record, fieldName)) {
        continue;
      }

      const text = coerceShellDisplayValue(record[fieldName]);
      if (!isUsableShellFallbackText(text, command, excludedText)) {
        continue;
      }

      return {
        present: true,
        text,
      };
    }
  }

  const candidate = segment.text;
  if (typeof candidate !== "string" || candidate.trim().length === 0) {
    return null;
  }

  if (!isUsableShellFallbackText(candidate, command, excludedText)) {
    return null;
  }

  return { present: true, text: candidate };
}

function isShellFailureStatus(status: string | null | undefined): boolean {
  if (!status) {
    return false;
  }

  const normalized = status.trim().toLowerCase();
  if (normalized.length === 0) {
    return false;
  }

  return [
    "failed",
    "error",
    "cancelled",
    "canceled",
    "timed_out",
    "timeout",
    "denied",
    "killed",
  ].includes(normalized);
}

function hasVisibleShellText(value: PresentShellTextValue | null): boolean {
  return typeof value?.text === "string" && value.text.trim().length > 0;
}

function readShellErrorText({
  error,
  result,
  call,
  status,
}: {
  error: AssistantTranscriptSegment | null;
  result: AssistantTranscriptSegment | null;
  call: AssistantTranscriptSegment | null;
  status: string | null;
}): string | null {
  const explicitError =
    readPrioritizedShellDisplayField([error, result, call], ["error", "detail"])?.text.trim() ??
    error?.text?.trim() ??
    null;
  if (explicitError) {
    return explicitError;
  }

  if (!isShellFailureStatus(status)) {
    return null;
  }

  return readPrioritizedShellDisplayField([error, result, call], ["message"])?.text.trim() ?? null;
}

function readShellArtifacts(segment: AssistantTranscriptSegment | null): string[] {
  const labels: string[] = [];
  const seen = new Set<string>();

  for (const record of collectShellCandidateRecords(segment)) {
    for (const fieldName of SHELL_ARTIFACT_FIELD_NAMES) {
      if (!hasOwnKey(record, fieldName)) {
        continue;
      }

      for (const label of readArtifactLabels(record[fieldName])) {
        if (seen.has(label)) {
          continue;
        }

        seen.add(label);
        labels.push(label);
      }
    }
  }

  return labels;
}

type ToolSegmentRichness = {
  score: number;
  hasRenderable: boolean;
};

function scoreStandaloneToolText(text: string | null | undefined, command: string | null): number {
  if (!text) {
    return 0;
  }

  const normalized = text.trim();
  if (normalized.length === 0) {
    return 0;
  }

  if (command && normalized === command.trim()) {
    return 0;
  }

  if (isGenericShellSummary(normalized)) {
    return 1;
  }

  return normalized.includes("\n") ? 4 : 3;
}

function readToolSegmentRichness(segment: AssistantTranscriptSegment | null): ToolSegmentRichness {
  if (!segment) {
    return { score: 0, hasRenderable: false };
  }

  const command = readSegmentCommand(segment);
  const stdout = readShellDisplayField(segment, ["stdout"]);
  const stderr = readShellDisplayField(segment, ["stderr"]);
  const exitCode = readShellDisplayField(segment, ["exit_code"]);
  const errorText = readShellErrorText({
    error: segment.kind === "error" ? segment : null,
    result: segment.kind === "tool_result" ? segment : null,
    call: segment.kind === "tool_call" ? segment : null,
    status: segment.status ?? null,
  });
  const fallbackOutput =
    stdout === null && stderr === null
      ? readShellFallbackOutput(segment, command ?? "Shell", errorText)
      : null;
  const hasStdout = hasVisibleShellText(stdout);
  const hasStderr = hasVisibleShellText(stderr);
  const hasFallbackOutput = hasVisibleShellText(fallbackOutput);
  const hasErrorText = typeof errorText === "string" && errorText.trim().length > 0;
  const hasExitCode = exitCode !== null;
  const artifactCount = readShellArtifacts(segment).length;
  const textScore = scoreStandaloneToolText(segment.text, command);
  const fallbackScore =
    fallbackOutput && fallbackOutput.text.trim().length > 0
      ? isGenericShellSummary(fallbackOutput.text)
        ? 1
        : 8
      : 0;
  const score =
    (command ? 1 : 0) +
    (hasStdout ? 16 : 0) +
    (hasStderr ? 12 : 0) +
    fallbackScore +
    (hasErrorText ? 12 : 0) +
    (hasExitCode ? 4 : 0) +
    (artifactCount > 0 ? 2 : 0) +
    textScore;

  return {
    score,
    hasRenderable: hasStdout || hasStderr || hasFallbackOutput || hasErrorText,
  };
}

function preferPopulatedString(
  preferred: string | null | undefined,
  fallback: string | null | undefined,
): string | null | undefined {
  if (typeof preferred === "string" && preferred.trim().length > 0) {
    return preferred;
  }

  if (typeof fallback === "string" && fallback.trim().length > 0) {
    return fallback;
  }

  return preferred ?? fallback;
}

function mergePreferredValue(preferred: unknown, fallback: unknown): unknown {
  if (isRecord(preferred) && isRecord(fallback)) {
    const merged: Record<string, unknown> = {};
    const keys = new Set([...Object.keys(fallback), ...Object.keys(preferred)]);
    for (const key of keys) {
      merged[key] = mergePreferredValue(preferred[key], fallback[key]);
    }
    return merged;
  }

  if (Array.isArray(preferred)) {
    return preferred.length > 0 ? preferred : fallback;
  }

  if (Array.isArray(fallback)) {
    return fallback;
  }

  if (typeof preferred === "string") {
    return preferred.trim().length > 0 ? preferred : (fallback ?? preferred);
  }

  if (preferred !== undefined && preferred !== null) {
    return preferred;
  }

  if (typeof fallback === "string") {
    return fallback;
  }

  return fallback ?? preferred;
}

function mergeToolSegmentMetadata(
  preferred: Record<string, unknown> | undefined,
  fallback: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  const merged = mergePreferredValue(preferred, fallback);
  return isRecord(merged) ? merged : undefined;
}

function pickPreferredToolSegment(
  existing: AssistantTranscriptSegment,
  candidate: AssistantTranscriptSegment,
): AssistantTranscriptSegment {
  const existingRichness = readToolSegmentRichness(existing);
  const candidateRichness = readToolSegmentRichness(candidate);

  if (candidateRichness.score > existingRichness.score) {
    return candidate;
  }

  if (candidateRichness.score < existingRichness.score) {
    return existing;
  }

  if (candidateRichness.hasRenderable && !existingRichness.hasRenderable) {
    return candidate;
  }

  if (!candidateRichness.hasRenderable && existingRichness.hasRenderable) {
    return existing;
  }

  const existingTextScore = scoreStandaloneToolText(existing.text, readSegmentCommand(existing));
  const candidateTextScore = scoreStandaloneToolText(candidate.text, readSegmentCommand(candidate));

  if (candidateTextScore > existingTextScore) {
    return candidate;
  }

  return existing;
}

function mergeOrPickToolSegment(
  existing: AssistantTranscriptSegment | null,
  candidate: AssistantTranscriptSegment | null,
  options?: { preserveStableFields?: boolean },
): AssistantTranscriptSegment | null {
  if (!existing) {
    return candidate;
  }

  if (!candidate) {
    return existing;
  }

  const preferred = pickPreferredToolSegment(existing, candidate);
  const fallback = preferred === existing ? candidate : existing;
  const preferredTextScore = scoreStandaloneToolText(preferred.text, readSegmentCommand(preferred));
  const fallbackTextScore = scoreStandaloneToolText(fallback.text, readSegmentCommand(fallback));
  const merged: AssistantTranscriptSegment = {
    ...fallback,
    ...preferred,
    status: preferPopulatedString(preferred.status, fallback.status),
    title: preferPopulatedString(preferred.title, fallback.title),
    text:
      preferredTextScore >= fallbackTextScore
        ? (preferred.text ?? fallback.text)
        : (fallback.text ?? preferred.text),
    tool_name: preferPopulatedString(preferred.tool_name, fallback.tool_name),
    tool_call_id: preferPopulatedString(preferred.tool_call_id, fallback.tool_call_id),
    updated_at:
      preferPopulatedString(preferred.updated_at, fallback.updated_at) ?? preferred.updated_at,
    metadata: mergeToolSegmentMetadata(preferred.metadata, fallback.metadata),
  };

  if (options?.preserveStableFields) {
    merged.id = existing.id;
    merged.sequence = existing.sequence;
    merged.recorded_at = existing.recorded_at;
  }

  return merged;
}

function buildToolPairs(segments: AssistantTranscriptSegment[]): Map<string, TranscriptToolPair> {
  const pairs = new Map<string, TranscriptToolPair>();

  for (const segment of segments) {
    if (
      !segment.tool_call_id ||
      (segment.kind !== "tool_call" && segment.kind !== "tool_result" && segment.kind !== "error")
    ) {
      continue;
    }

    const existing = pairs.get(segment.tool_call_id);
    const pair: TranscriptToolPair = existing ?? {
      anchorId: segment.id,
      call: null,
      result: null,
      error: null,
    };

    if (segment.kind === "tool_call") {
      pair.call = mergeOrPickToolSegment(pair.call, segment);
    }
    if (segment.kind === "tool_result") {
      pair.result = mergeOrPickToolSegment(pair.result, segment, {
        preserveStableFields: pair.result !== null,
      });
    }
    if (segment.kind === "error") {
      pair.error = mergeOrPickToolSegment(pair.error, segment);
    }

    pairs.set(segment.tool_call_id, pair);
  }

  return pairs;
}

function buildTranscriptBlocks(
  segments: AssistantTranscriptSegment[],
): TranscriptRenderableBlock[] {
  const ordered = [...segments].sort(compareTranscriptSegments);
  const blocks: TranscriptRenderableBlock[] = [];
  const seenPrimaryOutputTexts = new Set<string>();
  const toolPairs = buildToolPairs(ordered);
  const renderedToolPairIds = new Set<string>();

  for (const segment of ordered) {
    if (segment.kind === "reasoning") {
      if (shouldRenderReasoningSegment(segment)) {
        const normalizedText = normalizeMarkdownSpacing(segment.text);
        if (normalizedText) {
          blocks.push({
            type: "reasoning",
            key: `reasoning:${segment.id}`,
            segment: {
              ...segment,
              text: normalizedText,
            },
          });
        }
      }
      continue;
    }

    if (segment.kind === "status") {
      if (shouldRenderStatusSegment(segment)) {
        blocks.push({ type: "cue", key: segment.id, segment });
      }
      continue;
    }

    if (
      segment.tool_call_id &&
      (segment.kind === "tool_call" || segment.kind === "tool_result" || segment.kind === "error")
    ) {
      const pair = toolPairs.get(segment.tool_call_id);
      if (!pair || pair.anchorId !== segment.id || renderedToolPairIds.has(segment.tool_call_id)) {
        continue;
      }

      renderedToolPairIds.add(segment.tool_call_id);
      blocks.push({
        type: "tool",
        key: `tool:${segment.tool_call_id}`,
        call: pair.call,
        result: pair.result,
        error: pair.error,
      });
      continue;
    }

    if (segment.kind === "tool_result") {
      blocks.push({
        type: "tool",
        key: `tool-result:${segment.id}`,
        call: null,
        result: segment,
        error: null,
      });
      continue;
    }

    if (segment.kind === "error" && segment.tool_call_id) {
      blocks.push({
        type: "tool",
        key: `tool-error:${segment.id}`,
        call: null,
        result: null,
        error: segment,
      });
      continue;
    }

    if (segment.kind === "output") {
      const normalizedText = normalizeMarkdownSpacing(segment.text);
      if (normalizedText && !seenPrimaryOutputTexts.has(normalizedText)) {
        seenPrimaryOutputTexts.add(normalizedText);
        blocks.push({
          type: "output",
          key: segment.id,
          segment: {
            ...segment,
            text: normalizedText,
          },
        });
      }
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
  if (assistantContent && !hasAuthoritativeAssistantPrimarySegment(segments, assistantContent)) {
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
    !segments.some(
      (segment) =>
        (segment.kind === "status" || segment.kind === "error") &&
        normalizeForBoilerplateCheck(segment.text) !== null,
    )
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
  const transcript = mergeMissingGenerationToolSegments(
    [...message.assistant_transcript].sort(compareTranscriptSegments),
    generation,
  );
  const content = message.content.trim();

  if (content && !hasAuthoritativeAssistantPrimarySegment(transcript, content)) {
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

function readInlineToolLabel(
  call: AssistantTranscriptSegment | null,
  result: AssistantTranscriptSegment | null,
  error: AssistantTranscriptSegment | null,
): string {
  const reference = result ?? call ?? error;
  if (!reference) {
    return "Tool";
  }

  return (
    inferSkillTitle(reference) ??
    (reference.tool_name ? humanizeIdentifier(reference.tool_name) : "Tool")
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

  const command = readSegmentCommand(call ?? result ?? reference) ?? "Shell";
  const stdout = readPrioritizedShellDisplayField([result, call, error], ["stdout"]);
  const stderr = readPrioritizedShellDisplayField([result, error, call], ["stderr"]);
  const exitCode = readPrioritizedShellDisplayField([result, call, error], ["exit_code"]);
  const status = error ? "failed" : (result?.status ?? call?.status ?? null);
  const shellErrorText = readShellErrorText({
    error,
    result,
    call,
    status,
  });
  const outputFallback =
    !hasVisibleShellText(stdout) && !hasVisibleShellText(stderr)
      ? (readShellFallbackOutput(result, command, shellErrorText) ??
        readShellFallbackOutput(call, command, shellErrorText) ??
        readShellFallbackOutput(error, command, shellErrorText))
      : null;
  const artifacts = [
    ...readShellArtifacts(result),
    ...readShellArtifacts(call),
    ...readShellArtifacts(error),
  ].filter((artifact, index, allArtifacts) => allArtifacts.indexOf(artifact) === index);

  return (
    <details
      className={`assistant-tool-block assistant-shell-block${error ? " assistant-shell-block-error" : ""}`}
      data-status={status ?? undefined}
    >
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
          <pre className="assistant-terminal-output">
            <span className="assistant-terminal-output-prompt">$ {command}</span>
            {stdout !== null && stdout.text ? `\n${stdout.text}` : ""}
            {stderr !== null && stderr.text ? `\n${stderr.text}` : ""}
            {outputFallback !== null && outputFallback.text ? `\n${outputFallback.text}` : ""}
          </pre>
        </div>
        {exitCode !== null ? (
          <p className="assistant-tool-inline-meta">退出码：{exitCode.text || "(empty)"}</p>
        ) : null}
        {artifacts.length > 0 ? (
          <div className="assistant-tool-detail-group">
            <span className="assistant-tool-detail-label">产物</span>
            <div className="chat-bubble-artifacts assistant-transcript-artifacts">
              {artifacts.map((artifact) => (
                <span key={`${reference.id}:${artifact}`} className="chat-artifact-chip">
                  {artifact}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {shellErrorText ? <p className="assistant-tool-error-copy">{shellErrorText}</p> : null}
      </div>
    </details>
  );
}

function AssistantInlineSkillTag({ label }: { label: string }) {
  return <strong className="assistant-inline-skill-tag">{label}</strong>;
}

function AssistantReasoningBlock({
  segment,
  isFinal,
}: {
  segment: AssistantTranscriptSegment;
  isFinal: boolean;
}) {
  return (
    <div
      className={`assistant-reasoning-block${isFinal ? " assistant-reasoning-block-final" : ""}`}
    >
      {normalizeMarkdownSpacing(segment.text)
        ? renderAssistantMarkdownMessage(segment.text ?? "")
        : renderMarkdownMessage("")}
    </div>
  );
}

function readAutoroutedSkillName(segment: AssistantTranscriptSegment): string | null {
  const skill = readSegmentMetadata(segment)?.skill;
  if (typeof skill === "string" && skill.trim().length > 0) {
    return skill.trim();
  }

  const normalized = normalizeForBoilerplateCheck(segment.text);
  const match = normalized?.match(/(?:自动选择(?:了)?(?:技能)?[:：]?\s*)(.+)$/);
  return match?.[1]?.trim() || null;
}

function AssistantInlineCue({ segment }: { segment: AssistantTranscriptSegment }) {
  const text = normalizeMarkdownSpacing(segment.text);
  if (!text) {
    return null;
  }

  const state = readSegmentState(segment);
  if (state === "skill.autoroute.selected") {
    const skillName = readAutoroutedSkillName(segment);
    return (
      <div className="assistant-inline-cue assistant-inline-cue-skill">
        <span className="assistant-inline-cue-prefix">自动选择</span>
        <AssistantInlineSkillTag label={skillName ?? text} />
      </div>
    );
  }

  return <p className="assistant-inline-cue assistant-inline-cue-muted">{text}</p>;
}

function renderInlineSkillLabel(
  call: AssistantTranscriptSegment | null,
  result: AssistantTranscriptSegment | null,
  error: AssistantTranscriptSegment | null,
) {
  const reference = result ?? error ?? call;
  if (!reference) {
    return null;
  }

  return <AssistantInlineSkillTag label={readInlineToolLabel(call, result, error)} />;
}

function AssistantErrorBlock({ segment }: { segment: AssistantTranscriptSegment }) {
  const detail =
    normalizeMarkdownSpacing(segment.text) ??
    readFirstString(readSegmentMetadata(segment), ["detail", "error", "message"]);

  return (
    <div className="assistant-error-block" role="status">
      <strong className="assistant-error-title">执行失败</strong>
      {detail ? <p className="assistant-error-copy">{detail}</p> : null}
    </div>
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
      {normalizeMarkdownSpacing(segment.text)
        ? renderAssistantMarkdownMessage(segment.text ?? "")
        : renderMarkdownMessage("")}
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
  const shellLikeTool =
    reference.tool_name === "execute_kali_command" ||
    reference.tool_name === "bash" ||
    reference.tool_name === "sh" ||
    reference.tool_name === "zsh";
  if (shellLikeTool || command) {
    return <AssistantShellBlock call={call} result={result} error={error} />;
  }

  return (
    <>
      {renderInlineSkillLabel(call, result, error)}
      {error ? <AssistantErrorBlock segment={error} /> : null}
    </>
  );
}

export function ConversationFeed(props: ConversationFeedProps) {
  const {
    messages,
    generations,
    events,
    activeGeneration = null,
    queuedGenerations = [],
    messageActionBusyId,
    cancelGenerationBusy = false,
    onCancelGeneration,
    onEditMessage,
  } = props;
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const compactionNotes = useMemo(() => buildVisibleCompactionNotes(events), [events]);

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
    const lastCompactionNote = compactionNotes[compactionNotes.length - 1] ?? null;

    return [
      messages.length,
      lastMessage?.id ?? "none",
      lastMessage?.content.length ?? 0,
      mergedGenerations.length,
      lastGeneration?.id ?? "none",
      lastGeneration?.status ?? "none",
      lastGeneration?.updated_at ?? "none",
      queuedGenerations.length,
      compactionNotes.length,
      lastCompactionNote?.id ?? "none",
    ].join(":");
  }, [compactionNotes, mergedGenerations, messages, queuedGenerations.length]);

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
    if (!editingMessageId) {
      return;
    }

    editTextareaRef.current?.focus();
  }, [editingMessageId]);

  useEffect(() => {
    if (!editingMessageId) {
      return;
    }

    if (!messages.some((message) => message.id === editingMessageId)) {
      setEditingMessageId(null);
      setEditingContent("");
    }
  }, [editingMessageId, messages]);

  function renderTranscriptSegments(segments: AssistantTranscriptSegment[]) {
    if (segments.length === 0) {
      return renderMarkdownMessage("");
    }

    const blocks = buildTranscriptBlocks(segments);
    if (blocks.length === 0) {
      return renderMarkdownMessage("");
    }

    const lastPrimaryTextBlockIndex = (() => {
      for (let index = blocks.length - 1; index >= 0; index -= 1) {
        if (blocks[index]?.type === "output" || blocks[index]?.type === "reasoning") {
          return index;
        }
      }
      return -1;
    })();

    return (
      <div className="assistant-transcript">
        {blocks.map((block, index) => {
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

          if (block.type === "cue") {
            return <AssistantInlineCue key={block.key} segment={block.segment} />;
          }

          if (block.type === "reasoning") {
            return (
              <AssistantReasoningBlock
                key={block.key}
                segment={block.segment}
                isFinal={index === lastPrimaryTextBlockIndex}
              />
            );
          }

          return (
            <AssistantPrimaryOutput
              key={block.key}
              segment={block.segment}
              isFinal={index === lastPrimaryTextBlockIndex}
            />
          );
        })}
      </div>
    );
  }

  function startInlineEdit(message: SessionMessage) {
    setEditingMessageId(message.id);
    setEditingContent(message.content);
  }

  function cancelInlineEdit() {
    setEditingMessageId(null);
    setEditingContent("");
  }

  async function saveInlineEdit(message: SessionMessage): Promise<void> {
    if (typeof onEditMessage !== "function") {
      return;
    }

    const trimmed = editingContent.trim();
    if (!trimmed || trimmed === message.content.trim()) {
      cancelInlineEdit();
      return;
    }

    await onEditMessage(message, trimmed);
    cancelInlineEdit();
  }

  function renderUserEditTrigger(message: SessionMessage) {
    const isBusy = messageActionBusyId === message.id;
    if (message.role !== "user" || typeof onEditMessage !== "function") {
      return null;
    }

    return (
      <div className="chat-bubble-action-shell">
        <button
          className="chat-bubble-edit-trigger"
          type="button"
          aria-label="返回并编辑消息"
          disabled={isBusy}
          onClick={() => startInlineEdit(message)}
        >
          <span aria-hidden="true" className="chat-bubble-edit-trigger-icon">
            ↩
          </span>
        </button>
      </div>
    );
  }

  function renderInlineEditComposer(message: SessionMessage) {
    const isBusy = messageActionBusyId === message.id;
    const trimmedContent = editingContent.trim();
    const isSaveDisabled =
      isBusy || trimmedContent.length === 0 || trimmedContent === message.content.trim();

    return (
      <form
        className="chat-bubble-inline-editor"
        onSubmit={(event) => {
          event.preventDefault();
          void saveInlineEdit(message);
        }}
      >
        <textarea
          ref={editTextareaRef}
          className="field-textarea chat-bubble-inline-editor-input"
          value={editingContent}
          rows={Math.max(3, message.content.split(/\r?\n/).length)}
          disabled={isBusy}
          onChange={(event) => setEditingContent(event.target.value)}
        />
        <div className="chat-bubble-inline-editor-actions">
          <button className="inline-button" type="submit" disabled={isSaveDisabled}>
            保存
          </button>
          <button
            className="text-button"
            type="button"
            disabled={isBusy}
            onClick={cancelInlineEdit}
          >
            取消
          </button>
        </div>
      </form>
    );
  }

  function renderAssistantBubble(
    message: SessionMessage,
    generation: ChatGeneration | null = null,
  ) {
    const transcript = buildAssistantTranscript(message, generation);
    const generationStatus = generation?.status ?? message.status ?? null;
    const canCancelGeneration =
      generation !== null &&
      typeof onCancelGeneration === "function" &&
      (generation.status === "queued" || generation.status === "running");

    return (
      <article
        key={message.id}
        className="chat-bubble chat-bubble-assistant chat-bubble-assistant-transcript"
        data-status={generationStatus ?? undefined}
      >
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {generation?.status === "queued" && generation.queue_position ? (
              <span className="management-token-chip">排队 #{generation.queue_position}</span>
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
        data-status={generation.status}
      >
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">助手</strong>
          <div className="chat-bubble-meta-actions">
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

    const isEditing = editingMessageId === message.id;

    return (
      <article
        key={message.id}
        className={`chat-bubble chat-bubble-${message.role}`}
        data-status={message.status ?? undefined}
      >
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {message.status && message.status !== "completed" ? (
              <StatusBadge status={message.status} />
            ) : null}
            {!isEditing ? renderUserEditTrigger(message) : null}
          </div>
        </div>
        {isEditing ? renderInlineEditComposer(message) : renderUserMessage(message.content)}
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
    queuedGenerations.length > 0 ||
    compactionNotes.length > 0;

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

      {compactionNotes.map((eventNote) => (
        <ConversationEventNote key={eventNote.id} summary={eventNote.summary} />
      ))}
    </section>
  );
}
