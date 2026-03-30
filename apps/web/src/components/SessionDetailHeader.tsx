import { useState } from "react";
import { formatDateTime } from "../lib/format";
import type { SessionSummary } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

type SessionDetailHeaderProps = {
  session: SessionSummary;
  subtitle: string;
  isRenaming: boolean;
  isDeleting: boolean;
  isRestoring: boolean;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onRestore: () => Promise<void>;
  onOpenChat?: () => void;
};

export function SessionDetailHeader({
  session,
  subtitle,
  isRenaming,
  isDeleting,
  isRestoring,
  onRename,
  onDelete,
  onRestore,
  onOpenChat,
}: SessionDetailHeaderProps) {
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(session.title);

  async function handleRenameSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const nextTitle = titleDraft.trim();

    if (!nextTitle || nextTitle === session.title) {
      setIsEditingTitle(false);
      setTitleDraft(session.title);
      return;
    }

    await onRename(nextTitle);
    setIsEditingTitle(false);
  }

  async function handleDelete(): Promise<void> {
    const isConfirmed = window.confirm(
      `Soft delete "${session.title}"? You can restore it later from the session workspace.`,
    );

    if (!isConfirmed) {
      return;
    }

    await onDelete();
  }

  return (
    <header className="workspace-pane-header">
      <div className="session-title-row">
        <div className="panel-header-copy">
          <p className="eyebrow">Session detail</p>
          <h1 className="session-title">{session.title}</h1>
          <p className="session-copy">{subtitle}</p>
        </div>

        <div className="action-row">
          <StatusBadge status={session.status} />
          {session.deleted_at ? <span className="meta-pill">Deleted</span> : null}
        </div>
      </div>

      <div className="session-detail-meta">
        <div className="session-header-meta">
          <span className="session-meta-copy">Created {formatDateTime(session.created_at)}</span>
          <span className="session-meta-copy">Updated {formatDateTime(session.updated_at)}</span>
          <span className="session-meta-copy">ID {session.id}</span>
        </div>

        <div className="action-row">
          {onOpenChat ? (
            <button className="inline-button" type="button" onClick={onOpenChat}>
              Open chat
            </button>
          ) : null}
          {!session.deleted_at ? (
            <>
              <button
                className="inline-button"
                type="button"
                onClick={() => {
                  setIsEditingTitle((currentValue) => !currentValue);
                  setTitleDraft(session.title);
                }}
              >
                {isEditingTitle ? "Cancel rename" : "Rename"}
              </button>
              <button
                className="inline-button button-danger"
                type="button"
                disabled={isDeleting}
                onClick={() => {
                  void handleDelete();
                }}
              >
                {isDeleting ? "Deleting…" : "Delete"}
              </button>
            </>
          ) : (
            <button
              className="inline-button"
              type="button"
              disabled={isRestoring}
              onClick={() => {
                void onRestore();
              }}
            >
              {isRestoring ? "Restoring…" : "Restore"}
            </button>
          )}
        </div>
      </div>

      {isEditingTitle ? (
        <form className="rename-form" onSubmit={handleRenameSubmit}>
          <label className="field-label" htmlFor={`rename-${session.id}`}>
            Rename session
            <input
              id={`rename-${session.id}`}
              className="field-inline-input"
              type="text"
              value={titleDraft}
              onChange={(event) => setTitleDraft(event.target.value)}
            />
          </label>
          <div className="inline-action-row">
            <button className="button button-primary" type="submit" disabled={isRenaming}>
              {isRenaming ? "Saving…" : "Save title"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setIsEditingTitle(false);
                setTitleDraft(session.title);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </header>
  );
}
