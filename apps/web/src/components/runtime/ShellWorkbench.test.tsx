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
  mockGetSessionTerminalBuffer,
  mockInterruptTerminal,
  mockListSessionTerminalJobs,
  mockStopSessionTerminalJob,
  mockCleanupSessionTerminalJobs,
} = vi.hoisted(() => ({
  mockListSessionTerminals: vi.fn(),
  mockCreateSessionTerminal: vi.fn(),
  mockCloseSessionTerminal: vi.fn(),
  mockExecuteTerminalCommand: vi.fn(),
  mockGetSessionTerminalBuffer: vi.fn(),
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
    getSessionTerminalBuffer: mockGetSessionTerminalBuffer,
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
    last_job_id: null,
    last_job_status: null,
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

function createJob(id: string, status: string, stdoutTail = ""): TerminalJob {
  return {
    id,
    terminal_session_id: "term-1",
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

function renderShellWorkbench() {
  const queryClient = createQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShellWorkbench sessionId="session-1" />
    </QueryClientProvider>,
  );
}

describe("ShellWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mockListSessionTerminals.mockResolvedValue([
      createTerminal("term-1", "Kali #1", "attached"),
      createTerminal("term-2", "Kali #2"),
    ]);
    mockCreateSessionTerminal.mockResolvedValue(createTerminal("term-3", "Kali #3"));
    mockCloseSessionTerminal.mockResolvedValue({
      ...createTerminal("term-1", "Kali #1", "cancelled"),
      status: "closed",
      closed_at: "2026-04-12T10:05:00.000Z",
    });
    mockExecuteTerminalCommand.mockResolvedValue({
      terminal_id: "term-1",
      accepted: true,
      detach: false,
      job_id: "job-term-1",
      status: "running",
    });
    mockGetSessionTerminalBuffer.mockImplementation((sessionId: string, terminalId: string) => {
      expect(sessionId).toBe("session-1");
      return Promise.resolve(createBuffer(terminalId, `${terminalId}-buffer`));
    });
    mockInterruptTerminal.mockResolvedValue(undefined);
    mockListSessionTerminalJobs.mockResolvedValue([createJob("job-1", "running", "tick")]);
    mockStopSessionTerminalJob.mockResolvedValue(createJob("job-1", "cancelled"));
    mockCleanupSessionTerminalJobs.mockResolvedValue({ deleted_jobs: 1, kept_jobs: 0 });
  });

  it("renders terminal tabs and switches the focused terminal", async () => {
    const user = userEvent.setup();
    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #1|term-1-buffer");
    });

    await user.click(screen.getByTestId("shell-tab-term-2"));

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");
    });
    expect(mockGetSessionTerminalBuffer).toHaveBeenCalledWith(
      "session-1",
      "term-2",
      { lines: 400 },
      expect.anything(),
    );
  });

  it("executes multiple foreground commands on the same terminal", async () => {
    const user = userEvent.setup();
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

  it("starts a background job and refreshes the completed status", async () => {
    const user = userEvent.setup();
    mockListSessionTerminalJobs
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createJob("job-9", "completed", "done")]);
    mockExecuteTerminalCommand.mockResolvedValue({
      terminal_id: "term-1",
      accepted: true,
      detach: true,
      job_id: "job-9",
      status: "running",
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

  it("restores focused terminal from localStorage after remount", async () => {
    window.localStorage.setItem("aegissec.shell.focus.session-1", "term-2");

    const firstRender = renderShellWorkbench();
    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");
    });
    firstRender.unmount();

    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("terminal-pane")).toHaveTextContent("Kali #2|term-2-buffer");
    });
    expect(mockGetSessionTerminalBuffer).toHaveBeenCalledWith(
      "session-1",
      "term-2",
      { lines: 400 },
      expect.anything(),
    );
  });
});
