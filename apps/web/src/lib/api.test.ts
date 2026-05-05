import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apiRequestEnvelope,
  listRuntimeRuns,
  listRuntimeRunsWithMeta,
} from "./api";
import type { RuntimeExecutionRun } from "../types/runtime";

function createRuntimeRun(id: string): RuntimeExecutionRun {
  return {
    id,
    session_id: "session-1",
    command: "echo ok",
    requested_timeout_seconds: 30,
    status: "success",
    exit_code: 0,
    stdout: "ok",
    stderr: "",
    container_name: "runtime",
    created_at: "2026-05-01T00:00:00.000Z",
    started_at: "2026-05-01T00:00:00.000Z",
    ended_at: "2026-05-01T00:00:00.000Z",
    artifacts: [],
  };
}

describe("api envelope helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps apiRequest callers returning data payload only", async () => {
    const run = createRuntimeRun("run-1");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: [run],
            meta: {
              pagination: { page: 1, page_size: 1, total: 42 },
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const runs = await listRuntimeRuns({ page: 1, page_size: 1 });

    expect(runs).toEqual([run]);
  });

  it("returns full envelope with meta in apiRequestEnvelope", async () => {
    const run = createRuntimeRun("run-2");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: [run],
            meta: {
              pagination: { page: 2, page_size: 1, total: 99 },
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const envelope = await apiRequestEnvelope<RuntimeExecutionRun[]>("/api/runtime/runs?page=2");

    expect(envelope.data).toEqual([run]);
    expect(envelope.meta?.pagination?.total).toBe(99);
  });

  it("exposes pagination meta through listRuntimeRunsWithMeta", async () => {
    const run = createRuntimeRun("run-3");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: [run],
            meta: {
              pagination: { page: 1, page_size: 20, total: 123 },
              sort: { by: "started_at", direction: "desc" },
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const envelope = await listRuntimeRunsWithMeta({ page_size: 20 });

    expect(envelope.data).toEqual([run]);
    expect(envelope.meta?.pagination?.total).toBe(123);
  });
});
