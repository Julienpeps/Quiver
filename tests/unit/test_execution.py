import subprocess
import sys
import time
from typing import Any

import pytest

from quiver.docker.backend import DockerBackend
from quiver.mcp.execution import ExecutionBroker, ExecutionError


def fake_process_runner(_args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        **kwargs,
    )


def wait_for_exit(broker: ExecutionBroker, execution_id: str) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not broker.status(execution_id).running:
            return
        time.sleep(0.01)
    pytest.fail("execution did not exit")


def test_broker_reads_both_non_pty_streams_with_a_cursor() -> None:
    broker = ExecutionBroker(
        DockerBackend(process_runner=fake_process_runner), max_output_bytes=1024
    )
    execution = broker.start("demo", "quiver-demo", ["ignored"])
    wait_for_exit(broker, execution.execution_id)

    result = broker.read(execution.execution_id, max_bytes=4)
    assert len(result.chunks) == 1
    assert result.chunks[0].data in {"out\n", "err\n"}
    assert result.next_cursor == 4

    remainder = broker.read(execution.execution_id, cursor=result.next_cursor)
    assert len(remainder.chunks) == 1
    assert remainder.chunks[0].data in {"out\n", "err\n"}
    assert remainder.chunks[0].data != result.chunks[0].data
    assert remainder.execution.exit_code == 0


def test_broker_can_resume_in_the_middle_of_a_chunk() -> None:
    broker = ExecutionBroker(
        DockerBackend(process_runner=fake_process_runner), max_output_bytes=1024
    )
    execution = broker.start("demo", "quiver-demo", ["ignored"])
    wait_for_exit(broker, execution.execution_id)

    first = broker.read(execution.execution_id, max_bytes=2)
    second = broker.read(execution.execution_id, cursor=first.next_cursor, max_bytes=2)
    assert first.chunks[0].data in {"ou", "er"}
    assert second.chunks[0].data in {"t\n", "r\n"}


def test_broker_reports_expired_output_cursor() -> None:
    broker = ExecutionBroker(
        DockerBackend(process_runner=fake_process_runner), max_output_bytes=3
    )
    execution = broker.start("demo", "quiver-demo", ["ignored"])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and broker.status(execution.execution_id).running:
        time.sleep(0.01)

    with pytest.raises(ExecutionError, match="cursor has expired"):
        broker.read(execution.execution_id, cursor=0)


def test_broker_rejects_writes_to_completed_execution() -> None:
    broker = ExecutionBroker(DockerBackend(process_runner=fake_process_runner))
    execution = broker.start("demo", "quiver-demo", ["ignored"])
    wait_for_exit(broker, execution.execution_id)

    with pytest.raises(ExecutionError, match="not running"):
        broker.write(execution.execution_id, "input")
