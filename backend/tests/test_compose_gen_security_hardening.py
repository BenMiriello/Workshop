"""Test that compose_gen emits hardened security settings for app containers.

Verifies the defense-in-depth security measures from SEC-A6:
- security_opt: ["no-new-privileges:true"] blocks privilege escalation via setuid
- cap_drop: ["ALL"] removes all Linux capabilities
- user: "nobody" runs as non-root

This test asserts that these settings are present in every generated app service,
so a future edit that drops one will fail and prevent regression.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

import pytest
import yaml

from services import compose_gen
from services.app_runner import ServiceSpec, DepSpec


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory with one service."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n")
    return tmp_path


@dataclass
class MockManifest:
    """Minimal manifest for testing compose generation."""
    services: list[ServiceSpec] = field(default_factory=list)
    deps: list[DepSpec] = field(default_factory=list)


def test_compose_gen_emits_security_hardening_for_app_services(project_dir):
    """All generated app services must have security_opt, cap_drop, and non-root user."""
    manifest = MockManifest(
        services=[
            ServiceSpec(
                name="api",
                start="python main.py",
                cwd="src",
                expose=True,
            ),
            ServiceSpec(
                name="worker",
                start="python worker.py",
                cwd="src",
                expose=False,
                depends_on=["api"],
            ),
        ],
    )

    result = compose_gen.generate(
        slug="test-app",
        manifest=manifest,
        project_dir=project_dir,
        host_port=8888,
    )

    # Read the generated compose file
    compose_path = result["compose_path"]
    assert compose_path.exists(), f"Compose file not generated at {compose_path}"

    compose_data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose_data.get("services", {})

    # Both app services should be present
    assert "api" in services, "api service missing from compose"
    assert "worker" in services, "worker service missing from compose"

    # Check each app service has all three security settings
    for service_name in ["api", "worker"]:
        service = services[service_name]

        # Verify security_opt: no-new-privileges
        assert "security_opt" in service, \
            f"{service_name}: security_opt missing"
        assert service["security_opt"] == ["no-new-privileges:true"], \
            f"{service_name}: security_opt incorrect: {service['security_opt']}"

        # Verify cap_drop: ALL
        assert "cap_drop" in service, \
            f"{service_name}: cap_drop missing"
        assert service["cap_drop"] == ["ALL"], \
            f"{service_name}: cap_drop incorrect: {service['cap_drop']}"

        # Verify non-root user
        assert "user" in service, \
            f"{service_name}: user field missing"
        assert service["user"] == "nobody", \
            f"{service_name}: user should be 'nobody', got {service['user']}"


def test_compose_gen_preserves_external_dep_user_if_present(project_dir):
    """External deps that declare a user in their compose file should preserve it."""
    # Create a compose file with an external dep that specifies a user
    deps_dir = project_dir / "infra"
    deps_dir.mkdir()
    deps_file = deps_dir / "compose.yml"
    deps_file.write_text("""
services:
  postgres:
    image: postgres:15
    user: "postgres"
    environment:
      POSTGRES_PASSWORD: secret
""")

    manifest = MockManifest(
        services=[
            ServiceSpec(
                name="api",
                start="python main.py",
                cwd="src",
                expose=True,
            ),
        ],
        deps=[
            DepSpec(
                name="db",
                compose_file="infra/compose.yml",
                compose_service="postgres",
            ),
        ],
    )

    result = compose_gen.generate(
        slug="test-app-with-deps",
        manifest=manifest,
        project_dir=project_dir,
        host_port=8888,
    )

    compose_data = yaml.safe_load(result["compose_path"].read_text(encoding="utf-8"))
    services = compose_data.get("services", {})

    # App service must have the hardening
    api_service = services["api"]
    assert api_service.get("security_opt") == ["no-new-privileges:true"]
    assert api_service.get("cap_drop") == ["ALL"]
    assert api_service.get("user") == "nobody"

    # Ext dep should preserve its original user (from _sanitize_dep_block)
    db_service = services["db"]
    assert db_service.get("user") == "postgres", \
        "External dep user should be preserved from source compose"
