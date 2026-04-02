__all__ = [
    "Coordinator",
    "Executor",
    "GraphManager",
    "PlannedTaskNode",
    "Planner",
    "Reflector",
    "WorkflowExecutionContext",
]


def __getattr__(name: str) -> object:
    if name == "Coordinator":
        from app.agent.coordinator import Coordinator

        return Coordinator
    if name == "Executor":
        from app.agent.executor import Executor

        return Executor
    if name == "GraphManager":
        from app.agent.graph_manager import GraphManager

        return GraphManager
    if name == "PlannedTaskNode":
        from app.agent.workflow import PlannedTaskNode

        return PlannedTaskNode
    if name == "Planner":
        from app.agent.planner import Planner

        return Planner
    if name == "Reflector":
        from app.agent.reflector import Reflector

        return Reflector
    if name == "WorkflowExecutionContext":
        from app.agent.workflow import WorkflowExecutionContext

        return WorkflowExecutionContext
    raise AttributeError(f"module 'app.agent' has no attribute {name!r}")
