import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./components/AuthGate";
import { AuthLoginPage } from "./components/AuthLoginPage";
import { ConversationWorkbench } from "./components/ConversationWorkbench";
import { GraphWorkbench } from "./components/GraphWorkbench";
import { McpWorkbench } from "./components/McpWorkbench";
import { RuntimeWorkspace } from "./components/RuntimeWorkspace";
import { SettingsWorkbench } from "./components/SettingsWorkbench";
import { SkillsWorkbench } from "./components/SkillsWorkbench";
import { WorkbenchShell } from "./components/WorkbenchShell";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthLoginPage />} />

      <Route element={<AuthGate />}>
        <Route element={<WorkbenchShell />}>
          <Route path="/" element={<Navigate to="/sessions" replace />} />
          <Route path="/sessions" element={<ConversationWorkbench />} />
          <Route path="/sessions/:sessionId/chat" element={<ConversationWorkbench />} />
          <Route path="/sessions/:sessionId/graph" element={<GraphWorkbench />} />
          <Route path="/projects" element={<Navigate to="/sessions" replace />} />
          <Route path="/history" element={<Navigate to="/sessions" replace />} />
          <Route path="/skills" element={<SkillsWorkbench />} />
          <Route path="/skills/:skillId" element={<SkillsWorkbench />} />
          <Route path="/mcp" element={<McpWorkbench />} />
          <Route path="/mcp/:serverId" element={<McpWorkbench />} />
          <Route path="/settings" element={<SettingsWorkbench />} />
          <Route path="/runtime" element={<RuntimeWorkspace />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/sessions" replace />} />
    </Routes>
  );
}
