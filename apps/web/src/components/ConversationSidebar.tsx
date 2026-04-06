import { useMemo, useState } from "react";
import type { SessionSummary } from "../types/sessions";

type ConversationSidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  collapsed: boolean;
  isCreating: boolean;
  extraSections?: React.ReactNode;
  onCreate: () => Promise<void>;
  onToggleCollapsed: () => void;
  onSelect: (sessionId: string) => void;
  onRename: (sessionId: string) => Promise<void>;
  onArchive: (sessionId: string) => Promise<void>;
  onRestore: (sessionId: string) => Promise<void>;
};

function getSessionTitle(title: string): string {
  return title === "New Session" ? "新对话" : title;
}

export function ConversationSidebar({
  sessions,
  activeSessionId,
  collapsed,
  isCreating,
  extraSections,
  onCreate,
  onToggleCollapsed,
  onSelect,
  onRename,
  onArchive,
  onRestore,
}: ConversationSidebarProps) {
  const [searchValue, setSearchValue] = useState("");
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);

  const visibleSessions = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    if (!keyword) {
      return sessions;
    }

    return sessions.filter((session) =>
      getSessionTitle(session.title).toLowerCase().includes(keyword),
    );
  }, [searchValue, sessions]);

  async function handleRename(sessionId: string): Promise<void> {
    setMenuSessionId(null);
    await onRename(sessionId);
  }

  async function handleArchive(sessionId: string): Promise<void> {
    setMenuSessionId(null);
    await onArchive(sessionId);
  }

  async function handleRestore(sessionId: string): Promise<void> {
    setMenuSessionId(null);
    await onRestore(sessionId);
  }

  return (
    <aside
      className={`conversation-sidebar-shell${collapsed ? " conversation-sidebar-shell-collapsed" : ""}`}
    >
      <section className="conversation-sidebar">
        <div className="conversation-sidebar-actions">
          <button
            className="conversation-sidebar-toggle"
            type="button"
            aria-pressed={collapsed}
            aria-label={collapsed ? "展开近期对话面板" : "收起近期对话面板"}
            title={collapsed ? "展开近期对话" : "收起近期对话"}
            onClick={onToggleCollapsed}
          >
            <span
              className={`conversation-sidebar-toggle-icon${collapsed ? " conversation-sidebar-toggle-icon-collapsed" : ""}`}
              aria-hidden="true"
            >
              <svg
                viewBox="0 0 16 16"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                aria-hidden="true"
                focusable="false"
              >
                <path
                  d={collapsed ? "M5 3.5v9M8 5.5l3 2.5-3 2.5" : "M11 3.5v9M8 5.5L5 8l3 2.5"}
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
          </button>

          <button
            className="conversation-new-button"
            type="button"
            disabled={isCreating}
            aria-label={isCreating ? "创建对话中" : "新建对话"}
            title={isCreating ? "创建中" : "新建对话"}
            onClick={() => void onCreate()}
          >
            <span className="conversation-new-button-icon" aria-hidden="true">
              +
            </span>
            <span className="conversation-new-button-label">
              {isCreating ? "创建中" : "新对话"}
            </span>
          </button>
        </div>

        <div className="conversation-sidebar-body">
          <div className="conversation-sidebar-section">
            <input
              className="conversation-search-input"
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="搜索"
            />
          </div>

          <div className="conversation-list-shell">
            {visibleSessions.length === 0 ? (
              <div className="conversation-empty-list">
                <p>{searchValue.trim() ? "没有找到对话" : "还没有对话"}</p>
              </div>
            ) : (
              <ul className="conversation-list">
                {visibleSessions.map((session) => {
                  const isActive = session.id === activeSessionId;
                  const isMenuOpen = menuSessionId === session.id;

                  return (
                    <li key={session.id} className="conversation-item-shell">
                      <div
                        className={`conversation-item-card${isActive ? " conversation-item-card-active" : ""}`}
                      >
                        <button
                          className="conversation-item-main"
                          type="button"
                          onClick={() => {
                            setMenuSessionId(null);
                            onSelect(session.id);
                          }}
                        >
                          <div className="conversation-link-row">
                            <span
                              className={`conversation-link-dot status-${session.status}`}
                              aria-hidden="true"
                            />
                            <span className="conversation-link-title">
                              {getSessionTitle(session.title)}
                            </span>
                          </div>
                          {session.deleted_at ? (
                            <div className="conversation-link-row conversation-link-meta-row">
                              <span className="conversation-link-flag">已归档</span>
                            </div>
                          ) : null}
                        </button>

                        <div className="conversation-item-actions">
                          <button
                            className="conversation-item-menu"
                            type="button"
                            aria-label="打开对话操作"
                            onClick={(event) => {
                              event.stopPropagation();
                              setMenuSessionId((currentValue) =>
                                currentValue === session.id ? null : session.id,
                              );
                            }}
                          >
                            ···
                          </button>

                          {isMenuOpen ? (
                            <div className="conversation-item-dropdown">
                              <button type="button" onClick={() => void handleRename(session.id)}>
                                重命名
                              </button>
                              {session.deleted_at ? (
                                <button
                                  type="button"
                                  onClick={() => void handleRestore(session.id)}
                                >
                                  恢复对话
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => void handleArchive(session.id)}
                                >
                                  归档对话
                                </button>
                              )}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {extraSections ? (
            <div className="conversation-sidebar-extra-sections">{extraSections}</div>
          ) : null}
        </div>
      </section>
    </aside>
  );
}
