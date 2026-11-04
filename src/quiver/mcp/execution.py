"""Bounded, cancellable execution sessions for MCP clients."""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from quiver.docker.backend import DockerBackend, DockerError

logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    """Raised when an execution session cannot be addressed or controlled."""


Channel = Literal["stdout", "stderr", "terminal"]
AuditWriter = Callable[[str, list[str] | str, datetime, datetime, int, str, str], None]


@dataclass(frozen=True)
class OutputChunk:
    """A bounded piece of output and its monotonic cursor."""

    cursor: int
    channel: Channel
    data: str


@dataclass(frozen=True)
class ExecutionView:
    """Safe execution state returned to an MCP caller."""

    execution_id: str
    workspace: str
    tty: bool
    running: bool
    exit_code: int | None
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class ExecutionRead:
    """Output read result with a cursor for the next read."""

    execution: ExecutionView
    chunks: tuple[OutputChunk, ...]
    next_cursor: int
    truncated: bool


@dataclass
class _Session:
    execution_id: str
    workspace: str
    process: subprocess.Popen[bytes]
    tty: bool
    started_at: datetime
    master_fd: int | None = None
    next_cursor: int = 0
    chunks: deque[OutputChunk] | None = None
    output_bytes: int = 0
    ended_at: datetime | None = None
    exit_code: int | None = None
    truncated_before: int = 0
    last_activity: float = 0.0
    command: list[str] | str = field(default_factory=list)
    shell: bool = False
    reader_threads: list[threading.Thread] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def view_running(self) -> bool:
        return self.exit_code is None and self.process.poll() is None


