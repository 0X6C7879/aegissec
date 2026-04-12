import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TerminalJob, TerminalSession } from "../../types/terminals";
import { ShellWorkbench } from "./ShellWorkbench";

const {
  mockListSessionTerminals,
  mockCreateSessionTerminal,
  mockCloseSessionTerminal,
  mockListSessionTerminalJobs,
  mockStopSessionTerminalJob,
  mockCleanupSessionTerminalJobs,
} = vi.hoisted(() => ({
  mockListSessionTerminals: vi.fn(),
  mockCreateSessionTerminal: vi.fn(),
  mockCloseSessionTerminal: vi.fn(),
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
    listSessionTerminalJobs: mockListSessionTerminalJobs,
    stopSessionTerminalJob: mockStopSessionTerminalJob,
    cleanupSessionTerminalJobs: mockCleanupSessionTerminalJobs,
  };
});

vi.mock("./TerminalPane", () => ({
  TerminalPane: ({ terminal }: { terminal: TerminalSession }) => (
    <div data-testid="terminal-pane">{terminal.title}</div>
  ),
}));

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function createTerminal(id: string, title: string): TerminalSession {
  return {
    id,
    session_id: "session-1",
    title,
    status: "open",
    shell: "/bin/zsh",
    cwd: "/workspace/sessions/session-1",
    metadata: {},
    created_at: "2026-04-12T10:00:00.000Z",
    updated_at: "2026-04-12T10:00:00.000Z",
    closed_at: null,
  };
}

function createJob(id: string, status: string): TerminalJob {
  return {
    id,
    terminal_session_id: "term-1",
    session_id: "session-1",
    status,
    command: "sleep 60",
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
    mockListSessionTerminals.mockResolvedValue([
      createTerminal("term-1", "Kali #1"),
      createTerminal("term-2", "Kali #2"),
    ]);
    mockCreateSessionTerminal.mockResolvedValue(createTerminal("term-3", "Kali #3"));
    mockCloseSessionTerminal.mockResolvedValue(createTerminal("term-1", "Kali #1"));
    mockListSessionTerminalJobs.mockResolvedValue([createJob("job-1", "running")]);
    mockStopSessionTerminalJob.mockResolvedValue(createJob("job-1", "cancelled"));
    mockCleanupSessionTerminalJobs.mockResolvedValue({ deleted_jobs: 1, kept_jobs: 0 });
  });

  it("renders terminal tabs and the focused terminal pane", async () => {
    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("shell-tab-term-1")).toBeInTheDocument();
    });

    expect(screen.getByTestId("shell-tab-term-2")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-pane").textContent).toContain("Kali #1");
  });

  it("creates a new terminal and switches focus to it", async () => {
    const user = userEvent.setup();
    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("shell-workbench")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "新建终端" }));

    await waitFor(() => {
      expect(mockCreateSessionTerminal).toHaveBeenCalledWith(
        "session-1",
        expect.objectContaining({ cwd: "/workspace/sessions/session-1" }),
      );
    });
  });

  it("stops background jobs and triggers cleanup", async () => {
    const user = userEvent.setup();
    renderShellWorkbench();

    await waitFor(() => {
      expect(screen.getByTestId("shell-job-job-1")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "停止" }));
    await waitFor(() => {
      expect(mockStopSessionTerminalJob).toHaveBeenCalledWith("session-1", "job-1");
    });

    await user.click(screen.getByRole("button", { name: "清理完成项" }));
    await waitFor(() => {
      expect(mockCleanupSessionTerminalJobs).toHaveBeenCalledWith("session-1");
    });
  });
});
