"""Native app helper: detached Python server, file locks, and a private stop pipe.

Standard library only. Never reads API credentials or kills a process by port/PID file.
"""

import argparse
import errno
import fcntl
import http.client
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path.home() / "Library/Application Support/Next/config.json"


class LauncherError(Exception):
    pass


@dataclass
class StartResult:
    url: str
    reused: bool


def configured_port(path=CONFIG_PATH):
    if not path.exists():
        return 8765
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("port", 8765)
        if isinstance(value, bool) or int(value) != float(value) or not 1024 <= int(value) <= 65535:
            raise ValueError
        return int(value)
    except (OSError, ValueError, TypeError, AttributeError):
        raise LauncherError("无法读取 Next 配置中的端口。请检查 config.json；不要删除或重填 API Key。") from None


class Launcher:
    def __init__(self, project=PROJECT, runtime=None, logs=None, port=None):
        self.project = Path(project).resolve()
        self.python = self.project / ".venv/bin/python"
        self.runtime = Path(runtime or Path.home() / "Library/Application Support/Next Launcher").resolve()
        self.logs = Path(logs or Path.home() / "Library/Logs/Next").resolve()
        self.log_file = self.logs / "server.log"
        self.control = self.runtime / "stop.pipe"
        self.server_lock = self.runtime / "server.lock"
        self.state_file = self.runtime / "server.json"
        self.port = configured_port() if port is None else port
        self.url = "http://127.0.0.1:{}".format(self.port)

    def prepare(self):
        for directory in (self.runtime, self.logs):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        descriptor = os.open(self.log_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.close(descriptor)
        os.chmod(self.log_file, 0o600)

    @contextmanager
    def lock(self):
        descriptor = os.open(self.runtime / "launcher.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w") as lock_file:
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LauncherError("Next 正在启动或关闭，请稍等后再次双击。") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def check_files(self):
        if not (self.project / "next_app/static/index.html").is_file():
            raise LauncherError("找不到 Next 项目。请恢复原项目路径，或按 macOS 启动说明重新生成图标。")
        if not os.access(self.python, os.X_OK):
            raise LauncherError("找不到可运行的 .venv/bin/python。请按项目 README 修复虚拟环境；现有凭证无需重填。")

    def probe(self):
        """Check exact assets and a Next response header, without /api/status."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=0.8)
        try:
            for route, filename in (("/", "index.html"), ("/static/app.js", "app.js")):
                connection.request("GET", route)
                response = connection.getresponse()
                content = response.read(1024 * 1024)
                expected = (self.project / "next_app/static" / filename).read_bytes()
                if response.status != 200 or content != expected or response.getheader("X-Content-Type-Options") != "nosniff":
                    return "occupied"
            return "ready"
        except ConnectionRefusedError:
            return "free"
        except OSError as exc:
            return "free" if exc.errno == errno.ECONNREFUSED else "occupied"
        except http.client.HTTPException:
            return "occupied"
        finally:
            connection.close()

    def owns_server(self):
        descriptor = os.open(self.server_lock, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            return False

    def server_command(self):
        return [
            str(self.python), "-m", "uvicorn", "next_app.app:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log",
            "--timeout-graceful-shutdown", "10",
        ]

    def open_page(self):
        result = subprocess.run(["/usr/bin/open", self.url], capture_output=True, timeout=10)
        if result.returncode:
            raise LauncherError("Next 已就绪，但默认浏览器未能打开。请手动访问 " + self.url)

    def stop_owned(self, timeout=20):
        """Ask our live supervisor to stop its own child. Never trust a saved PID."""
        deadline = time.monotonic() + timeout
        sent = False
        while self.owns_server():
            if time.monotonic() >= deadline:
                raise LauncherError("Next 后台进程暂未完成关闭。请查看日志后重试。")
            if not sent:
                try:
                    if not stat.S_ISFIFO(self.control.stat().st_mode):
                        raise LauncherError("Next 控制文件异常，未结束任何进程。请查看 macOS 启动说明。")
                    descriptor = os.open(self.control, os.O_WRONLY | os.O_NONBLOCK)
                    try:
                        os.write(descriptor, b"stop\n")
                        sent = True
                    finally:
                        os.close(descriptor)
                except OSError as exc:
                    if exc.errno not in (errno.ENOENT, errno.ENXIO):
                        raise
            time.sleep(0.1)

    def start(self, open_browser=True, timeout=20):
        self.check_files()
        self.prepare()
        with self.lock():
            state = self.probe()
            if state == "ready":
                if open_browser:
                    self.open_page()
                return StartResult(self.url, reused=True)
            if state == "occupied":
                raise LauncherError(
                    "端口 {} 已被其他服务占用或无法确认是 Next。\n"
                    "请检查占用程序，或修改 Next 配置中的 port 后重试。启动器不会结束占用进程。".format(self.port)
                )
            process = None
            if not self.owns_server():
                # The helper stays detached with its own child handle and lock.
                # No Terminal, login item, launchd registration, or stored key.
                with self.log_file.open("ab", buffering=0) as log:
                    process = subprocess.Popen(
                        [str(self.python), str(Path(__file__).resolve()), "_serve",
                         "--project", str(self.project), "--runtime", str(self.runtime),
                         "--logs", str(self.logs), "--port", str(self.port)],
                        cwd=self.project, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True, close_fds=True,
                    )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.probe() == "ready":
                    if open_browser:
                        self.open_page()
                    return StartResult(self.url, reused=False)
                if process is not None and process.poll() is not None:
                    break
                time.sleep(0.2)
            self.stop_owned()
            raise LauncherError("Next 没有在规定时间内启动成功。请查看日志；常见原因是依赖缺失、配置错误或端口冲突。")

    def stop(self):
        self.prepare()
        with self.lock():
            self.stop_owned()

    def ensure(self):
        """Recover after a reused Terminal server exits; leave healthy servers alone."""
        result = self.start(open_browser=False)
        if not result.reused:
            self.open_page()
        return result

    def supervise(self):
        """Own the server child until it exits or the app asks it to stop."""
        self.prepare()
        descriptor = os.open(self.server_lock, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            child = None
            control = None
            stopping = False

            def request_stop(signum, frame):
                nonlocal stopping
                stopping = True

            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
            try:
                if self.control.exists():
                    if not stat.S_ISFIFO(self.control.stat().st_mode):
                        raise LauncherError("Next 控制文件异常；未覆盖该文件。")
                    self.control.unlink()
                os.mkfifo(self.control, 0o600)
                control = os.open(self.control, os.O_RDWR | os.O_NONBLOCK)
                child = subprocess.Popen(
                    self.server_command(), cwd=self.project, stdin=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                state_descriptor = os.open(self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(state_descriptor, "w", encoding="utf-8") as state:
                    json.dump({"supervisor_pid": os.getpid(), "server_pid": child.pid, "port": self.port}, state)
                while child.poll() is None and not stopping:
                    readable, _, _ = select.select([control], [], [], 0.2)
                    if readable and b"stop" in os.read(control, 4096):
                        stopping = True
                return child.returncode or 0
            finally:
                if child is not None and child.poll() is None:
                    # An unreaped child handle identifies our process exactly;
                    # a port lookup or a stale PID file is never used to kill.
                    child.terminate()
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                if control is not None:
                    os.close(control)
                    self.control.unlink(missing_ok=True)
                self.state_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Next macOS desktop launcher")
    parser.add_argument("action", choices=["start", "ensure", "stop", "_serve"])
    # Internal supervisor arguments, also used for isolated lifecycle tests.
    parser.add_argument("--project", type=Path, default=PROJECT, help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--logs", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    launcher = None
    try:
        launcher = Launcher(args.project, args.runtime, args.logs, port=8765 if args.action == "stop" else args.port)
        if args.action == "start":
            result = launcher.start()
            print(("已打开运行中的 Next：" if result.reused else "Next 已启动：") + result.url)
        elif args.action == "stop":
            launcher.stop()
        elif args.action == "ensure":
            launcher.ensure()
        else:
            sys.exit(launcher.supervise())
    except (LauncherError, OSError, subprocess.TimeoutExpired) as exc:
        log_file = launcher.log_file if launcher else Path.home() / "Library/Logs/Next/server.log"
        print("{}\n\n日志：{}".format(exc, log_file), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
