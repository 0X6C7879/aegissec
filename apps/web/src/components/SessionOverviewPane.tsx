import { formatDateTime } from "../lib/format";
import type { SessionDetail, SessionSummary } from "../types/sessions";
import { SessionDetailHeader } from "./SessionDetailHeader";

type SessionOverviewPaneProps = {
  session: SessionSummary | null;
  detail: SessionDetail | null;
  isRenaming: boolean;
  isDeleting: boolean;
  isRestoring: boolean;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onRestore: () => Promise<void>;
  onOpenChat: (sessionId: string) => void;
};

export function SessionOverviewPane({
  session,
  detail,
  isRenaming,
  isDeleting,
  isRestoring,
  onRename,
  onDelete,
  onRestore,
  onOpenChat,
}: SessionOverviewPaneProps) {
  if (!session) {
    return (
      <section className="panel workspace-pane">
        <div className="empty-state">
          <p className="eyebrow">Session workspace</p>
          <h1 className="panel-title">Create or select a session</h1>
          <p className="empty-copy">
            Use the session list to start a new evidence trail, revisit an existing conversation, or
            restore a previously archived record.
          </p>
        </div>
      </section>
    );
  }

  const previewMessages = detail?.messages.slice(-3).reverse() ?? [];

  return (
    <section className="panel workspace-pane">
      <SessionDetailHeader
        session={session}
        subtitle="Review the current session state before jumping into the live chat stream."
        isRenaming={isRenaming}
        isDeleting={isDeleting}
        isRestoring={isRestoring}
        onRename={onRename}
        onDelete={onDelete}
        onRestore={onRestore}
        onOpenChat={() => onOpenChat(session.id)}
      />

      {session.deleted_at ? (
        <div className="notice">
          This session has been soft deleted. Restore it to continue the conversation or retain it
          as part of your authorized research record.
        </div>
      ) : null}

      <div className="session-summary-grid">
        <article className="summary-card">
          <p className="summary-label">Messages</p>
          <p className="summary-value">{detail?.messages.length ?? 0}</p>
          <p className="session-meta-copy">Ordered history is reloaded from the API on refresh.</p>
        </article>

        <article className="summary-card">
          <p className="summary-label">Last activity</p>
          <p className="summary-value">{formatDateTime(session.updated_at)}</p>
          <p className="session-meta-copy">
            Status and title changes stay aligned with backend state.
          </p>
        </article>

        <article className="summary-card">
          <p className="summary-label">Retention</p>
          <p className="summary-value">{session.deleted_at ? "Soft deleted" : "Active"}</p>
          <p className="session-meta-copy">
            Deleted sessions remain restorable for audit continuity.
          </p>
        </article>
      </div>

      <section className="empty-state">
        <h2 className="panel-title">Recent message preview</h2>
        {previewMessages.length === 0 ? (
          <p className="message-empty">
            No messages yet. Open the chat view to send the first prompt and start streaming events.
          </p>
        ) : (
          <ul className="message-preview-list">
            {previewMessages.map((message) => (
              <li key={message.id} className={`message-item message-item-${message.role}`}>
                <div className="message-header">
                  <p className="message-role">{message.role}</p>
                  <span className="timestamp-label">{formatDateTime(message.created_at)}</span>
                </div>
                <p className="message-snippet">{message.content}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
