import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cleanupSessionTerminalJobs,
  closeSessionTerminal,
  createSessionTerminal,
  executeTerminalCommand,
  getSessionTerminalBuffer,
  getSessionTerminal,
  getRuntimeStatus,
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
import { TerminalPane, type TerminalPaneRuntimeEvent } from "./TerminalPane";
import { TerminalTabs } from "./TerminalTabs";

type ShellWorkbenchProps = {
  sessionId: string;
  disabled?: boolean;
  variant?: "default" | "focus-docked";
  focusRequest?: ShellWorkbenchFocusRequest | null;
  onDismiss?: () => void;
};

export type ShellWorkbenchFocusRequest = {
  requestId: number;
  terminalId: string | null;
  command: string;
  toolCallId: string | null;
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

function isTerminalEnded(terminal: TerminalSession | null): boolean {
  if (!terminal) {
    return false;
  }
  return (
    terminal.status === "closed" ||
    terminal.workbench_status === "completed" ||
    terminal.workbench_status === "failed" ||
    terminal.workbench_status === "cancelled"
  );
}

export function ShellWorkbench({
  sessionId,
  disabled = false,
  variant = "default",
  focusRequest = null,
  onDismiss,
}: ShellWorkbenchProps) {
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
  const [terminalNotices, setTerminalNotices] = useState<Record<string, string | null>>({});
  const [reconnectKeysByTerminal, setReconnectKeysByTerminal] = useState<Record<string, number>>({});
  const [pendingFocusRequest, setPendingFocusRequest] =
    useState<ShellWorkbenchFocusRequest | null>(null);
  const lastHandledTerminalEventIdRef = useRef<string | null>(null);
  const previousSessionIdRef = useRef(sessionId);
  const lastAutoCreateFocusRequestIdRef = useRef<number | null>(null);

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
      setTerminalNotices((current) => ({ ...current, [terminal.id]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    },
  });

  const closeTerminalMutation = useMutation({
    mutationFn: (terminalId: string) => closeSessionTerminal(sessionId, terminalId),
    onSuccess: async (terminal) => {
      setTerminalErrors((current) => ({ ...current, [terminal.id]: null }));
      setTerminalNotices((current) => ({ ...current, [terminal.id]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const closeAllTerminalsMutation = useMutation({
    mutationFn: async (terminalIds: string[]) => {
      for (const terminalId of terminalIds) {
        await closeSessionTerminal(sessionId, terminalId);
      }
    },
    onSuccess: async () => {
      setFocusedTerminalId(null);
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
      setTerminalNotices((current) => ({ ...current, [variables.terminalId]: null }));
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const interruptMutation = useMutation({
    mutationFn: (terminalId: string) => interruptTerminal(sessionId, terminalId),
    onSuccess: async (_, terminalId) => {
      setTerminalErrors((current) => ({ ...current, [terminalId]: null }));
      setTerminalNotices((current) => ({ ...current, [terminalId]: null }));
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
  const isDockedVariant = variant === "focus-docked";
  const focusedTerminal =
    terminals.find((terminal) => terminal.id === focusedTerminalId) ?? null;

  useEffect(() => {
    if (!focusRequest) {
      return;
    }
    setPendingFocusRequest(focusRequest);
  }, [focusRequest]);

  useEffect(() => {
    if (
      !isDockedVariant ||
      !focusRequest ||
      disabled ||
      terminalsQuery.isLoading ||
      createTerminalMutation.isPending ||
      terminals.length > 0
    ) {
      return;
    }

    if (lastAutoCreateFocusRequestIdRef.current === focusRequest.requestId) {
      return;
    }

    lastAutoCreateFocusRequestIdRef.current = focusRequest.requestId;
    createTerminalMutation.mutate();
  }, [
    createTerminalMutation,
    disabled,
    focusRequest,
    isDockedVariant,
    terminals.length,
    terminalsQuery.isLoading,
  ]);

  useEffect(() => {
    if (previousSessionIdRef.current === sessionId) {
      return;
    }
    previousSessionIdRef.current = sessionId;
    setFocusedTerminalId(readStoredFocusedTerminalId(sessionId));
    setBuffersByTerminal({});
    setConnectionStateByTerminal({});
    setTerminalErrors({});
    setTerminalNotices({});
    setReconnectKeysByTerminal({});
    setPendingFocusRequest(null);
    lastAutoCreateFocusRequestIdRef.current = null;
    lastHandledTerminalEventIdRef.current = null;
  }, [sessionId]);

  useEffect(() => {
    if (!pendingFocusRequest || terminalsQuery.isLoading) {
      return;
    }

    const requestedTerminalId = pendingFocusRequest.terminalId?.trim() || null;

    if (requestedTerminalId && terminals.some((terminal) => terminal.id === requestedTerminalId)) {
      setFocusedTerminalId(requestedTerminalId);
      setTerminalError(requestedTerminalId, null);
      setTerminalNotice(requestedTerminalId, null);
      setPendingFocusRequest(null);
      return;
    }

    if (terminals.length === 0) {
      setPendingFocusRequest(null);
      return;
    }

    const fallbackTerminalId = pickFocusedTerminal(
      terminals,
      focusedTerminalId ?? readStoredFocusedTerminalId(sessionId),
    );

    if (fallbackTerminalId && fallbackTerminalId !== focusedTerminalId) {
      setFocusedTerminalId(fallbackTerminalId);
    }

    if (requestedTerminalId && fallbackTerminalId) {
      setTerminalNotice(fallbackTerminalId, "未找到卡片关联终端，已切换到当前可用终端。");
    }

    setPendingFocusRequest(null);
  }, [focusedTerminalId, pendingFocusRequest, sessionId, terminals, terminalsQuery.isLoading]);

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
    const storedFocusedTerminalId = readStoredFocusedTerminalId(sessionId);
    const focusCandidate =
      focusedTerminalId && terminals.some((terminal) => terminal.id === focusedTerminalId)
        ? focusedTerminalId
        : storedFocusedTerminalId;
    const nextFocusedTerminalId = pickFocusedTerminal(
      terminals,
      focusCandidate,
    );
    if (nextFocusedTerminalId !== focusedTerminalId) {
      setFocusedTerminalId(nextFocusedTerminalId);
    }
  }, [focusedTerminalId, sessionId, terminals, terminalsQuery.isLoading]);

  useEffect(() => {
    if (
      focusedTerminalId !== null &&
      terminals.length > 0 &&
      !terminals.some((terminal) => terminal.id === focusedTerminalId)
    ) {
      return;
    }
    writeStoredFocusedTerminalId(sessionId, focusedTerminalId);
  }, [focusedTerminalId, sessionId, terminals]);

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
    setConnectionStateByTerminal((current) => {
      let changed = false;
      const next = { ...current };
      for (const terminal of terminals) {
        if (isTerminalEnded(terminal) && next[terminal.id] !== "closed") {
          next[terminal.id] = "closed";
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [terminals]);

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
  const focusedTerminalNotice =
    (focusedTerminalId && terminalNotices[focusedTerminalId]) ?? null;
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

  function setTerminalNotice(terminalId: string, message: string | null): void {
    setTerminalNotices((current) => ({
      ...current,
      [terminalId]: message,
    }));
  }

  async function resolveTerminalConnectionIssue(
    terminalId: string,
    options: { hadReady: boolean; ended: boolean },
  ): Promise<void> {
    try {
      const [terminal, runtimeStatus] = await Promise.all([
        getSessionTerminal(sessionId, terminalId),
        getRuntimeStatus(),
      ]);

      if (options.ended || isTerminalEnded(terminal)) {
        setConnectionStateByTerminal((current) => ({
          ...current,
          [terminalId]: "closed",
        }));
        setTerminalError(terminalId, null);
        setTerminalNotice(terminalId, "终端已结束，请切换其他终端或新建终端。");
        return;
      }

      if (runtimeStatus.runtime.status !== "running") {
        setTerminalError(terminalId, "Runtime 未启动，无法附着终端。");
        setTerminalNotice(terminalId, null);
        return;
      }

      if (!options.hadReady && terminal.attached) {
        setTerminalError(terminalId, "该 terminal 已被其他连接占用，当前无法附着。");
        setTerminalNotice(terminalId, null);
        return;
      }

      setTerminalError(terminalId, "终端连接已断开，可点击“重连”尝试恢复。");
      setTerminalNotice(terminalId, null);
    } catch (error) {
      setTerminalError(terminalId, readErrorMessage(error));
      setTerminalNotice(terminalId, null);
    }
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
      setTerminalNotice(focusedTerminalId, null);
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
      setTerminalNotice(focusedTerminalId, null);
    }
  }

  async function handleClose(terminalId: string): Promise<void> {
    try {
      await closeTerminalMutation.mutateAsync(terminalId);
    } catch (error) {
      if (isApiError(error) && error.status === 409) {
        try {
          await interruptMutation.mutateAsync(terminalId);
          await closeTerminalMutation.mutateAsync(terminalId);
          setTerminalError(terminalId, null);
          setTerminalNotice(terminalId, null);
          return;
        } catch {
          // no-op: 兜底失败后走统一错误提示
        }
      }
      setTerminalError(terminalId, readErrorMessage(error));
      setTerminalNotice(terminalId, null);
    }
  }

  async function handleCloseAll(): Promise<void> {
    const openTerminalIds = terminals
      .filter((terminal) => terminal.status !== "closed")
      .map((terminal) => terminal.id);

    if (openTerminalIds.length === 0) {
      return;
    }

    try {
      await closeAllTerminalsMutation.mutateAsync(openTerminalIds);
    } catch (error) {
      const primaryTerminalId = focusedTerminalId ?? openTerminalIds[0] ?? null;
      if (primaryTerminalId) {
        setTerminalError(primaryTerminalId, readErrorMessage(error));
        setTerminalNotice(primaryTerminalId, null);
      }
    }
  }

  function handleReconnect(): void {
    if (!focusedTerminalId) {
      return;
    }
    setTerminalError(focusedTerminalId, null);
    setTerminalNotice(focusedTerminalId, "正在重连终端…");
    setConnectionStateByTerminal((current) => ({
      ...current,
      [focusedTerminalId]: "connecting",
    }));
    setReconnectKeysByTerminal((current) => ({
      ...current,
      [focusedTerminalId]: (current[focusedTerminalId] ?? 0) + 1,
    }));
  }

  function handlePaneRuntimeEvent(terminalId: string, event: TerminalPaneRuntimeEvent): void {
    if (event.type === "ready") {
      setTerminalError(terminalId, null);
      setTerminalNotice(terminalId, event.reattached ? "已重新附着到原终端。" : null);
    }
    if (event.type === "closed" || event.type === "exit") {
      setConnectionStateByTerminal((current) => ({
        ...current,
        [terminalId]: "closed",
      }));
      setTerminalError(terminalId, null);
      setTerminalNotice(
        terminalId,
        event.type === "exit"
          ? `终端执行已结束（${event.reason}）。`
          : `终端连接已关闭（${event.reason}）。`,
      );
    }
    if (event.type === "error") {
      setTerminalError(terminalId, event.message);
      setTerminalNotice(terminalId, null);
    }
    if (event.type === "socket.close" || event.type === "socket.error") {
      void resolveTerminalConnectionIssue(terminalId, {
        hadReady: event.hadReady,
        ended: event.ended,
      });
    }
    if (
      event.type === "ready" ||
      event.type === "closed" ||
      event.type === "exit" ||
      event.type === "socket.close" ||
      event.type === "socket.error"
    ) {
      void queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      void queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    }
  }

  return (
    <section
      className={`shell-workbench${isDockedVariant ? " shell-workbench-docked" : ""}`}
      data-testid="shell-workbench"
    >
      {isDockedVariant ? (
        onDismiss ? (
          <div className="shell-workbench-docked-toolbar">
            <button
              className="button button-secondary shell-workbench-dismiss"
              type="button"
              onClick={onDismiss}
            >
              收起
            </button>
          </div>
        ) : null
      ) : (
        <header className="shell-workbench-header">
          <div>
            <h3 className="shell-section-title">Shell Workbench</h3>
            <p className="shell-section-copy">聚焦当前终端，持续输入多条命令，并查看后台任务状态。</p>
          </div>
          <div className="shell-workbench-status">
            <span>连接：{focusedConnectionState}</span>
            <span>
              服务端状态：{focusedTerminal?.workbench_status ?? "idle"}
            </span>
          </div>
        </header>
      )}

      <TerminalTabs
        terminals={terminals}
        focusedTerminalId={focusedTerminalId}
        disabled={disabled || closeAllTerminalsMutation.isPending}
        onSelect={setFocusedTerminalId}
        onClose={(terminalId) => void handleClose(terminalId)}
        onCreate={() => createTerminalMutation.mutate()}
        onCloseAll={() => void handleCloseAll()}
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
        <div className="shell-empty-state">
          {isDockedVariant && createTerminalMutation.isPending
            ? "正在为当前会话创建终端…"
            : "还没有终端，点击“+”开始。"}
        </div>
      )}

      <TerminalCommandBar
        terminal={focusedTerminal}
        disabled={disabled}
        busy={
          executeTerminalMutation.isPending ||
          interruptMutation.isPending ||
          closeTerminalMutation.isPending ||
          closeAllTerminalsMutation.isPending
        }
        errorMessage={focusedTerminalError ?? focusedTerminalNotice}
        onExecuteForeground={(command) => handleExecute(command, false)}
        onExecuteBackground={(command) => handleExecute(command, true)}
        onInterrupt={handleInterrupt}
        onReconnect={handleReconnect}
      />

      {!isDockedVariant ? (
        <BackgroundJobsPanel
          jobs={jobs}
          terminals={terminals}
          disabled={disabled}
          onStopJob={(jobId) => stopJobMutation.mutate(jobId)}
          onCleanup={() => cleanupJobsMutation.mutate()}
        />
      ) : null}
    </section>
  );
}
