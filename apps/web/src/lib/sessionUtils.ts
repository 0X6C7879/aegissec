import type {
  AttachmentMetadata,
  SessionDetail,
  SessionMessage,
  SessionSummary,
} from "../types/sessions";

const THINK_BLOCK_PATTERN = /<think>[\s\S]*?<\/think>/gi;
const MAX_SAFE_SUMMARY_LENGTH = 280;

export type SafeSessionSummaryEntry = {
  label: string;
  summary: string;
  tone: "neutral" | "connected" | "warning" | "success" | "error";
};

function toTimestamp(value: string): number {
  return new Date(value).getTime();
}

function isOptimisticUserMessage(message: SessionMessage): boolean {
  return message.role === "user" && message.id.startsWith("optimistic-user-");
}

export function sortSessions(sessions: SessionSummary[]): SessionSummary[] {
  return [...sessions].sort(
    (left, right) => toTimestamp(right.updated_at) - toTimestamp(left.updated_at),
  );
}

export function upsertSession(
  sessions: SessionSummary[] | undefined,
  session: SessionSummary,
): SessionSummary[] {
  const currentSessions = sessions ?? [];
  const remainingSessions = currentSessions.filter((item) => item.id !== session.id);
  return sortSessions([session, ...remainingSessions]);
}

export function mergeSessionMessage(
  detail: SessionDetail | undefined,
  message: SessionMessage,
): SessionDetail | undefined {
  if (!detail || detail.id !== message.session_id) {
    return detail;
  }

  const remainingMessages = detail.messages.filter((item) => item.id !== message.id);
  const reconciledMessages =
    message.role === "user" && !isOptimisticUserMessage(message)
      ? remainingMessages.filter(
          (item) =>
            !(isOptimisticUserMessage(item) && item.content.trim() === message.content.trim()),
        )
      : remainingMessages;
  const nextMessages = [...reconciledMessages, message].sort(
    (left, right) => toTimestamp(left.created_at) - toTimestamp(right.created_at),
  );

  return {
    ...detail,
    messages: nextMessages,
  };
}

export function mergeSessionMessages(
  detail: SessionDetail | undefined,
  messages: SessionMessage[],
): SessionDetail | undefined {
  return messages.reduce<SessionDetail | undefined>(
    (currentDetail, message) => mergeSessionMessage(currentDetail, message),
    detail,
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readFirstNonEmptyString(
  value: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate;
    }
  }

  return null;
}

function sanitizeSafeSummaryText(value: string): string {
  const cleaned = value.replace(THINK_BLOCK_PATTERN, " ").replace(/\s+/g, " ").trim();
  if (cleaned.length <= MAX_SAFE_SUMMARY_LENGTH) {
    return cleaned;
  }

  return `${cleaned.slice(0, MAX_SAFE_SUMMARY_LENGTH - 1).trimEnd()}…`;
}

function buildSessionStatusSummary(value: Record<string, unknown>): SafeSessionSummaryEntry | null {
  const status = typeof value.status === "string" ? value.status : null;
  if (!status) {
    return null;
  }

  const queuedPromptCount =
    typeof value.queued_prompt_count === "number" && value.queued_prompt_count > 0
      ? value.queued_prompt_count
      : null;

  switch (status) {
    case "running":
      return {
        label: "模型进度",
        summary: queuedPromptCount
          ? `正在生成回复，后面还有 ${queuedPromptCount} 条提示排队。`
          : "正在生成回复，可继续整理下一条提示。",
        tone: "connected",
      };
    case "done":
      return {
        label: "模型进度",
        summary: "当前回复已完成，可继续发送下一条提示。",
        tone: "success",
      };
    case "cancelled":
      return {
        label: "中断反馈",
        summary: "当前回复已停止，可立即继续下一步。",
        tone: "warning",
      };
    case "error": {
      const errorMessage = readFirstNonEmptyString(value, ["error", "message"]);
      return {
        label: "模型进度",
        summary: errorMessage
          ? `生成过程中出现异常：${sanitizeSafeSummaryText(errorMessage)}`
          : "生成过程中出现异常。",
        tone: "error",
      };
    }
    default:
      return null;
  }
}

function isAssistantSummaryType(type: string): boolean {
  return (
    type === "assistant.summary" ||
    type === "assistant.status" ||
    type.startsWith("assistant.summary.") ||
    type.startsWith("assistant.progress.") ||
    type === "assistant.progress"
  );
}

