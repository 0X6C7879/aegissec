import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { getSessionTerminalStreamUrl } from "../../lib/api";
import type { TerminalClientFrame, TerminalSession, TerminalStreamFrame } from "../../types/terminals";

export type TerminalPaneRuntimeEvent =
  | { type: "socket.open" }
  | { type: "socket.error"; hadReady: boolean; ended: boolean }
  | { type: "socket.close"; hadReady: boolean; ended: boolean }
  | { type: "ready"; reattached: boolean }
  | { type: "error"; message: string }
  | { type: "exit"; reason: string; exitCode: number | null }
  | { type: "closed"; reason: string };

type TerminalPaneProps = {
  sessionId: string;
  terminal: TerminalSession;
  bootstrapBuffer: string;
  reconnectKey?: number;
  onBufferAppend: (terminalId: string, content: string) => void;
  onConnectionStateChange: (
    terminalId: string,
    state: "connecting" | "open" | "closed" | "error",
  ) => void;
  onRuntimeEvent?: (terminalId: string, event: TerminalPaneRuntimeEvent) => void;
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
  bootstrapBuffer,
  reconnectKey = 0,
  onBufferAppend,
  onConnectionStateChange,
  onRuntimeEvent,
}: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const bootstrapBufferRef = useRef(bootstrapBuffer);
  const onBufferAppendRef = useRef(onBufferAppend);
  const onConnectionStateChangeRef = useRef(onConnectionStateChange);
  const onRuntimeEventRef = useRef(onRuntimeEvent);
  const readyStateRef = useRef(false);
  const endedStateRef = useRef(false);

  useEffect(() => {
    bootstrapBufferRef.current = bootstrapBuffer;
  }, [bootstrapBuffer]);

  useEffect(() => {
    onBufferAppendRef.current = onBufferAppend;
  }, [onBufferAppend]);

  useEffect(() => {
    onConnectionStateChangeRef.current = onConnectionStateChange;
  }, [onConnectionStateChange]);

  useEffect(() => {
    onRuntimeEventRef.current = onRuntimeEvent;
  }, [onRuntimeEvent]);

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
      scrollback: 4000,
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
    if (bootstrapBufferRef.current) {
      term.write(bootstrapBufferRef.current);
    }
    readyStateRef.current = false;
    endedStateRef.current = false;

    onConnectionStateChangeRef.current(terminal.id, "connecting");
    const socket = new WebSocket(
      `${getSessionTerminalStreamUrl(sessionId, terminal.id)}?cols=${term.cols}&rows=${term.rows}`,
    );

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
      onConnectionStateChangeRef.current(terminal.id, "open");
      onRuntimeEventRef.current?.(terminal.id, { type: "socket.open" });
      term.focus();
    });
    socket.addEventListener("close", () => {
      onConnectionStateChangeRef.current(terminal.id, "closed");
      onRuntimeEventRef.current?.(terminal.id, {
        type: "socket.close",
        hadReady: readyStateRef.current,
        ended: endedStateRef.current,
      });
    });
    socket.addEventListener("error", () => {
      onConnectionStateChangeRef.current(terminal.id, "error");
      onRuntimeEventRef.current?.(terminal.id, {
        type: "socket.error",
        hadReady: readyStateRef.current,
        ended: endedStateRef.current,
      });
    });
    socket.addEventListener("message", (event) => {
      const frame = JSON.parse(event.data as string) as TerminalStreamFrame;
      switch (frame.type) {
        case "ready":
          readyStateRef.current = true;
          endedStateRef.current = false;
          onRuntimeEventRef.current?.(terminal.id, {
            type: "ready",
            reattached: frame.reattached,
          });
          break;
        case "output": {
          term.write(frame.data);
          onBufferAppendRef.current(terminal.id, frame.data);
          break;
        }
        case "error": {
          const line = `\r\n[error] ${frame.message}\r\n`;
          term.write(line);
          onBufferAppendRef.current(terminal.id, line);
          onRuntimeEventRef.current?.(terminal.id, { type: "error", message: frame.message });
          break;
        }
        case "exit": {
          const line = `\r\n[exit:${frame.reason}]\r\n`;
          term.write(line);
          onBufferAppendRef.current(terminal.id, line);
          endedStateRef.current = true;
          onRuntimeEventRef.current?.(terminal.id, {
            type: "exit",
            reason: frame.reason,
            exitCode: frame.exit_code,
          });
          break;
        }
        case "closed": {
          const line = `\r\n[closed:${frame.reason}]\r\n`;
          term.write(line);
          onBufferAppendRef.current(terminal.id, line);
          endedStateRef.current = true;
          onRuntimeEventRef.current?.(terminal.id, { type: "closed", reason: frame.reason });
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
      term.dispose();
    };
  }, [reconnectKey, sessionId, terminal.id]);

  return <div ref={containerRef} className="shell-terminal-pane" data-testid="shell-terminal-pane" />;
}
