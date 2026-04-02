import { useMemo } from "react";
import { formatBytes } from "../lib/format";
import { useUiStore } from "../store/uiStore";
import type { AttachmentMetadata } from "../types/sessions";

type ChatComposerProps = {
  sessionId: string;
  disabled: boolean;
  isSending: boolean;
  onSend: (content: string, attachments: AttachmentMetadata[]) => Promise<void>;
};

export function ChatComposer({ sessionId, disabled, isSending, onSend }: ChatComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const updateAttachmentForm = useUiStore((state) => state.updateAttachmentForm);
  const addDraftAttachment = useUiStore((state) => state.addDraftAttachment);
  const removeDraftAttachment = useUiStore((state) => state.removeDraftAttachment);
  const clearDraft = useUiStore((state) => state.clearDraft);

  const draftContent = draft?.content ?? "";
  const draftAttachments = draft?.attachments ?? [];
  const attachmentForm = draft?.attachmentForm ?? {
    name: "",
    contentType: "application/octet-stream",
    sizeBytes: "0",
  };

  const isSubmitDisabled = useMemo(() => {
    return disabled || draftContent.trim().length === 0;
  }, [disabled, draftContent]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedContent = draftContent.trim();

    if (!trimmedContent) {
      return;
    }

    await onSend(trimmedContent, draftAttachments);
    clearDraft(sessionId);
  }

  return (
    <section className="chat-compose-panel">
      <div className="panel-header">
        <div className="panel-header-copy">
          <p className="eyebrow">Compose</p>
          <h2 className="panel-title">Send a message</h2>
        </div>
      </div>

      <form className="compose-form" onSubmit={handleSubmit}>
        <label className="field-label" htmlFor={`chat-draft-${sessionId}`}>
          Prompt
          <textarea
            id={`chat-draft-${sessionId}`}
            className="field-textarea"
            value={draftContent}
            onChange={(event) => setDraftContent(sessionId, event.target.value)}
            placeholder="Describe the validation step, evidence request, or session note to send."
            disabled={disabled}
          />
        </label>

        <div className="compose-meta">
          <p className="panel-description">
            Attachment metadata is optional. This UI records name, content type, and size only.
          </p>
          <div className="field-inline-group">
            <label className="field-label" htmlFor={`attachment-name-${sessionId}`}>
              Name
              <input
                id={`attachment-name-${sessionId}`}
                className="field-inline-input"
                type="text"
                value={attachmentForm.name}
                onChange={(event) => updateAttachmentForm(sessionId, "name", event.target.value)}
                disabled={disabled}
              />
            </label>

            <label className="field-label" htmlFor={`attachment-type-${sessionId}`}>
              Content type
              <input
                id={`attachment-type-${sessionId}`}
                className="field-inline-input"
                type="text"
                value={attachmentForm.contentType}
                onChange={(event) =>
                  updateAttachmentForm(sessionId, "contentType", event.target.value)
                }
                disabled={disabled}
              />
            </label>

            <label className="field-label" htmlFor={`attachment-size-${sessionId}`}>
              Size (bytes)
              <input
                id={`attachment-size-${sessionId}`}
                className="field-inline-input"
                type="number"
                min="0"
                value={attachmentForm.sizeBytes}
                onChange={(event) =>
                  updateAttachmentForm(sessionId, "sizeBytes", event.target.value)
                }
                disabled={disabled}
              />
            </label>
          </div>

          <div className="metadata-row">
            <button
              className="inline-button"
              type="button"
              disabled={disabled}
              onClick={() => {
                addDraftAttachment(sessionId);
              }}
            >
              Add attachment metadata
            </button>
          </div>

          {draftAttachments.length > 0 ? (
            <div className="attachment-metadata-list">
              {draftAttachments.map((attachment) => (
                <span key={attachment.id} className="attachment-metadata-item">
                  {attachment.name} · {attachment.content_type} ·{" "}
                  {formatBytes(attachment.size_bytes)}
                  <button
                    className="chip-button"
                    type="button"
                    onClick={() => removeDraftAttachment(sessionId, attachment.id)}
                    aria-label={`Remove ${attachment.name}`}
                    disabled={disabled}
                  >
                    Remove
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="button-row">
          <button className="button button-primary" type="submit" disabled={isSubmitDisabled}>
            {isSending ? "Queue message" : "Send message"}
          </button>
        </div>
      </form>
    </section>
  );
}