export function extractSafeSessionSummary(
  type: string,
  data: unknown,
): SafeSessionSummaryEntry | null {
  if (!isRecord(data)) {
    return null;
  }

  if (type === "generation.started") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && data.queued_prompt_count > 0
        ? data.queued_prompt_count
        : null;

    return {
      label: "模型进度",
      summary: queuedPromptCount
        ? `新一轮生成已开始，后面还有 ${queuedPromptCount} 条提示等待处理。`
        : "新一轮生成已开始，助手正在整理回复。",
      tone: "connected",
    };
  }

  if (type === "generation.cancelled") {
    return {
      label: "中断反馈",
      summary: "当前回复已停止，已保留到目前为止的可见输出。",
      tone: "warning",
    };
  }

  if (type === "session.updated") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && data.queued_prompt_count > 0
        ? data.queued_prompt_count
        : null;
    if (queuedPromptCount !== null) {
      const summaryText = readFirstNonEmptyString(data, [
        "queued_prompt_summary",
        "queued_prompt_message",
      ]);
      return {
        label: "排队提示",
        summary: summaryText
          ? sanitizeSafeSummaryText(summaryText)
          : `已有 ${queuedPromptCount} 条提示在排队区等待执行。`,
        tone: "neutral",
      };
    }

    return buildSessionStatusSummary(data);
  }

  if (!isAssistantSummaryType(type)) {
    return null;
  }

  const summaryText = readFirstNonEmptyString(data, [
    "safe_summary",
    "summary",
    "message",
    "status_text",
  ]);

  if (!summaryText) {
    return null;
  }

  const status = typeof data.status === "string" ? data.status : null;
  const phase = typeof data.phase === "string" ? data.phase : null;
  const label =
    readFirstNonEmptyString(data, ["label", "title"]) ??
    (phase ? `思路摘要 · ${phase.replace(/_/g, " ")}` : "思路摘要");

  return {
    label,
    summary: sanitizeSafeSummaryText(summaryText),
    tone:
      status === "error"
        ? "error"
        : status === "cancelled"
          ? "warning"
          : status === "done"
            ? "success"
            : status === "running"
              ? "connected"
              : "neutral",
  };
}

export function shouldStoreRealtimeEvent(type: string, data: unknown): boolean {
  if (type === "message.created" || type === "message.updated") {
    return false;
  }

  if (type === "assistant.trace") {
    return isRecord(data) && data.status === "error";
  }

  if (type === "session.updated") {
    return extractSafeSessionSummary(type, data) !== null;
  }

  if (isAssistantSummaryType(type)) {
    return extractSafeSessionSummary(type, data) !== null;
  }

  return true;
}

export function isSessionSummary(value: unknown): value is SessionSummary {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.status === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    (typeof value.deleted_at === "string" || value.deleted_at === null)
  );
}

export function isSessionMessage(value: unknown): value is SessionMessage {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.role === "string" &&
    typeof value.content === "string" &&
    typeof value.created_at === "string"
  );
}

function isAttachmentMetadata(value: unknown): value is AttachmentMetadata {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.content_type === "string" &&
    typeof value.size_bytes === "number"
  );
}

function toAttachmentMetadataList(value: unknown): AttachmentMetadata[] {
  return Array.isArray(value) ? value.filter(isAttachmentMetadata) : [];
}

export function toSessionSummaryUpdate(
  currentSession: SessionSummary,
  value: unknown,
  createdAt: string,
): SessionSummary | null {
  if (!isRecord(value)) {
    return null;
  }

  return {
    ...currentSession,
    title: typeof value.title === "string" ? value.title : currentSession.title,
    status: typeof value.status === "string" ? value.status : currentSession.status,
    deleted_at:
      typeof value.deleted_at === "string" || value.deleted_at === null
        ? value.deleted_at
        : currentSession.deleted_at,
    updated_at: createdAt,
  };
}

export function toSessionMessageEvent(
  value: unknown,
  sessionId: string,
  createdAt: string,
): SessionMessage | null {
  if (!isRecord(value)) {
    return null;
  }

  const messageId =
    typeof value.id === "string"
      ? value.id
      : typeof value.message_id === "string"
        ? value.message_id
        : null;

  if (!messageId || typeof value.role !== "string" || typeof value.content !== "string") {
    return null;
  }

  return {
    id: messageId,
    session_id: typeof value.session_id === "string" ? value.session_id : sessionId,
    role: value.role,
    content: value.content,
    attachments: toAttachmentMetadataList(value.attachments),
    created_at: typeof value.created_at === "string" ? value.created_at : createdAt,
  };
}

export function buildEventSummary(type: string, data: unknown): string {
  const safeSummary = extractSafeSessionSummary(type, data);
  if (safeSummary) {
    return safeSummary.summary;
  }

  if (type.startsWith("session.") && isRecord(data)) {
    const title = typeof data.title === "string" ? data.title : "当前会话";
    const status = typeof data.status === "string" ? data.status : "已更新";
    const isDeleted = typeof data.deleted_at === "string";
    return `${title} 状态已更新为 ${status}${isDeleted ? "（已归档）" : "。"}`;
  }

  if (type === "assistant.trace" && isRecord(data)) {
    if (typeof data.message === "string") {
      return data.message;
    }

    return "模型请求失败。";
  }

  if (type === "tool.call.started" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "未命名命令";
    return `模型已触发工具调用：${command}`;
  }

  if (type === "tool.call.finished" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "工具调用";
    const status = typeof data.status === "string" ? data.status : "完成";
    return `${command} 执行结束，状态：${status}。`;
  }

  if (type === "tool.call.failed" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "工具调用";
    const error = typeof data.error === "string" ? data.error : "未知错误";
    return `${command} 执行失败：${error}`;
  }

  if (type === "message.created" && isRecord(data)) {
    const role = typeof data.role === "string" ? data.role : "message";
    const content = typeof data.content === "string" ? data.content : "";
    const preview = content.trim().replace(/\s+/g, " ").slice(0, 72);
    if (role === "assistant") {
      return `模型已回复${preview ? `：${preview}` : "。"}`;
    }
    if (role === "user") {
      return `你发送了新消息${preview ? `：${preview}` : "。"}`;
    }
    return `收到新消息${preview ? `：${preview}` : "。"}`;
  }

  if (type === "message.updated" && isRecord(data)) {
    const role = typeof data.role === "string" ? data.role : "message";
    if (role === "assistant") {
      return "模型回复正在更新。";
    }
    return "消息内容已更新。";
  }

  return "收到新的实时事件。";
}
