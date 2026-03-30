import { useUiStore } from "../store/uiStore";
import type { AttachmentMetadata, SessionDetail, SessionEventEntry, SessionSummary } from "../types/sessions";
import { ChatComposer } from "./ChatComposer";
import { EventStreamPanel } from "./EventStreamPanel";
import { MessageTimeline } from "./MessageTimeline";
import { SessionDetailHeader } from "./SessionDetailHeader";

type SessionChatPaneProps = {
  session: SessionSummary;
  detail: SessionDetail;
  events: SessionEventEntry[];
  connectionState: "connecting" | "open" | "closed" | "error";
  isRenaming: boolean;
  isDeleting: boolean;
  isRestoring: boolean;
  isSending: boolean;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onRestore: () => Promise<void>;
  onSend: (content: string, attachments: AttachmentMetadata[]) => Promise<void>;
};

export function SessionChatPane({
  session,
  detail,
  events,
  connectionState,
  isRenaming,
  isDeleting,
  isRestoring,
  isSending,
  onRename,
  onDelete,
  onRestore,
  onSend,
}: SessionChatPaneProps) {
  const isEventPanelOpen = useUiStore((state) => state.isEventPanelOpen);
  const toggleEventPanel = useUiStore((state) => state.toggleEventPanel);

  return (
    <section className="panel workspace-pane">
      <SessionDetailHeader
        session={session}
        subtitle="Send messages, inspect retained transcript entries, and watch typed backend events arrive in real time."
        isRenaming={isRenaming}
        isDeleting={isDeleting}
        isRestoring={isRestoring}
        onRename={onRename}
        onDelete={onDelete}
        onRestore={onRestore}
      />

      {session.deleted_at ? (
        <div className="notice">
          This session is currently soft deleted, so new messages are blocked until it is restored.
        </div>
      ) : null}

      <div className="chat-layout">
        <div className="chat-main">
          <MessageTimeline messages={detail.messages} />
          <ChatComposer
            sessionId={session.id}
            disabled={Boolean(session.deleted_at)}
            isSending={isSending}
            onSend={onSend}
          />
        </div>

        <EventStreamPanel
          events={events}
          connectionState={connectionState}
          isOpen={isEventPanelOpen}
          onToggle={toggleEventPanel}
        />
      </div>
    </section>
  );
}
