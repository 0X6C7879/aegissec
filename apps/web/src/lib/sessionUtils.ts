import type {
  AttachmentMetadata,
  SessionDetail,
  SessionMessage,
  SessionSummary,
} from "../types/sessions";

function toTimestamp(value: string): number {
  return new Date(value).getTime();
}

function isOptimisticUserMessage(message: SessionMessage): boolean {
  return message.role === "user" && message.id.startsWith("optimistic-user-");
}

export function sortSessions(sessions: SessionSummary[]): SessionSummary[] {
  return [...sessions].sort((left, right) => toTimestamp(right.updated_at) - toTimestamp(left.updated_at));
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
          (item) => !(isOptimisticUserMessage(item) && item.content.trim() === message.content.trim()),
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
