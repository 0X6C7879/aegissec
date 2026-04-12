import type { TerminalSession } from "../../types/terminals";

type TerminalTabsProps = {
  terminals: TerminalSession[];
  focusedTerminalId: string | null;
  disabled?: boolean;
  onSelect: (terminalId: string) => void;
  onClose: (terminalId: string) => void;
  onCreate: () => void;
  onCloseAll?: () => void;
};

export function TerminalTabs({
  terminals,
  focusedTerminalId,
  disabled = false,
  onSelect,
  onClose,
  onCreate,
  onCloseAll,
}: TerminalTabsProps) {
  return (
    <div className="shell-workbench-tabs" data-testid="shell-workbench-tabs">
      <div className="shell-workbench-tab-list">
        {terminals.map((terminal) => {
          const isActive = terminal.id === focusedTerminalId;
          return (
            <div
              key={terminal.id}
              className={`shell-workbench-tab-shell${isActive ? " shell-workbench-tab-shell-active" : ""}`}
            >
              <button
                type="button"
                className={`shell-workbench-tab${isActive ? " shell-workbench-tab-active" : ""}`}
                data-testid={`shell-tab-${terminal.id}`}
                onClick={() => onSelect(terminal.id)}
              >
                <span className="shell-workbench-tab-title">{terminal.title}</span>
                <span className="shell-workbench-tab-status">{terminal.workbench_status}</span>
              </button>
              <button
                type="button"
                className="shell-workbench-tab-close"
                aria-label={`关闭 ${terminal.title}`}
                title={`关闭 ${terminal.title}`}
                disabled={disabled}
                onMouseDown={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                }}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onClose(terminal.id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
      <div className="shell-workbench-tab-actions">
        <button
          type="button"
          className="button button-secondary shell-workbench-create"
          aria-label="新建终端"
          title="新建终端"
          onClick={onCreate}
          disabled={disabled}
        >
          +
        </button>
        <button
          type="button"
          className="button button-secondary shell-workbench-close-all"
          aria-label="关闭全部终端"
          title="关闭全部终端"
          onClick={onCloseAll}
          disabled={disabled || terminals.length === 0 || typeof onCloseAll !== "function"}
        >
          X
        </button>
      </div>
    </div>
  );
}
