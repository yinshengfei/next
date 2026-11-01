import errno
import os
import subprocess

import pytest

from macos import launcher as desktop


@pytest.fixture
def launcher(tmp_path):
    project = tmp_path / "project with spaces"
    assets = project / "next_app/static"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("Next test page")
    (assets / "app.js").write_text("// Next test script")
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("test executable")
    python.chmod(0o700)
    return desktop.Launcher(project, tmp_path / "runtime", tmp_path / "logs", port=18765)


def test_existing_next_is_opened_without_starting_a_process(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda: "ready")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No new server"))
    opened = []
    monkeypatch.setattr(launcher, "open_page", lambda: opened.append(launcher.url))
    assert launcher.start().reused is True
    assert opened == ["http://127.0.0.1:18765"]


def test_background_check_does_not_open_browser_for_healthy_server(launcher, monkeypatch):
    requests = []
    monkeypatch.setattr(launcher, "start", lambda **kwargs: requests.append(kwargs) or desktop.StartResult(launcher.url, True))
    monkeypatch.setattr(launcher, "open_page", lambda: pytest.fail("Do not repeatedly open tabs"))
    assert launcher.ensure().reused is True
    assert requests == [{"open_browser": False}]


def test_background_check_opens_page_once_after_recovery(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "start", lambda **kwargs: desktop.StartResult(launcher.url, False))
    opened = []
    monkeypatch.setattr(launcher, "open_page", lambda: opened.append(launcher.url))
    assert launcher.ensure().reused is False
    assert opened == [launcher.url]


def test_occupied_port_is_not_opened_or_terminated(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda: "occupied")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No process on port conflict"))
    monkeypatch.setattr(launcher, "stop_owned", lambda: pytest.fail("Do not stop anything on port conflict"))
    with pytest.raises(desktop.LauncherError, match="占用"):
        launcher.start()


def test_cold_start_is_detached_and_repeat_start_reuses_server(launcher, monkeypatch):
    states = iter(["free", "free", "ready", "ready"])
    monkeypatch.setattr(launcher, "probe", lambda: next(states))
    monkeypatch.setattr(launcher, "owns_server", lambda: False)
    calls = []

    class Process:
        def poll(self):
            return None

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(desktop.subprocess, "Popen", spawn)
    assert launcher.start(open_browser=False).reused is False
    assert launcher.start(open_browser=False).reused is True
    assert len(calls) == 1
    command, options = calls[0]
    assert command[0] == str(launcher.python) and "_serve" in command
    assert options["start_new_session"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["cwd"] == launcher.project
    server = launcher.server_command()
    assert server[server.index("--host") + 1] == "127.0.0.1"
    assert server[server.index("--port") + 1] == "18765"


def test_shutdown_leaves_manual_server_and_stale_pid_alone(launcher, monkeypatch):
    launcher.prepare()
    launcher.state_file.write_text('{"server_pid": 1}')
    monkeypatch.setattr(launcher, "owns_server", lambda: False)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("A PID file must never cause a kill"))
    launcher.stop()


def test_shutdown_requests_our_supervisor_through_private_fifo(launcher, monkeypatch):
    launcher.prepare()
    os.mkfifo(launcher.control, 0o600)
    reader = os.open(launcher.control, os.O_RDWR | os.O_NONBLOCK)
    owner = iter([True, False])
    monkeypatch.setattr(launcher, "owns_server", lambda: next(owner))
    try:
        launcher.stop()
        assert os.read(reader, 100) == b"stop\n"
    finally:
        os.close(reader)


def test_shutdown_refuses_to_overwrite_an_unexpected_control_file(launcher, monkeypatch):
    launcher.prepare()
    launcher.control.write_text("not a pipe")
    monkeypatch.setattr(launcher, "owns_server", lambda: True)
    with pytest.raises(desktop.LauncherError, match="控制文件异常"):
        launcher.stop()
    assert launcher.control.read_text() == "not a pipe"


def test_failed_start_is_reported_without_signalling_any_pid(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda: "free")
    monkeypatch.setattr(launcher, "owns_server", lambda: False)

    class Process:
        def poll(self):
            return 1

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Never kill a guessed PID"))
    with pytest.raises(desktop.LauncherError, match="没有在规定时间"):
        launcher.start(open_browser=False)


def test_missing_virtual_environment_is_actionable(launcher):
    launcher.python.unlink()
    with pytest.raises(desktop.LauncherError, match="虚拟环境"):
        launcher.start()


def test_probe_checks_actual_assets_without_api_status(launcher, monkeypatch):
    requests = []

    class Response:
        status = 200

        def __init__(self, content):
            self.content = content

        def read(self, limit):
            return self.content

        def getheader(self, name):
            return "nosniff"

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, route):
            requests.append(route)

        def getresponse(self):
            return Response(b"Next test page" if len(requests) == 1 else b"// Next test script")

        def close(self):
            pass

    monkeypatch.setattr(desktop.http.client, "HTTPConnection", Connection)
    assert launcher.probe() == "ready"
    assert requests == ["/", "/static/app.js"]


@pytest.mark.parametrize("error,expected", [
    (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), "free"),
    (TimeoutError("timeout"), "occupied"),
])
def test_other_service_is_not_assumed_free(launcher, monkeypatch, error, expected):
    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise error

        def close(self):
            pass

    monkeypatch.setattr(desktop.http.client, "HTTPConnection", Connection)
    assert launcher.probe() == expected


def test_port_configuration_is_read_only(tmp_path):
    config = tmp_path / "config.json"
    assert desktop.configured_port(config) == 8765
    config.write_text('{"port": 9876, "model": "unchanged"}')
    original = config.read_bytes()
    assert desktop.configured_port(config) == 9876
    assert config.read_bytes() == original
    config.write_text('{"port": true}')
    with pytest.raises(desktop.LauncherError):
        desktop.configured_port(config)
