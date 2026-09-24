"""`WORKSHOP_APP_BACKEND` decides how apps run. `auto` asks whether docker is
reachable; the other two values pin the answer, so an installation whose apps
run off the host filesystem is not moved into containers the day docker
becomes reachable."""
import pytest

from services import app_runner, container_runtime


@pytest.mark.parametrize("docker_up", [True, False])
def test_subprocess_and_container_pin_the_backend(monkeypatch, docker_up):
    monkeypatch.setattr(container_runtime, "available", lambda *a, **k: docker_up)
    monkeypatch.setenv("WORKSHOP_APP_BACKEND", "subprocess")
    assert app_runner._use_containers() is False
    monkeypatch.setenv("WORKSHOP_APP_BACKEND", "container")
    assert app_runner._use_containers() is True


@pytest.mark.parametrize("docker_up", [True, False])
def test_auto_and_unset_follow_docker(monkeypatch, docker_up):
    monkeypatch.setattr(container_runtime, "available", lambda *a, **k: docker_up)
    monkeypatch.delenv("WORKSHOP_APP_BACKEND", raising=False)
    assert app_runner._use_containers() is docker_up
    monkeypatch.setenv("WORKSHOP_APP_BACKEND", "auto")
    assert app_runner._use_containers() is docker_up
