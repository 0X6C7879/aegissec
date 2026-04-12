import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cleanupSessionTerminalJobs,
  closeSessionTerminal,
  createSessionTerminal,
  executeTerminalCommand,
  getSessionTerminalBuffer,
  interruptTerminal,
  isApiError,
  listSessionTerminalJobs,
  listSessionTerminals,
  stopSessionTerminalJob,
} from "../../lib/api";
import { useUiStore } from "../../store/uiStore";
import type { TerminalJob, TerminalSession } from "../../types/terminals";
import { BackgroundJobsPanel } from "./BackgroundJobsPanel";
import { TerminalCommandBar } from "./TerminalCommandBar";
import { TerminalPane } from "./TerminalPane";
import { TerminalTabs } from "./TerminalTabs";

type ShellWorkbenchProps = {
  sessionId: string;
  disabled?: boolean;
};

type ConnectionState = "connecting" | "open" | "closed" | "error";
const EMPTY_SESSION_EVENTS: ReturnType<typeof useUiStore.getState>["eventsBySession"][string] = [];
const EMPTY_TERMINALS: TerminalSession[] = [];
const EMPTY_JOBS: TerminalJob[] = [];

const TERMINALS_QUERY_KEY = (sessionId: string) => ["session", sessionId, "terminals"] as const;
const TERMINAL_JOBS_QUERY_KEY = (sessionId: string) => ["session", sessionId, "terminal-jobs"] as const;
const TERMINAL_BUFFER_QUERY_KEY = (sessionId: string, terminalId: string | null, reconnectKey: number) =>
  ["session", sessionId, "terminal-buffer", terminalId, reconnectKey] as const;
const FOCUSED_TERMINAL_STORAGE_KEY = (sessionId: string) => `aegissec.shell.focus.${sessionId}`;

function readStoredFocusedTerminalId(sessionId: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(FOCUSED_TERMINAL_STORAGE_KEY(sessionId));
}

function writeStoredFocusedTerminalId(sessionId: string, terminalId: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  if (!terminalId) {
    window.localStorage.removeItem(FOCUSED_TERMINAL_STORAGE_KEY(sessionId));
    return;
  }
  window.localStorage.setItem(FOCUSED_TERMINAL_STORAGE_KEY(sessionId), terminalId);
}

function pickFocusedTerminal(terminals: TerminalSession[], storedTerminalId: string | null): string | null {
  if (storedTerminalId && terminals.some((terminal) => terminal.id === storedTerminalId)) {
    return storedTerminalId;
  }
  return terminals.find((terminal) => terminal.status !== "closed")?.id ?? terminals[0]?.id ?? null;
}

function readErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Shell 工作台操作失败。";
}

