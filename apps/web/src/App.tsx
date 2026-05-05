import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./components/AuthGate";
import { AuthLoginPage } from "./components/AuthLoginPage";
import { ConversationWorkbench } from "./components/ConversationWorkbench";
import { WorkbenchShell } from "./components/WorkbenchShell";

const GraphWorkbench = lazy(() =>
  import("./components/GraphWorkbench").then((module) => ({ default: module.GraphWorkbench })),
);
const McpWorkbench = lazy(() =>
  import("./components/McpWorkbench").then((module) => ({ default: module.McpWorkbench })),
);
const RuntimeWorkspace = lazy(() =>
  import("./components/RuntimeWorkspace").then((module) => ({ default: module.RuntimeWorkspace })),
);
const SettingsWorkbench = lazy(() =>
  import("./components/SettingsWorkbench").then((module) => ({ default: module.SettingsWorkbench })),
);
const SkillsWorkbench = lazy(() =>
  import("./components/SkillsWorkbench").then((module) => ({ default: module.SkillsWorkbench })),
);

const lazyRouteFallback = <div className="workbench-route-loading">Loading...</div>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthLoginPage />} />

      <Route element={<AuthGate />}>
        <Route element={<WorkbenchShell />}>
          <Route path="/" element={<Navigate to="/sessions" replace />} />
          <Route path="/sessions" element={<ConversationWorkbench />} />
          <Route path="/sessions/:sessionId/chat" element={<ConversationWorkbench />} />
          <Route
            path="/sessions/:sessionId/graph"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <GraphWorkbench />
              </Suspense>
            }
          />
          <Route path="/projects" element={<Navigate to="/sessions" replace />} />
          <Route path="/history" element={<Navigate to="/sessions" replace />} />
          <Route
            path="/skills"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <SkillsWorkbench />
              </Suspense>
            }
          />
          <Route
            path="/skills/:skillId"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <SkillsWorkbench />
              </Suspense>
            }
          />
          <Route
            path="/mcp"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <McpWorkbench />
              </Suspense>
            }
          />
          <Route
            path="/mcp/:serverId"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <McpWorkbench />
              </Suspense>
            }
          />
          <Route
            path="/settings"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <SettingsWorkbench />
              </Suspense>
            }
          />
          <Route
            path="/runtime"
            element={
              <Suspense fallback={lazyRouteFallback}>
                <RuntimeWorkspace />
              </Suspense>
            }
          />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/sessions" replace />} />
    </Routes>
  );
}
