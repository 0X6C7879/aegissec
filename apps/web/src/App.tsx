import { Navigate, Route, Routes } from "react-router-dom";
import { ConversationWorkbench } from "./components/ConversationWorkbench";
import { GraphWorkbench } from "./components/GraphWorkbench";
import { McpWorkbench } from "./components/McpWorkbench";
import { SettingsWorkbench } from "./components/SettingsWorkbench";
import { SkillsWorkbench } from "./components/SkillsWorkbench";
import { WorkbenchShell } from "./components/WorkbenchShell";

export default function App() {
  return (
    <Routes>
      <Route element={<WorkbenchShell />}>
        <Route path="/" element={<Navigate to="/sessions" replace />} />
        <Route path="/sessions" element={<ConversationWorkbench />} />
        <Route path="/sessions/:sessionId/chat" element={<ConversationWorkbench />} />
        <Route path="/sessions/:sessionId/graph" element={<GraphWorkbench />} />
        <Route path="/skills" element={<SkillsWorkbench />} />
        <Route path="/skills/:skillId" element={<SkillsWorkbench />} />
        <Route path="/mcp" element={<McpWorkbench />} />
        <Route path="/mcp/:serverId" element={<McpWorkbench />} />
        <Route path="/settings" element={<SettingsWorkbench />} />
        <Route path="/runtime" element={<Navigate to="/sessions" replace />} />
      </Route>
    </Routes>
  );
}
