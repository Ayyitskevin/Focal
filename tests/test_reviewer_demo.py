"""Safety contract for the disposable reviewer-tour launcher."""

import argparse
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reviewer_demo.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("reviewer_demo_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_environment_scrubs_inherited_integrations(tmp_path, monkeypatch):
    monkeypatch.setenv("MISE_STRIPE_SECRET_KEY", "sk_live_must_not_escape")
    monkeypatch.setenv("MISE_NOTION_TOKEN", "secret-notion-token")
    monkeypatch.setenv("MISE_SAAS_CONTROL_DB_PATH", "/real/control.db")

    env = _load_launcher().demo_environment(tmp_path, 8840)

    assert "MISE_STRIPE_SECRET_KEY" not in env
    assert "MISE_NOTION_TOKEN" not in env
    assert env["MISE_SAAS_CONTROL_DB_PATH"] == str(tmp_path / "saas-control.db")
    assert env["MISE_SAAS_TENANT_DATA_DIR"] == str(tmp_path / "tenants")
    assert env["MISE_ENV_FILE"] == str(tmp_path / "no-env-file")
    assert env["MISE_SAAS_MODE"] == "true"
    assert env["MISE_SAAS_ROOT_DOMAIN"] == "localhost:8840"
    assert env["MISE_HOST"] == "127.0.0.1"
    assert env["MISE_COOKIE_SECURE"] == "false"
    assert len(env["MISE_SECRET_KEY"]) >= 32


@pytest.mark.parametrize("value", ["0", "65536", "-1"])
def test_port_rejects_values_outside_tcp_range(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _load_launcher()._port(value)


def test_main_runs_loopback_and_removes_disposable_state(monkeypatch):
    launcher = _load_launcher()
    observed = {}

    class FakeProcess:
        def wait(self, timeout=None):
            observed["wait_timeout"] = timeout
            return 0

    def fake_popen(command, *, cwd, env, start_new_session):
        observed["command"] = command
        observed["cwd"] = cwd
        observed["env"] = env
        observed["data_dir"] = Path(env["MISE_DATA_DIR"])
        assert observed["data_dir"].is_dir()
        observed["start_new_session"] = start_new_session
        return FakeProcess()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    assert launcher.main(["--port", "8840"]) == 0
    assert observed["command"][-4:] == ["--host", "127.0.0.1", "--port", "8840"]
    assert observed["cwd"] == ROOT
    assert observed["start_new_session"] is True
    assert not observed["data_dir"].exists()
    assert observed["env"]["MISE_BASE_URL"] == "http://localhost:8840"


def test_interrupt_stops_child_before_removing_state(monkeypatch):
    launcher = _load_launcher()
    observed = {"waits": []}

    class InterruptedProcess:
        def wait(self, timeout=None):
            observed["waits"].append(timeout)
            if len(observed["waits"]) == 1:
                raise KeyboardInterrupt
            return 0

        def send_signal(self, sig):
            observed["signal"] = sig

        def terminate(self):
            observed["terminated"] = True

    def fake_popen(command, *, cwd, env, start_new_session):
        del command, cwd, start_new_session
        observed["data_dir"] = Path(env["MISE_DATA_DIR"])
        return InterruptedProcess()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    assert launcher.main(["--port", "8840"]) == 130
    assert observed["signal"] == launcher.signal.SIGINT
    assert observed["waits"] == [None, 10]
    assert "terminated" not in observed
    assert not observed["data_dir"].exists()
