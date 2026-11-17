import io
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from tests.integration.rest_api import support


def test_port_forward_service_uses_https_and_stops_on_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = None
    process.stderr = io.StringIO()
    commands = []

    def start(command: list[str], **kwargs: object) -> Mock:
        commands.append(command)
        return process

    monkeypatch.setattr(support.subprocess, "Popen", start)
    monkeypatch.setattr(
        support.socket, "create_connection", lambda *args, **kwargs: nullcontext()
    )

    with support.port_forward_service("test-namespace", "registry-rest") as url:
        assert url.startswith("https://127.0.0.1:")
        assert commands == [
            [
                "kubectl",
                "port-forward",
                "--address=127.0.0.1",
                "service/registry-rest",
                f"{url.rsplit(':', 1)[1]}:443",
                "-n",
                "test-namespace",
            ]
        ]

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert process.stderr.closed


def test_port_forward_service_reports_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = 1
    process.stderr = io.StringIO("service unavailable")
    monkeypatch.setattr(support.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(RuntimeError, match="service unavailable"):
        with support.port_forward_service("test-namespace", "registry-rest"):
            pass

    process.terminate.assert_not_called()
    assert process.stderr.closed
