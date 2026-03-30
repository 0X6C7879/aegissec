import { useState } from "react";
import { formatRelativeTime } from "../lib/format";
import type { SessionSummary } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type SessionSidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  includeDeleted: boolean;
  isCreating: boolean;
  onCreate: (title: string) => Promise<void>;
  onIncludeDeletedChange: (value: boolean) => void;
  onSelect: (sessionId: string) => void;
};

export function SessionSidebar({
  sessions,
  activeSessionId,
  includeDeleted,
  isCreating,
  onCreate,
  onIncludeDeletedChange,
  onSelect,
}: SessionSidebarProps) {
  const [createTitle, setCreateTitle] = useState("");

  async function handleCreateSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await onCreate(createTitle);
    setCreateTitle("");
  }

  return (
    <aside className="panel session-sidebar">
      <div className="panel-header">
        <div className="panel-header-copy">
          <p className="eyebrow">Authorized research workspace</p>
          <h1 className="panel-title">Sessions</h1>
          <p className="panel-description">
            Maintain reproducible validation conversations, preserve message history, and monitor
            live backend events as each authorized workflow evolves.
          </p>
        </div>
      </div>

      <form className="session-create-form" onSubmit={handleCreateSubmit}>
        <label className="field-label" htmlFor="create-session-title">
          Create a new session
          <input
            id="create-session-title"
            className="field-input"
            type="text"
            value={createTitle}
            onChange={(event) => setCreateTitle(event.target.value)}
            placeholder="Optional session title"
          />
        </label>
        <div className="button-row">
          <button className="button button-primary" type="submit" disabled={isCreating}>
            {isCreating ? "Creating…" : "Create session"}
          </button>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(event) => onIncludeDeletedChange(event.target.checked)}
            />
            Include deleted
          </label>
        </div>
      </form>

      {sessions.length === 0 ? (
        <section className="empty-state">
          <h2 className="panel-title">No sessions yet</h2>
          <p className="empty-copy">
            Start a session to capture chat history, status updates, and live event activity from the
            backend.
          </p>
        </section>
      ) : (
        <ul className="session-list">
          {sessions.map((session) => {
            const isActive = session.id === activeSessionId;

            return (
              <li key={session.id} className="session-item">
                <button
                  className={`session-link${isActive ? " session-link-active" : ""}${
                    session.deleted_at ? " session-link-deleted" : ""
                  }`}
                  type="button"
                  onClick={() => onSelect(session.id)}
                >
                  <div className="session-link-header">
                    <h2 className="session-link-title">{session.title}</h2>
                    <StatusBadge status={session.status} />
                  </div>

                  <p className="session-link-subtitle">
                    {session.deleted_at
                      ? "Deleted session retained for audit continuity."
                      : "Active conversation history and event stream available."}
                  </p>

                  <div className="session-meta-row">
                    <span className="session-meta-copy">Updated {formatRelativeTime(session.updated_at)}</span>
                    {session.deleted_at ? <span className="meta-pill">Deleted</span> : null}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
