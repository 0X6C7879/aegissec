import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TerminalSession } from "../../types/terminals";
import { TerminalPane } from "./TerminalPane";

const terminalMocks = vi.hoisted(() => ({
  write: vi.fn(),
  open: vi.fn(),
  focus: vi.fn(),
  dispose: vi.fn(),
  loadAddon: vi.fn(),
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;

    open = terminalMocks.open;
    focus = terminalMocks.focus;
    dispose = terminalMocks.dispose;
    loadAddon = terminalMocks.loadAddon;
    write = terminalMocks.write;

    onData(callback: (data: string) => void) {
      this.onDataCallback = callback;
      return { dispose: vi.fn() };
    }

    onResize(callback: (size: { cols: number; rows: number }) => void) {
      this.onResizeCallback = callback;
      return { dispose: vi.fn() };
    }

    onDataCallback?: (data: string) => void;
    onResizeCallback?: (size: { cols: number; rows: number }) => void;
  },
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit = vi.fn();
  },
}));

class MockResizeObserver {
  observe = vi.fn();
  disconnect = vi.fn();
}

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  static OPEN = 1;
  static CLOSED = 3;
  static CONNECTING = 0;

  url: string;
  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];
  private listeners = new Map<string, Array<(event?: Event | MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, callback: (event?: Event | MessageEvent) => void) {
    const callbacks = this.listeners.get(type) ?? [];
    callbacks.push(callback);
    this.listeners.set(type, callbacks);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
  }

  emit(type: string, payload?: unknown) {
    if (type === "open") {
      this.readyState = MockWebSocket.OPEN;
    }
    const callbacks = this.listeners.get(type) ?? [];
    const event =
      type === "message"
        ? ({ data: payload } as MessageEvent)
        : ({} as Event);
    callbacks.forEach((callback) => callback(event));
  }
}

function createTerminal(id: string): TerminalSession {
  return {
    id,
    session_id: "session-1",
    title: `Terminal ${id}`,
    status: "open",
    workbench_status: "attached",
    shell: "/bin/zsh",
    cwd: "/workspace/sessions/session-1",
    attached: true,
    active_job_id: "job-1",
    last_job_id: "job-1",
    last_job_status: "running",
    reattach_deadline: null,
    metadata: {},
    created_at: "2026-04-12T10:00:00.000Z",
    updated_at: "2026-04-12T10:00:00.000Z",
    closed_at: null,
  };
}

describe("TerminalPane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  it("bootstraps the server buffer before streaming live output", async () => {
    const onBufferAppend = vi.fn();
    const onConnectionStateChange = vi.fn();

    render(
      <TerminalPane
        sessionId="session-1"
        terminal={createTerminal("term-1")}
        bootstrapBuffer={"pwd\n"}
        onBufferAppend={onBufferAppend}
        onConnectionStateChange={onConnectionStateChange}
      />,
    );

    await waitFor(() => {
      expect(terminalMocks.write).toHaveBeenCalledWith("pwd\n");
    });

    const socket = MockWebSocket.instances[0];
    socket.emit("open");
    socket.emit("message", JSON.stringify({ type: "output", data: "whoami\n" }));

    expect(onConnectionStateChange).toHaveBeenCalledWith("term-1", "connecting");
    expect(onConnectionStateChange).toHaveBeenCalledWith("term-1", "open");
    expect(terminalMocks.write).toHaveBeenCalledWith("whoami\n");
    expect(onBufferAppend).toHaveBeenCalledWith("term-1", "whoami\n");
  });

  it("reports websocket and terminal lifecycle events", async () => {
    const onConnectionStateChange = vi.fn();
    const onRuntimeEvent = vi.fn();

    render(
      <TerminalPane
        sessionId="session-1"
        terminal={createTerminal("term-2")}
        bootstrapBuffer=""
        onBufferAppend={vi.fn()}
        onConnectionStateChange={onConnectionStateChange}
        onRuntimeEvent={onRuntimeEvent}
      />,
    );

    const socket = MockWebSocket.instances[0];
    socket.emit("open");
    socket.emit("message", JSON.stringify({ type: "ready", session_id: "session-1", terminal_id: "term-2", job_id: "job-1", reattached: false }));
    socket.emit("message", JSON.stringify({ type: "exit", exit_code: 0, reason: "exit" }));
    socket.emit("message", JSON.stringify({ type: "closed", reason: "exit" }));
    socket.emit("error");
    socket.emit("close");

    await waitFor(() => {
      expect(onRuntimeEvent).toHaveBeenCalledWith("term-2", {
        type: "ready",
        reattached: false,
      });
    });
    expect(onRuntimeEvent).toHaveBeenCalledWith("term-2", {
      type: "exit",
      reason: "exit",
      exitCode: 0,
    });
    expect(onRuntimeEvent).toHaveBeenCalledWith("term-2", {
      type: "closed",
      reason: "exit",
    });
    expect(onRuntimeEvent).toHaveBeenCalledWith("term-2", {
      type: "socket.error",
      hadReady: true,
      ended: true,
    });
    expect(onRuntimeEvent).toHaveBeenCalledWith("term-2", {
      type: "socket.close",
      hadReady: true,
      ended: true,
    });
    expect(onConnectionStateChange).toHaveBeenCalledWith("term-2", "error");
    expect(onConnectionStateChange).toHaveBeenCalledWith("term-2", "closed");
  });
});
