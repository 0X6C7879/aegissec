import { formatDateTime } from "../lib/format";
import type { SessionEventEntry } from "../types/sessions";

type EventStreamPanelProps = {
  events: SessionEventEntry[];
  connectionState: "connecting" | "open" | "closed" | "error";
  isOpen: boolean;
  onToggle: () => void;
};

export function EventStreamPanel({
  events,
  connectionState,
  isOpen,
  onToggle,
}: EventStreamPanelProps) {
  return (
    <aside className={`event-panel${isOpen ? "" : " event-panel-collapsed"}`}>
      <div className="panel-header">
        <div className="panel-header-copy">
          <p className="eyebrow">Live events</p>
          <h2 className="panel-title">Websocket stream</h2>
        </div>
        <div className="action-row">
          <span className={`connection-pill connection-${connectionState}`}>
            <span className="status" aria-hidden="true" />
            <span className="connection-label">{connectionState}</span>
          </span>
          <button className="inline-button" type="button" onClick={onToggle}>
            {isOpen ? "Collapse" : "Expand"}
          </button>
        </div>
      </div>

      {isOpen ? (
        events.length === 0 ? (
          <p className="event-empty">
            Waiting for the first backend event. Session and message updates will appear here live.
          </p>
        ) : (
          <ul className="event-list">
            {events.map((event) => (
              <li key={event.id} className="event-item">
                <div className="event-item-header">
                  <span className="event-type">{event.type}</span>
                  <span className="timestamp-label">{formatDateTime(event.createdAt)}</span>
                </div>
                <p className="event-summary">{event.summary}</p>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </aside>
  );
}
