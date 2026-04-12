import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cleanupSessionTerminalJobs,
  createSessionTerminal,
  closeSessionTerminal,
  listSessionTerminalJobs,
  listSessionTerminals,
  stopSessionTerminalJob,
} from "../../lib/api";
import { BackgroundJobsPanel } from "./BackgroundJobsPanel";
import { TerminalPane } from "./TerminalPane";
import { TerminalTabs } from "./TerminalTabs";

type ShellWorkbenchProps = {
  sessionId: string;
  disabled?: boolean;
};

const TERMINALS_QUERY_KEY = (sessionId: string) => ["session", sessionId, "terminals"] as const;
const TERMINAL_JOBS_QUERY_KEY = (sessionId: string) => ["session", sessionId, "terminal-jobs"] as const;

export function ShellWorkbench({ sessionId, disabled = false }: ShellWorkbenchProps) {
  const queryClient = useQueryClient();
  const [focusedTerminalId, setFocusedTerminalId] = useState<string | null>(null);
  const [buffersByTerminal, setBuffersByTerminal] = useState<Record<string, string>>({});
  const [connectionStateByTerminal, setConnectionStateByTerminal] = useState<
    Record<string, "connecting" | "open" | "closed" | "error">
  >({});

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

  const createTerminalMutation = useMutation({
    mutationFn: () =>
      createSessionTerminal(sessionId, {
        title: `Kali #${(terminalsQuery.data?.length ?? 0) + 1}`,
        cwd: `/workspace/sessions/${sessionId}`,
      }),
    onSuccess: async (terminal) => {
      setFocusedTerminalId(terminal.id);
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
    },
  });

  const closeTerminalMutation = useMutation({
    mutationFn: (terminalId: string) => closeSessionTerminal(sessionId, terminalId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: TERMINALS_QUERY_KEY(sessionId) });
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const stopJobMutation = useMutation({
    mutationFn: (jobId: string) => stopSessionTerminalJob(sessionId, jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const cleanupJobsMutation = useMutation({
    mutationFn: () => cleanupSessionTerminalJobs(sessionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: TERMINAL_JOBS_QUERY_KEY(sessionId) });
    },
  });

  const terminals = useMemo(() => terminalsQuery.data ?? [], [terminalsQuery.data]);
  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data]);

  useEffect(() => {
    if (!focusedTerminalId || !terminals.some((terminal) => terminal.id === focusedTerminalId)) {
      setFocusedTerminalId(terminals[0]?.id ?? null);
    }
  }, [focusedTerminalId, terminals]);

  const focusedTerminal = useMemo(
    () => terminals.find((terminal) => terminal.id === focusedTerminalId) ?? null,
    [focusedTerminalId, terminals],
  );

  const focusedConnectionState =
    (focusedTerminalId && connectionStateByTerminal[focusedTerminalId]) ?? "closed";

  function appendBuffer(terminalId: string, content: string): void {
    setBuffersByTerminal((current) => {
      const next = `${current[terminalId] ?? ""}${content}`;
      return {
        ...current,
        [terminalId]: next.slice(-50_000),
      };
    });
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
        <div className="shell-workbench-status">连接状态：{focusedConnectionState}</div>
      </header>

      <TerminalTabs
        terminals={terminals}
        focusedTerminalId={focusedTerminalId}
        disabled={disabled}
        onSelect={setFocusedTerminalId}
        onClose={(terminalId) => closeTerminalMutation.mutate(terminalId)}
        onCreate={() => createTerminalMutation.mutate()}
      />

      {focusedTerminal ? (
        <TerminalPane
          key={focusedTerminal.id}
          sessionId={sessionId}
          terminal={focusedTerminal}
          initialBuffer={buffersByTerminal[focusedTerminal.id] ?? ""}
          onBufferAppend={appendBuffer}
          onConnectionStateChange={(terminalId, state) => {
            setConnectionStateByTerminal((current) => ({
              ...current,
              [terminalId]: state,
            }));
          }}
        />
      ) : (
        <div className="shell-empty-state">还没有终端，点击“新建终端”开始。</div>
      )}

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