class ExecutionBroker:
    """Own Docker exec processes and expose bounded session operations.

    The broker is deliberately independent of MCP.  Its small interface is the
    seam used by the MCP adapter and can be exercised without a protocol client.
    Each session has reader threads so a noisy stderr pipe cannot deadlock a
    command, and a bounded deque so an agent cannot make the server grow memory
    without limit.
    """

    def __init__(
        self,
        docker: DockerBackend,
        *,
        max_output_bytes: int = 4 * 1024 * 1024,
        max_sessions: int = 32,
        default_idle_seconds: float | None = 900,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        if max_output_bytes <= 0 or max_sessions <= 0:
            raise ValueError("execution limits must be positive")
        self.docker = docker
        self.max_output_bytes = max_output_bytes
        self.max_sessions = max_sessions
        self.default_idle_seconds = default_idle_seconds
        self.audit_writer = audit_writer
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def start(
        self,
        workspace: str,
        container: str,
        command: list[str] | str,
        *,
        tty: bool = False,
        shell: bool = False,
        user: str | None = None,
        workdir: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> ExecutionView:
        with self._lock:
            self._reap_finished()
            if sum(session.view_running() for session in self._sessions.values()) >= self.max_sessions:
                raise ExecutionError("maximum concurrent execution sessions reached")

            master_fd: int | None = None
            slave_fd: int | None = None
            if tty:
                master_fd, slave_fd = pty.openpty()
                stdin = slave_fd
                stdout = slave_fd
                stderr = slave_fd
            else:
                stdin = subprocess.PIPE
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE
            try:
                process = self.docker.start_exec(
                    container,
                    command,
                    tty=tty,
                    shell=shell,
                    user=user,
                    workdir=workdir,
                    environment=environment,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                )
            except (DockerError, OSError) as error:
                if master_fd is not None:
                    os.close(master_fd)
                if slave_fd is not None:
                    os.close(slave_fd)
                raise ExecutionError(str(error)) from error
            finally:
                if slave_fd is not None:
                    os.close(slave_fd)

            session = _Session(
                execution_id=uuid.uuid4().hex,
                workspace=workspace,
                process=process,
                tty=tty,
                started_at=datetime.now(UTC),
                last_activity=time.monotonic(),
                command=command,
                shell=shell,
                master_fd=master_fd,
                chunks=deque(),
            )
            self._sessions[session.execution_id] = session
            if tty:
                assert master_fd is not None
                reader = threading.Thread(
                    target=self._read_fd,
                    args=(session, master_fd, "terminal"),
                    name=f"quiver-exec-{session.execution_id}-pty",
                    daemon=True,
                )
                session.reader_threads.append(reader)
                reader.start()
            else:
                assert process.stdout is not None and process.stderr is not None
                for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    reader = threading.Thread(
                        target=self._read_stream,
                        args=(session, stream, channel),
                        name=f"quiver-exec-{session.execution_id}-{channel}",
                        daemon=True,
                    )
                    session.reader_threads.append(reader)
                    reader.start()
            if self.default_idle_seconds is not None:
                threading.Thread(
                    target=self._watch_idle,
                    args=(session,),
                    name=f"quiver-exec-{session.execution_id}-idle",
                    daemon=True,
                ).start()
            threading.Thread(
                target=self._wait_for_exit,
                args=(session,),
                name=f"quiver-exec-{session.execution_id}-wait",
                daemon=True,
            ).start()
            return self.view(session)

    def status(self, execution_id: str) -> ExecutionView:
        """Return the current state without consuming output."""
        return self.view(self._get(execution_id))

    def read(self, execution_id: str, cursor: int = 0, max_bytes: int = 64 * 1024) -> ExecutionRead:
        if cursor < 0 or max_bytes <= 0:
            raise ExecutionError("cursor must be non-negative and max_bytes must be positive")
        session = self._get(execution_id)
        with session.lock:
            session.last_activity = time.monotonic()
            if cursor < session.truncated_before:
                raise ExecutionError("requested output cursor has expired")
            remaining = max_bytes
            selected: list[OutputChunk] = []
            for chunk in session.chunks or ():
                if remaining <= 0:
                    continue
                chunk_start = chunk.cursor
                chunk_bytes = chunk.data.encode()
                chunk_end = chunk_start + len(chunk_bytes)
                if chunk_end <= cursor:
                    continue
                offset = max(0, cursor - chunk_start)
                data_bytes = chunk_bytes[offset : offset + remaining]
                data = data_bytes.decode(errors="replace")
                selected.append(OutputChunk(max(chunk_start, cursor), chunk.channel, data))
                remaining -= len(data_bytes)
            next_cursor = selected[-1].cursor + len(selected[-1].data.encode()) if selected else cursor
            return ExecutionRead(self.view(session), tuple(selected), next_cursor, session.truncated_before > cursor)

    def write(self, execution_id: str, data: str) -> None:
        session = self._get(execution_id)
        with session.lock:
            session.last_activity = time.monotonic()
        if not session.tty and session.process.stdin is None:
            raise ExecutionError("execution stdin is unavailable")
        if not session.view_running():
            raise ExecutionError("execution is not running")
        try:
            if session.tty:
                assert session.master_fd is not None
                os.write(session.master_fd, data.encode())
            else:
                assert session.process.stdin is not None
                session.process.stdin.write(data.encode())
                session.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ExecutionError(f"could not write execution input: {error}") from error

    def resize(self, execution_id: str, rows: int, columns: int) -> None:
        if rows <= 0 or columns <= 0:
            raise ExecutionError("terminal dimensions must be positive")
        session = self._get(execution_id)
        with session.lock:
            session.last_activity = time.monotonic()
        if not session.tty or session.master_fd is None:
            raise ExecutionError("execution does not have a PTY")
        try:
            termios.tcsetwinsize(session.master_fd, (rows, columns))
        except AttributeError:
            winsize = struct.pack("HHHH", rows, columns, 0, 0)
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as error:
            raise ExecutionError(f"could not resize PTY: {error}") from error

    def signal(self, execution_id: str, signum: int = signal.SIGTERM) -> None:
        session = self._get(execution_id)
        with session.lock:
            session.last_activity = time.monotonic()
        if not session.view_running():
            return
        try:
            os.killpg(session.process.pid, signum)
        except ProcessLookupError:
            return
        except OSError as error:
            raise ExecutionError(f"could not signal execution: {error}") from error

    def close(self, execution_id: str, *, force: bool = False) -> ExecutionView:
        session = self._get(execution_id)
        if session.view_running():
            self.signal(execution_id, signal.SIGKILL if force else signal.SIGTERM)
            try:
                session.process.wait(timeout=2 if not force else 0.5)
            except subprocess.TimeoutExpired:
                self.signal(execution_id, signal.SIGKILL)
                session.process.wait(timeout=2)
        return self.view(session)

    def terminate_workspace(self, workspace: str) -> tuple[str, ...]:
        with self._lock:
            affected = tuple(session.execution_id for session in self._sessions.values() if session.workspace == workspace)
        for execution_id in affected:
            self.close(execution_id)
        return affected

    def view(self, session: _Session) -> ExecutionView:
        with session.lock:
            return ExecutionView(
                execution_id=session.execution_id,
                workspace=session.workspace,
                tty=session.tty,
                running=session.view_running(),
                exit_code=session.exit_code,
                started_at=session.started_at.isoformat(),
                ended_at=session.ended_at.isoformat() if session.ended_at else None,
            )

    def _get(self, execution_id: str) -> _Session:
        with self._lock:
            try:
                return self._sessions[execution_id]
            except KeyError as error:
                raise ExecutionError(f"unknown execution {execution_id!r}") from error

    def _read_stream(self, session: _Session, stream: object, channel: Channel) -> None:
        while True:
            data = stream.read(65536)  # type: ignore[attr-defined]
            if not data:
                return
            self._append(session, channel, data)

    def _read_fd(self, session: _Session, fd: int, channel: Channel) -> None:
        try:
            while True:
                try:
                    data = os.read(fd, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        return
                    raise
                if not data:
                    return
                self._append(session, channel, data)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _append(self, session: _Session, channel: Channel, data: bytes) -> None:
        text = data.decode(errors="replace")
        with session.lock:
            assert session.chunks is not None
            chunk = OutputChunk(session.next_cursor, channel, text)
            session.chunks.append(chunk)
            session.next_cursor += len(data)
            session.output_bytes += len(data)
            while session.output_bytes > self.max_output_bytes and session.chunks:
                removed = session.chunks.popleft()
                removed_bytes = len(removed.data.encode())
                session.output_bytes -= removed_bytes
                session.truncated_before = removed.cursor + removed_bytes

    def _wait_for_exit(self, session: _Session) -> None:
        code = session.process.wait()
        for reader in session.reader_threads:
            reader.join(timeout=2)
        with session.lock:
            session.exit_code = code
            session.ended_at = datetime.now(UTC)
            chunks = tuple(session.chunks or ())
            stdout = "".join(chunk.data for chunk in chunks if chunk.channel in {"stdout", "terminal"})
            stderr = "".join(chunk.data for chunk in chunks if chunk.channel == "stderr")
            ended_at = session.ended_at
        if self.audit_writer is not None:
            try:
                self.audit_writer(
                    session.workspace,
                    session.command,
                    session.started_at,
                    ended_at,
                    code,
                    stdout,
                    stderr,
                )
            except Exception:
                # Execution completion must not be lost because audit storage failed.
                logger.exception("could not persist MCP execution audit artifact")

    def _watch_idle(self, session: _Session) -> None:
        assert self.default_idle_seconds is not None
        while session.view_running():
            time.sleep(min(self.default_idle_seconds, 1.0))
            with session.lock:
                idle = time.monotonic() - session.last_activity
            if session.view_running() and idle >= self.default_idle_seconds:
                try:
                    self.close(session.execution_id)
                except ExecutionError:
                    pass
                return

    def _reap_finished(self) -> None:
        """Keep completed handles readable; only running sessions count toward the cap."""
        return

