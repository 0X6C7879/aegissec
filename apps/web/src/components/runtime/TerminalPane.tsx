import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { getSessionTerminalStreamUrl } from "../../lib/api";
import type { TerminalClientFrame, TerminalSession, TerminalStreamFrame } from "../../types/terminals";

type TerminalPaneProps = {
  sessionId: string;
  terminal: TerminalSession;
  initialBuffer: string;
  onBufferAppend: (terminalId: string, content: string) => void;
  onConnectionStateChange: (
    terminalId: string,
    state: "connecting" | "open" | "closed" | "error",
  ) => void;
};

function sendFrame(socket: WebSocket | null, frame: TerminalClientFrame): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify(frame));
}

export function TerminalPane({
  sessionId,
  terminal,
  initialBuffer,
  onBufferAppend,
  onConnectionStateChange,
}: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }

    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 13,
      scrollback: 2000,
      theme: {
        background: "#07111d",
        foreground: "#d8edf7",
        cursor: "#00d9ff",
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();
    if (initialBuffer) {
      term.write(initialBuffer);
    }

    onConnectionStateChange(terminal.id, "connecting");
    const socket = new WebSocket(
      `${getSessionTerminalStreamUrl(sessionId, terminal.id)}?cols=${term.cols}&rows=${term.rows}`,
    );
    socketRef.current = socket;

    const disposeData = term.onData((data) => {
      sendFrame(socket, { type: "input", data });
    });
    const disposeResize = term.onResize(({ cols, rows }) => {
      sendFrame(socket, { type: "resize", cols, rows });
    });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      sendFrame(socket, { type: "resize", cols: term.cols, rows: term.rows });
    });
    resizeObserver.observe(container);

    socket.addEventListener("open", () => {
      onConnectionStateChange(terminal.id, "open");
      term.focus();
    });
    socket.addEventListener("close", () => {
      onConnectionStateChange(terminal.id, "closed");
    });
    socket.addEventListener("error", () => {
      onConnectionStateChange(terminal.id, "error");
    });
    socket.addEventListener("message", (event) => {
      const frame = JSON.parse(event.data as string) as TerminalStreamFrame;
      switch (frame.type) {
        case "output": {
          term.write(frame.data);
          onBufferAppend(terminal.id, frame.data);
          break;
        }
        case "error": {
          const line = `\r\n[error] ${frame.message}\r\n`;
          term.write(line);
          onBufferAppend(terminal.id, line);
          break;
        }
        case "exit": {
          const line = `\r\n[exit:${frame.reason}]\r\n`;
          term.write(line);
          onBufferAppend(terminal.id, line);
          break;
        }
        case "closed": {
          const line = `\r\n[closed:${frame.reason}]\r\n`;
          term.write(line);
          onBufferAppend(terminal.id, line);
          break;
        }
        default:
          break;
      }
    });

    return () => {
      resizeObserver.disconnect();
      disposeResize.dispose();
      disposeData.dispose();
      socket.close();
      socketRef.current = null;
      term.dispose();
    };
  }, [initialBuffer, onBufferAppend, onConnectionStateChange, sessionId, terminal.id]);

  return <div ref={containerRef} className="shell-terminal-pane" data-testid="shell-terminal-pane" />;
}
