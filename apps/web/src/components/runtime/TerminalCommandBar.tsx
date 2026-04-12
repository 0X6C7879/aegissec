import { useState } from "react";
import type { TerminalSession } from "../../types/terminals";

type TerminalCommandBarProps = {
  terminal: TerminalSession | null;
  disabled?: boolean;
  busy?: boolean;
  errorMessage?: string | null;
  onExecuteForeground: (command: string) => Promise<void> | void;
  onExecuteBackground: (command: string) => Promise<void> | void;
  onInterrupt: () => Promise<void> | void;
  onReconnect: () => void;
};

export function TerminalCommandBar({
  terminal,
  disabled = false,
  busy = false,
  errorMessage = null,
  onExecuteForeground,
  onExecuteBackground,
  onInterrupt,
  onReconnect,
}: TerminalCommandBarProps) {
  const [command, setCommand] = useState("");

  const commandDisabled = disabled || busy || terminal === null;

  async function submitCommand(mode: "foreground" | "background"): Promise<void> {
    const normalizedCommand = command.trim();
    if (!normalizedCommand) {
      return;
    }

    try {
      if (mode === "background") {
        await onExecuteBackground(normalizedCommand);
      } else {
        await onExecuteForeground(normalizedCommand);
      }
      setCommand("");
    } catch {
      return;
    }
  }

  return (
    <section className="shell-command-bar" data-testid="shell-command-bar">
      <div className="shell-command-main">
        <input
          type="text"
          className="management-search-input shell-command-input"
          value={command}
          placeholder="输入命令，Enter 前台执行"
          disabled={commandDisabled}
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") {
              return;
            }
            event.preventDefault();
            void submitCommand(event.shiftKey ? "background" : "foreground");
          }}
        />
        <div className="shell-command-actions">
          <button
            type="button"
            className="button button-primary"
            disabled={commandDisabled || command.trim().length === 0}
            onClick={() => void submitCommand("foreground")}
          >
            前台执行
          </button>
          <button
            type="button"
            className="button button-secondary"
            disabled={commandDisabled || command.trim().length === 0}
            onClick={() => void submitCommand("background")}
          >
            后台执行
          </button>
        </div>
      </div>

      <div className="shell-command-secondary">
        <button
          type="button"
          className="button button-secondary"
          disabled={disabled || terminal === null}
          onClick={() => void onInterrupt()}
        >
          Ctrl+C
        </button>
        <button
          type="button"
          className="button button-secondary"
          disabled={disabled || terminal === null}
          onClick={onReconnect}
        >
          重连
        </button>
      </div>

      <div className="shell-command-hint">
        <span>Enter 前台执行，Shift+Enter 后台执行。</span>
        {terminal ? (
          <span>
            当前终端：{terminal.title} / {terminal.workbench_status}
          </span>
        ) : null}
      </div>

      {errorMessage ? <div className="management-error-banner">{errorMessage}</div> : null}
    </section>
  );
}