export function ShellWorkbench({ sessionId, disabled = false }: ShellWorkbenchProps) {
  const queryClient = useQueryClient();
  const sessionEventsState = useUiStore((state) => state.eventsBySession[sessionId]);
  const sessionEvents = sessionEventsState ?? EMPTY_SESSION_EVENTS;
  const [focusedTerminalId, setFocusedTerminalId] = useState<string | null>(() =>
    readStoredFocusedTerminalId(sessionId),
  );
  const [buffersByTerminal, setBuffersByTerminal] = useState<Record<string, string>>({});
  const [connectionStateByTerminal, setConnectionStateByTerminal] = useState<
    Record<string, ConnectionState>
  >({});
  const [terminalErrors, setTerminalErrors] = useState<Record<string, string | null>>({});
  const [reconnectKeysByTerminal, setReconnectKeysByTerminal] = useState<Record<string, number>>({});
  const lastHandledTerminalEventIdRef = useRef<string | null>(null);

  const terminalsQuery = useQuery({
    queryKey: TERMINALS_QUERY_KEY(sessionId),
    queryFn: ({ signal }) => listSessionTerminals(sessionId, signal),
    refetchInterval: 5000,
    placeholderData: (previous) => previous,
  });

  const jobsQuery = useQuery({
    queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId),
    queryFn: ({ signal }) => listSessionTerminalJobs(sessionId, signal),
    refetchInterval: 2000,
    placeholderData: (previous) => previous,
  });

  const reconnectKey = focusedTerminalId ? reconnectKeysByTerminal[focusedTerminalId] ?? 0 : 0;
  const focusedBufferQuery = useQuery({
    queryKey: TERMINAL_BUFFER_QUERY_KEY(sessionId, focusedTerminalId, reconnectKey),
    queryFn: ({ signal }) =>
      getSessionTerminalBuffer(sessionId, focusedTerminalId as string, { lines: 400 }, signal),
    enabled: focusedTerminalId !== null,
    placeholderData: (previous) => previous,
  });

  const createTerminalMutation = useMutation({
    mutationFn: () =>
      createSessionTerminal(sessionId, {
        title: `Kali #${(terminalsQuery.data?.length ?? 0) + 1}`,
        cwd: `/workspace/sessions/${sessionId}`,
      }),
    onSuccess: async (terminal) => {
      setFocusedTerminalId(terminal.id);
      setTerminalErrors((current) => ({ ...current, [terminal.id]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    },
  });

  const closeTerminalMutation = useMutation({
    mutationFn: (terminalId: string) => closeSessionTerminal(sessionId, terminalId),
    onSuccess: async (terminal) => {
      setTerminalErrors((current) => ({ ...current, [terminal.id]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const executeTerminalMutation = useMutation({
    mutationFn: ({
      terminalId,
      command,
      detach,
    }: {
      terminalId: string;
      command: string;
      detach: boolean;
    }) => executeTerminalCommand(sessionId, terminalId, { command, detach }),
    onSuccess: async (_, variables) => {
      setTerminalErrors((current) => ({ ...current, [variables.terminalId]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const interruptMutation = useMutation({
    mutationFn: (terminalId: string) => interruptTerminal(sessionId, terminalId),
    onSuccess: async (_, terminalId) => {
      setTerminalErrors((current) => ({ ...current, [terminalId]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    },
  });

  const stopJobMutation = useMutation({
    mutationFn: (jobId: string) => stopSessionTerminalJob(sessionId, jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    },
  });

  const cleanupJobsMutation = useMutation({
    mutationFn: () => cleanupSessionTerminalJobs(sessionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const terminals = terminalsQuery.data ?? EMPTY_TERMINALS;
  const jobs = jobsQuery.data ?? EMPTY_JOBS;
  const focusedTerminal =
    terminals.find((terminal) => terminal.id === focusedTerminalId) ?? null;

  useEffect(() => {
    if (terminalsQuery.isLoading && terminals.length === 0) {
      return;
    }
    if (terminals.length === 0) {
      if (focusedTerminalId !== null) {
        setFocusedTerminalId(null);
      }
      return;
    }
    const nextFocusedTerminalId = pickFocusedTerminal(
      terminals,
      focusedTerminalId ?? readStoredFocusedTerminalId(sessionId),
    );
    if (nextFocusedTerminalId !== focusedTerminalId) {
      setFocusedTerminalId(nextFocusedTerminalId);
    }
  }, [focusedTerminalId, sessionId, terminals, terminalsQuery.isLoading]);

  useEffect(() => {
    writeStoredFocusedTerminalId(sessionId, focusedTerminalId);
  }, [focusedTerminalId, sessionId]);

  useEffect(() => {
    if (!focusedTerminalId || !focusedBufferQuery.data) {
      return;
    }
    setBuffersByTerminal((current) => ({
      ...current,
      [focusedTerminalId]: focusedBufferQuery.data.buffer,
    }));
  }, [focusedBufferQuery.data, focusedTerminalId]);

  useEffect(() => {
    const latestEvent = sessionEvents[sessionEvents.length - 1];
    if (!latestEvent || latestEvent.id === lastHandledTerminalEventIdRef.current) {
      return;
    }
    lastHandledTerminalEventIdRef.current = latestEvent.id;
    if (!latestEvent.type.startsWith("terminal.")) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    void queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    if (focusedTerminalId) {
      void queryClient.invalidateQueries({
        queryKey: TERMINAL_BUFFER_QUERY_KEY(sessionId, focusedTerminalId, reconnectKey),
      });
    }
  }, [focusedTerminalId, queryClient, reconnectKey, sessionEvents, sessionId]);

  const focusedConnectionState =
    (focusedTerminalId && connectionStateByTerminal[focusedTerminalId]) ?? "closed";
  const focusedTerminalError =
    (focusedTerminalId && terminalErrors[focusedTerminalId]) ?? null;
  const focusedCachedBuffer =
    (focusedTerminalId && buffersByTerminal[focusedTerminalId]) ?? "";
  const focusedBootstrapBuffer = focusedBufferQuery.data?.buffer ?? focusedCachedBuffer;
  const isFocusedBufferReady =
    !focusedTerminal ||
    focusedCachedBuffer.length > 0 ||
    focusedBufferQuery.isSuccess ||
    focusedBufferQuery.isError;

  function appendBuffer(terminalId: string, content: string): void {
    setBuffersByTerminal((current) => {
      const next = `${current[terminalId] ?? ""}${content}`;
      return {
        ...current,
        [terminalId]: next.slice(-50_000),
      };
    });
  }

  function setTerminalError(terminalId: string, message: string | null): void {
    setTerminalErrors((current) => ({
      ...current,
      [terminalId]: message,
    }));
  }

  async function handleExecute(command: string, detach: boolean): Promise<void> {
    if (!focusedTerminalId) {
      return;
    }
    try {
      await executeTerminalMutation.mutateAsync({
        terminalId: focusedTerminalId,
        command,
        detach,
      });
    } catch (error) {
      setTerminalError(focusedTerminalId, readErrorMessage(error));
      throw error;
    }
  }

  async function handleInterrupt(): Promise<void> {
    if (!focusedTerminalId) {
      return;
    }
    try {
      await interruptMutation.mutateAsync(focusedTerminalId);
    } catch (error) {
      setTerminalError(focusedTerminalId, readErrorMessage(error));
    }
  }

  async function handleClose(terminalId: string): Promise<void> {
    try {
      await closeTerminalMutation.mutateAsync(terminalId);
    } catch (error) {
      setTerminalError(terminalId, readErrorMessage(error));
    }
  }

  function handleReconnect(): void {
    if (!focusedTerminalId) {
      return;
    }
    setTerminalError(focusedTerminalId, null);
    setReconnectKeysByTerminal((current) => ({
      ...current,
      [focusedTerminalId]: (current[focusedTerminalId] ?? 0) + 1,
    }));
  }

  function handlePaneRuntimeEvent(terminalId: string, eventType: string): void {
    if (eventType === "ready") {
      setTerminalError(terminalId, null);
    }
    if (eventType === "socket.error") {
      setTerminalError(
        terminalId,
        "终端连接失败，请检查是否被占用、runtime 未启动或终端已关闭。",
      );
    }
    if (
      eventType === "ready" ||
      eventType === "closed" ||
      eventType === "exit" ||
      eventType === "socket.close" ||
      eventType === "socket.error"
    ) {
      void queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      void queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    }
  }

  return (
    <section className="shell-workbench" data-testid="shell-workbench">
      <header className="shell-workbench-header">
        <div>
          <h3 className="shell-section-title">Shell Workbench</h3>
          <p className="shell-section-copy">
            聚焦当前终端，持续输入多条命令，并查看后台任务状态。
          </p>
        </div>
        <div className="shell-workbench-status">
          <span>连接：{focusedConnectionState}</span>
          <span>
            服务端状态：{focusedTerminal?.workbench_status ?? "idle"}
          </span>
        </div>
      </header>

      <TerminalTabs
        terminals={terminals}
        focusedTerminalId={focusedTerminalId}
        disabled={disabled}
        onSelect={setFocusedTerminalId}
        onClose={(terminalId) => void handleClose(terminalId)}
        onCreate={() => createTerminalMutation.mutate()}
      />

      {focusedTerminal ? (
        isFocusedBufferReady ? (
          <TerminalPane
            key={`${focusedTerminal.id}:${reconnectKey}`}
            sessionId={sessionId}
            terminal={focusedTerminal}
            bootstrapBuffer={focusedBootstrapBuffer}
            reconnectKey={reconnectKey}
            onBufferAppend={appendBuffer}
            onConnectionStateChange={(terminalId, state) => {
              setConnectionStateByTerminal((current) => ({
                ...current,
                [terminalId]: state,
              }));
            }}
            onRuntimeEvent={handlePaneRuntimeEvent}
          />
        ) : (
          <div className="shell-empty-state">正在恢复终端最近输出…</div>
        )
      ) : (
        <div className="shell-empty-state">还没有终端，点击“新建终端”开始。</div>
      )}

      <TerminalCommandBar
        terminal={focusedTerminal}
        disabled={disabled}
        busy={
          executeTerminalMutation.isPending ||
          interruptMutation.isPending ||
          closeTerminalMutation.isPending
        }
        errorMessage={focusedTerminalError}
        onExecuteForeground={(command) => handleExecute(command, false)}
        onExecuteBackground={(command) => handleExecute(command, true)}
        onInterrupt={handleInterrupt}
        onReconnect={handleReconnect}
        onClose={() => (focusedTerminal ? handleClose(focusedTerminal.id) : Promise.resolve())}
      />

      <BackgroundJobsPanel
        jobs={jobs}
        terminals={terminals}
        disabled={disabled}
        onStopJob={(jobId) => stopJobMutation.mutate(jobId)}
        onCleanup={() => cleanupJobsMutation.mutate()}
      />
    </section>
  );
}
