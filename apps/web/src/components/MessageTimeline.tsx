import { formatDateTime, formatBytes } from "../lib/format";
import type { SessionMessage } from "../types/sessions";

type MessageTimelineProps = {
  messages: SessionMessage[];
};

export function MessageTimeline({ messages }: MessageTimelineProps) {
  return (
    <section className="message-timeline">
      <div className="panel-header">
        <div className="panel-header-copy">
          <p className="eyebrow">History</p>
          <h2 className="panel-title">Chat transcript</h2>
        </div>
      </div>

      {messages.length === 0 ? (
        <p className="message-empty">
          No messages in this session yet. Send a prompt below to create a retained transcript entry.
        </p>
      ) : (
        <div className="message-list">
          {messages.map((message) => (
            <article key={message.id} className={`message-item message-item-${message.role}`}>
              <div className="message-header">
                <p className="message-role">{message.role}</p>
                <span className="timestamp-label">{formatDateTime(message.created_at)}</span>
              </div>
              <pre className="message-content">{message.content}</pre>
              {message.attachments.length > 0 ? (
                <div className="attachment-list">
                  {message.attachments.map((attachment) => (
                    <span key={attachment.id} className="attachment-chip">
                      {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
                    </span>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
