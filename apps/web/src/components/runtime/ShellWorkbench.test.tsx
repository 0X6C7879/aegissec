import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TerminalBuffer, TerminalJob, TerminalSession } from "../../types/terminals";
import { ShellWorkbench } from "./ShellWorkbench";

const {
  mockListSessionTerminals,
  mockCreateSessionTerminal,
  mockCloseSessionTerminal,
  mockExecuteTerminalCommand,
  mockGetSessionTerminal,
  mockGetSessionTerminalBuffer,
  mockGetRuntimeStatus,
  mockInterruptTerminal,
  mockListSessionTerminalJobs,
  mockStopSessionTerminalJob,
  mockCleanupSessionTerminalJobs,
} = vi.hoisted(() => ({
  mockListSessionTerminals: vi.fn(),
  mockCreateSessionTerminal: vi.fn(),
  mockCloseSessionTerminal: vi.fn(),
  mockExecuteTerminalCommand: vi.fn(),
  mockGetSessionTerminal: vi.fn(),
  mockGetSessionTerminalBuffer: vi.fn(),
  mockGetRuntimeStatus: vi.fn(),
  mockInterruptTerminal: vi.fn(),
  mockListSessionTerminalJobs: vi.fn(),
  mockStopSessionTerminalJob: vi.fn(),
  mockCleanupSessionTerminalJobs: vi.fn(),
}));

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    listSessionTerminals: mockListSessionTerminals,
    createSessionTerminal: mockCreateSessionTerminal,
    closeSessionTerminal: mockCloseSessionTerminal,
    executeTerminalCommand: mockExecuteTerminalCommand,
    getSessionTerminal: mockGetSessionTerminal,
    getSessionTerminalBuffer: mockGetSessionTerminalBuffer,
    getRuntimeStatus: mockGetRuntimeStatus,
    interruptTerminal: mockInterruptTerminal,
    listSessionTerminalJobs: mockListSessionTerminalJobs,
    stopSessionTerminalJob: mockStopSessionTerminalJob,
    cleanupSessionTerminalJobs: mockCleanupSessionTerminalJobs,
  };
});

vi.mock("./TerminalPane", () => ({
  TerminalPane: ({
    terminal,
    bootstrapBuffer,
  }: {
    terminal: TerminalSession;
    bootstrapBuffer: string;
  }) => <div data-testid="terminal-pane">{`${terminal.title}|${bootstrapBuffer}`}</div>,
}));

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function createTerminal(id: string, title: string, workbenchStatus = "idle"): TerminalSession {
  return {
    id,
    session_id: "session-1",
    title,
    status: "open",
    workbench_status: workbenchStatus,
    shell: "/bin/zsh",
    cwd: "/workspace/sessions/session-1",
    attached: workbenchStatus === "attached",
    active_job_id: workbenchStatus === "attached" ? `job-${id}` : null,
    last_job_id: workbenchStatus === "attached" ? `job-${id}` : null,
    last_job_status: workbenchStatus === "attached" ? "running" : null,
    reattach_deadline: null,
    metadata: {},
    created_at: "2026-04-12T10:00:00.000Z",
    updated_at: "2026-04-12T10:00:00.000Z",
    closed_at: null,
  };
}

function createBuffer(terminalId: string, buffer: string): TerminalBuffer {
  return {
    session_id: "session-1",
    terminal_id: terminalId,
    attached: false,
    job_id: null,
    reattach_deadline: null,
    lines: 400,
    buffer,
  };
}

function createJob(id: string, status: string, stdoutTail = "", terminalId = "term-1"): TerminalJob {
  return {
    id,
    terminal_session_id: terminalId,
    session_id: "session-1",
    status,
    command: "sleep 60",
    finish_reason: status === "completed" ? "exit" : null,
    stdout_tail: stdoutTail,
    stderr_tail: "",
    run_id: null,
    metadata: {},
    created_at: "2026-04-12T10:00:00.000Z",
    updated_at: "2026-04-12T10:00:00.000Z",
  };
}

function renderShellWorkbench(sessionId = "session-1") {
  const queryClient = createQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShellWorkbench sessionId={sessionId} />
    </QueryClientProvider>,
  );
}

