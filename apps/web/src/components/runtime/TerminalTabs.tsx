import type { TerminalSession } from "../../types/terminals";

type TerminalTabsProps = {
  terminals: TerminalSession[];
  focusedTerminalId: string | null;
  disabled?: boolean;
  onSelect: (terminalId: string) => void;
  onClose: (terminalId: string) => void;
  onCreate: () => void;
};

export function TerminalTabs({
  terminals,
  focusedTerminalId,
  disabled = false,
  onSelect,
  onClose,
  onCreate,
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
                <span className="shell-workbench-tab-status">{terminal.status}</span>
              </button>
              <button
                type="button"
                className="shell-workbench-tab-close"
                aria-label={`关闭 ${terminal.title}`}
                disabled={disabled}
                onClick={(event) => {
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
      <button
        type="button"
        className="button button-secondary shell-workbench-create"
        onClick={onCreate}
        disabled={disabled}
      >
        新建终端
      </button>
    </div>
  );
}
