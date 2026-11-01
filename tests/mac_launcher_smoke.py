"""Manual macOS lifecycle verification; no API requests or credentials.

Run from a logged-in macOS desktop: .venv/bin/python tests/mac_launcher_smoke.py
Uses isolated temporary runtime files and free test ports.
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from macos.launcher import Launcher, LauncherError


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def pid(launcher):
    return json.loads(launcher.state_file.read_text())["server_pid"] if launcher.state_file.exists() else None


def main():
    with tempfile.TemporaryDirectory(prefix="next-launcher-smoke-") as directory:
        directory = Path(directory)
        launcher = Launcher(
            PROJECT, directory / "runtime", directory / "logs",
            port=free_port(),
        )
        try:
            assert launcher.start(open_browser=False).reused is False
            first_pid = pid(launcher)
            assert first_pid is not None
            print("PASS cold-start actual Next; port={}, pid={}".format(launcher.port, first_pid), flush=True)
            code = (
                "import json,sys; from macos.launcher import Launcher; "
                "s=json.loads(sys.argv[1]); r=Launcher(**s).start(open_browser=False); "
                "assert r.reused"
            )
            options = json.dumps({
                "project": str(PROJECT), "runtime": str(launcher.runtime), "logs": str(launcher.logs),
                "port": launcher.port,
            })
            workers = [subprocess.Popen([str(launcher.python), "-c", code, options], cwd=PROJECT) for _ in range(3)]
            assert all(worker.wait(timeout=30) == 0 for worker in workers)
            assert pid(launcher) == first_pid
            print("PASS three concurrent repeat launches; same single server pid={}".format(first_pid), flush=True)
            launcher.stop()
            deadline = time.monotonic() + 5
            while launcher.probe() != "free" and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not launcher.owns_server() and launcher.probe() == "free"
            print("PASS graceful shutdown stops only owned child", flush=True)
            # A reboot/crash can leave files, but not a held kernel lock. A saved
            # PID must not be used to identify or terminate a process.
            os.mkfifo(launcher.control, 0o600)
            launcher.state_file.write_text('{"server_pid": 1, "supervisor_pid": 1}')
            assert launcher.start(open_browser=False).reused is False
            assert pid(launcher) is not None and pid(launcher) != first_pid
            print("PASS fresh start with stale files; saved PID ignored", flush=True)
        except Exception:
            if launcher.log_file.exists():
                print(launcher.log_file.read_text(errors="replace")[-4000:], flush=True)
            raise
        finally:
            launcher.stop()

        # Reproduce the original failure: reuse a manually launched server,
        # close it, then let the app's recovery action create a detached server.
        with (directory / "manual-server.log").open("ab") as manual_log:
            manual = subprocess.Popen(
                launcher.server_command(), cwd=PROJECT, stdin=subprocess.DEVNULL,
                stdout=manual_log, stderr=subprocess.STDOUT,
            )
            original_open_page = launcher.open_page
            launcher.open_page = lambda: None
            try:
                deadline = time.monotonic() + 20
                while launcher.probe() != "ready" and time.monotonic() < deadline:
                    assert manual.poll() is None, "Manual server failed to start"
                    time.sleep(0.1)
                assert launcher.start(open_browser=False).reused is True
                assert not launcher.owns_server()
                manual.terminate()
                manual.wait(timeout=15)
                assert launcher.ensure().reused is False
                assert launcher.owns_server() and launcher.probe() == "ready"
                assert pid(launcher) != manual.pid
                print("PASS reused manual server exits; recovery creates independent Next server", flush=True)
            finally:
                if manual.poll() is None:
                    manual.terminate()
                    manual.wait(timeout=15)
                launcher.stop()
                launcher.open_page = original_open_page

        class OtherApp(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"A different local application")

            def log_message(self, *args):
                pass

        other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OtherApp)
        worker = threading.Thread(target=other.serve_forever, daemon=True)
        worker.start()
        conflict = Launcher(PROJECT, directory / "conflict", directory / "logs", port=other.server_port)
        try:
            try:
                conflict.start(open_browser=False)
                raise AssertionError("Should reject occupied port")
            except LauncherError as error:
                assert "占用" in str(error)
            assert worker.is_alive() and not conflict.owns_server()
            print("PASS unrelated service remains running on occupied port", flush=True)
        finally:
            other.shutdown()
            other.server_close()

        broken = directory / "broken project"
        (broken / ".venv/bin").mkdir(parents=True)
        (broken / ".venv/bin/python").symlink_to(PROJECT / ".venv/bin/python")
        shutil.copytree(PROJECT / "next_app/static", broken / "next_app/static")
        failure = Launcher(broken, directory / "failure", directory / "failure-logs", port=free_port())
        try:
            try:
                failure.start(open_browser=False)
                raise AssertionError("Should reject a broken app")
            except LauncherError as error:
                assert "没有在规定时间" in str(error)
            assert not failure.owns_server()
            assert failure.log_file.exists() and failure.log_file.stat().st_size > 0
            print("PASS startup failure reports error, preserves log, and releases lock", flush=True)
        finally:
            failure.stop()


if __name__ == "__main__":
    main()