describe("ShellWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mockGetRuntimeStatus.mockResolvedValue({
      runtime: { status: "running" },
      recent_runs: [],
      recent_artifacts: [],
    });
    mockInterruptTerminal.mockResolvedValue(undefined);
    mockStopSessionTerminalJob.mockResolvedValue(createJob("job-1", "cancelled"));
    mockCleanupSessionTerminalJobs.mockResolvedValue({ deleted_jobs: 1, kept_jobs: 0 });
  });

  it("creates two terminal tabs and switches between them", async () => {
    const user = userEvent.setup();
    const terminals: TerminalSession[] = [];

    mockListSessionTerminals.mockImplementation(() => Promise.resolve([...terminals]));
    mockCreateSessionTerminal.mockImplementation(async (_sessionId: string, payload: { title?: string }) => {
      const terminal = createTerminal(`term-${terminals.length + 1}`, payload.title ?? `Kali #${terminals.length + 1}`);
      terminals.push(terminal);
      return terminal;
    });
    mockGetSessionTerminal.mockImplementation(async (_sessionId: string, terminalId: string) => {
      const terminal = terminals.find((item) => item.id === terminalId);
      if (!terminal) {
        throw new Error("not found");
      }
      return terminal;
    });
    mockGetSessionTerminalBuffer.mockImplementation(async (_sessionId: string, terminalId: string) =>
      createBuffer(terminalId, `${terminalId}-buffer`),
    );
    mockListSessionTerminalJobs.mockResolvedValue([]);

    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByText("还没有终端，点击“新建终端”开始。")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "新建终端" }));
    await user.click(screen.getByRole("button", { name: "新建终端" }));

    await waitFor(() => {
      expect(screen.getByTestId("shell-tab-term-1")).toBeInTheDocument();
      expect(screen.getByTestId("shell-tab-term-2")).toBeInTheDocument();
    });

    expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");

    await user.click(screen.getByTestId("shell-tab-term-1"));

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #1|term-1-buffer");
    });
  });

  it("executes multiple foreground commands on the same terminal", async () => {
    const user = userEvent.setup();
    const terminals = [createTerminal("term-1", "Kali #1", "attached")];

    mockListSessionTerminals.mockResolvedValue(terminals);
    mockGetSessionTerminal.mockResolvedValue(terminals[0]);
    mockGetSessionTerminalBuffer.mockResolvedValue(createBuffer("term-1", "term-1-buffer"));
    mockListSessionTerminalJobs.mockResolvedValue([]);
    mockExecuteTerminalCommand
      .mockResolvedValueOnce({
        terminal_id: "term-1",
        accepted: true,
        detach: false,
        job_id: "job-term-1",
        status: "running",
      })
      .mockResolvedValueOnce({
        terminal_id: "term-1",
        accepted: true,
        detach: false,
        job_id: "job-term-1",
        status: "running",
      });

    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("shell-command-bar")).toBeInTheDocument();
    });

    const commandInput = screen.getByPlaceholderText("输入命令，Enter 前台执行");

    fireEvent.change(commandInput, { target: { value: "pwd" } });
    await user.click(screen.getByRole("button", { name: "前台执行" }));
    fireEvent.change(commandInput, { target: { value: "whoami" } });
    await user.click(screen.getByRole("button", { name: "前台执行" }));

    await waitFor(() => {
      expect(mockExecuteTerminalCommand).toHaveBeenNthCalledWith(
        1,
        "session-1",
        "term-1",
        expect.objectContaining({ command: "pwd", detach: false }),
      );
    });
    expect(mockExecuteTerminalCommand).toHaveBeenNthCalledWith(
      2,
      "session-1",
      "term-1",
      expect.objectContaining({ command: "whoami", detach: false }),
    );
  });

  it("shows a detached job after completion", async () => {
    const user = userEvent.setup();
    const terminals = [createTerminal("term-1", "Kali #1", "attached")];
    let jobs: TerminalJob[] = [];

    mockListSessionTerminals.mockResolvedValue(terminals);
    mockGetSessionTerminal.mockResolvedValue(terminals[0]);
    mockGetSessionTerminalBuffer.mockResolvedValue(createBuffer("term-1", "term-1-buffer"));
    mockListSessionTerminalJobs.mockImplementation(() => Promise.resolve([...jobs]));
    mockExecuteTerminalCommand.mockImplementation(async () => {
      jobs = [createJob("job-9", "completed", "done", "term-1")];
      return {
        terminal_id: "term-1",
        accepted: true,
        detach: true,
        job_id: "job-9",
        status: "running",
      };
    });

    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("shell-command-bar")).toBeInTheDocument();
    });

    const commandInput = screen.getByPlaceholderText("输入命令，Enter 前台执行");
    fireEvent.change(commandInput, { target: { value: "sleep 1" } });
    await user.click(screen.getByRole("button", { name: "后台执行" }));

    await waitFor(() => {
      expect(mockExecuteTerminalCommand).toHaveBeenCalledWith(
        "session-1",
        "term-1",
        expect.objectContaining({ command: "sleep 1", detach: true }),
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("shell-job-job-9")).toHaveTextContent("completed");
      expect(screen.getByTestId("shell-job-job-9")).toHaveTextContent("done");
    });
  });

  it("restores terminal list, job list, and focused terminal after remount", async () => {
    const terminals = [
      createTerminal("term-1", "Kali #1", "attached"),
      createTerminal("term-2", "Kali #2"),
    ];
    const jobs = [createJob("job-2", "completed", "restored", "term-2")];

    window.localStorage.setItem("aegissec.shell.focus.session-1", "term-2");

    mockListSessionTerminals.mockResolvedValue(terminals);
    mockGetSessionTerminal.mockImplementation(async (_sessionId: string, terminalId: string) => {
      const terminal = terminals.find((item) => item.id === terminalId);
      if (!terminal) {
        throw new Error("not found");
      }
      return terminal;
    });
    mockGetSessionTerminalBuffer.mockImplementation(async (_sessionId: string, terminalId: string) =>
      createBuffer(terminalId, `${terminalId}-buffer`),
    );
    mockListSessionTerminalJobs.mockResolvedValue(jobs);

    const firstRender = renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");
      expect(screen.getByTestId("shell-job-job-2")).toHaveTextContent("restored");
    });

    firstRender.unmount();
    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");
      expect(screen.getByTestId("shell-job-job-2")).toHaveTextContent("restored");
    });

    expect(mockListSessionTerminals).toHaveBeenCalledTimes(2);
    expect(mockListSessionTerminalJobs).toHaveBeenCalledTimes(2);
  });

  it("restores focused terminal with sessionId scope", async () => {
    window.localStorage.setItem("aegissec.shell.focus.session-1", "term-2");
    window.localStorage.setItem("aegissec.shell.focus.session-2", "term-b");

    mockListSessionTerminals.mockImplementation(async (sessionId: string) => {
      if (sessionId === "session-2") {
        return [
          {
            ...createTerminal("term-a", "Alpha"),
            session_id: "session-2",
          },
          {
            ...createTerminal("term-b", "Beta"),
            session_id: "session-2",
          },
        ];
      }
      return [
        createTerminal("term-1", "Kali #1", "attached"),
        createTerminal("term-2", "Kali #2"),
      ];
    });
    mockGetSessionTerminal.mockImplementation(async (sessionId: string, terminalId: string) => {
      const terminals =
        sessionId === "session-2"
          ? [
              {
                ...createTerminal("term-a", "Alpha"),
                session_id: "session-2",
              },
              {
                ...createTerminal("term-b", "Beta"),
                session_id: "session-2",
              },
            ]
          : [
              createTerminal("term-1", "Kali #1", "attached"),
              createTerminal("term-2", "Kali #2"),
            ];
      const terminal = terminals.find((item) => item.id === terminalId);
      if (!terminal) {
        throw new Error("not found");
      }
      return terminal;
    });
    mockGetSessionTerminalBuffer.mockImplementation(async (sessionId: string, terminalId: string) =>
      createBuffer(terminalId, `${sessionId}:${terminalId}`),
    );
    mockListSessionTerminalJobs.mockResolvedValue([]);

    const firstView = renderShellWorkbench("session-1");

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|session-1:term-2");
    });

    firstView.unmount();
    renderShellWorkbench("session-2");

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Beta|session-2:term-b");
    });
  });
});
