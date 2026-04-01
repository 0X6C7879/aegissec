from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_BASE_URL = "http://127.0.0.1:8000"


def expect_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected {label} payload")
    return value


def request_json(
    path: str, *, method: str = "GET", payload: dict[str, object] | None = None
) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - local API only
        payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload


def main() -> None:
    try:
        projects = request_json(f"/api/projects?q={quote('Demo Workspace')}")
        if not isinstance(projects, list):
            raise RuntimeError("Unexpected projects payload")

        demo_project = next(
            (
                project
                for project in projects
                if isinstance(project, dict) and project.get("name") == "Demo Workspace"
            ),
            None,
        )
        if demo_project is None:
            demo_project = expect_dict(
                request_json(
                    "/api/projects",
                    method="POST",
                    payload={
                        "name": "Demo Workspace",
                        "description": "用于模块 E 浏览器验收的示例项目。",
                    },
                ),
                "project",
            )
        else:
            demo_project = expect_dict(demo_project, "project")

        sessions = request_json(
            f"/api/sessions?q={quote('Authorized Assessment Demo')}"
        )
        if not isinstance(sessions, list):
            raise RuntimeError("Unexpected sessions payload")

        demo_session = next(
            (
                session
                for session in sessions
                if isinstance(session, dict)
                and session.get("title") == "Authorized Assessment Demo"
            ),
            None,
        )
        if demo_session is None:
            demo_session = expect_dict(
                request_json(
                    "/api/sessions",
                    method="POST",
                    payload={
                        "title": "Authorized Assessment Demo",
                        "project_id": demo_project["id"],
                        "goal": "验证 Workspace、History、Runtime、Skills 与 MCP 页面联调链路。",
                        "scenario_type": "web",
                    },
                ),
                "session",
            )
            request_json(
                f"/api/sessions/{demo_session['id']}/chat",
                method="POST",
                payload={"content": "请生成一个最小的授权评估计划。"},
            )
        else:
            demo_session = expect_dict(demo_session, "session")

        print(f"seeded project=Demo Workspace session_id={demo_session['id']}")
    except HTTPError as error:
        raise SystemExit(
            f"HTTP {error.code}: 无法写入演示数据，请确认 API 已启动：{API_BASE_URL}"
        ) from error
    except URLError as error:
        raise SystemExit(f"无法连接 API：{API_BASE_URL} ({error.reason})") from error


if __name__ == "__main__":
    main()
