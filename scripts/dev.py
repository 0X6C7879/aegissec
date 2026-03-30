from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
WEB_DIR = REPO_ROOT / "apps" / "web"
PNPM = ["corepack.cmd", "pnpm"] if os.name == "nt" else ["corepack", "pnpm"]


def run(command: list[str], workdir: Path) -> None:
    subprocess.run(command, cwd=workdir, check=True)


def spawn(
    command: list[str], workdir: Path, environment: dict[str, str]
) -> subprocess.Popen[str]:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    return subprocess.Popen(
        command,
        cwd=workdir,
        env=environment,
        text=True,
        creationflags=creationflags,
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.kill()


def main() -> int:
    env = os.environ.copy()
    api_host = env.get("AEGISSEC_API_HOST", "127.0.0.1")
    api_port = env.get("AEGISSEC_API_PORT", "8000")
    web_host = env.get("AEGISSEC_WEB_HOST", "127.0.0.1")
    web_port = env.get("AEGISSEC_WEB_PORT", "5173")

    print("==> Syncing API dependencies")
    run(["uv", "sync", "--all-extras", "--dev"], API_DIR)

    print("==> Installing web dependencies")
    run([*PNPM, "install"], WEB_DIR)

    print("==> Starting aegissec API and web dev servers")
    api_process = spawn(
        [
            "uv",
            "run",
            "uvicorn",
            "app.main:app",
            "--reload",
            "--host",
            api_host,
            "--port",
            api_port,
        ],
        API_DIR,
        env,
    )
    web_process = spawn(
        [
            *PNPM,
            "dev",
            "--host",
            web_host,
            "--port",
            web_port,
        ],
        WEB_DIR,
        env,
    )

    try:
        while True:
            api_exit_code = api_process.poll()
            web_exit_code = web_process.poll()

            if api_exit_code is not None:
                stop_process(web_process)
                return api_exit_code

            if web_exit_code is not None:
                stop_process(api_process)
                return web_exit_code

            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n==> Stopping dev servers")
        stop_process(web_process)
        stop_process(api_process)
        return 0
    finally:
        stop_process(web_process)
        stop_process(api_process)


if __name__ == "__main__":
    sys.exit(main())
