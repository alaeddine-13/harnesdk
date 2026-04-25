"""
agent.py
~~~~~~~~~~~~~~~~
Harness-agnostic abstraction for running AI agent CLIs inside E2B sandboxes.

This module defines :class:`AgentSession`, an **abstract base class** that
implements the common sandbox lifecycle, command execution, and streaming
orchestration shared by every supported harness.  Concrete subclasses live in
sibling modules:

* :class:`harnesdk.claude_agent.ClaudeAgentSession` – Anthropic *Claude Code*
* :class:`harnesdk.hermes_agent.HermesAgentSession` – Nous Research *Hermes Agent*

Each subclass plugs in harness-specific behavior (environment variables,
system-prompt file, skill installer, MCP registration, command-line flags,
output parser) through a small set of hooks while reusing the lifecycle,
``run`` / ``stream`` orchestration, and data types defined here.

Usage example::

    import asyncio
    from harnesdk import ClaudeAgentSession

    async def main():
        async with ClaudeAgentSession() as session:
            result = await session.run("Create a hello world HTTP server in Go")
            print(result.output)

            async for chunk in session.stream("Add unit tests"):
                print(chunk, end="", flush=True)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import os
import shlex
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from e2b import AsyncSandbox

from harnesdk.logging_utils import build_logger

LOGGER = build_logger("harnesdk.agent")

# ---------------------------------------------------------------------------
# MCP server type
# ---------------------------------------------------------------------------

@dataclass
class McpServer:
    """An MCP server to register with the agent before running.

    Supports both **HTTP/SSE** transports (``url``) and **stdio** transports
    (``command`` + ``args``).  Which fields are used depends on the harness
    and the ``transport`` selected.

    Attributes:
        name:       Identifier for this MCP server.
        url:        Endpoint URL (for HTTP/SSE transports).
        transport:  Transport protocol (``"http"``, ``"sse"``, ``"stdio"``).
                    Defaults to ``"http"``.
        scope:      Registration scope – used by Claude Code's ``claude mcp
                    add -s`` flag.  Harnesses that don't have a scope concept
                    ignore this value.
        headers:    Optional HTTP headers (typically for ``Authorization``).
        command:    Executable name for stdio MCP servers.
        args:       Arguments to pass to ``command``.
        env:        Environment variables to forward to the MCP process.
    """
    name: str
    url: str | None = None
    transport: str = "http"
    scope: str = "user"
    headers: dict[str, str] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Skill type
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    """A skill to install in the sandbox before running the agent.

    Two installation patterns are supported:

    * **Named skill** – installs from the registry by name::

        Skill(name="commit")

    * **URL skill** – installs from a Git (or other) URL::

        Skill(name="customer-research",
              url="https://github.com/coreyhaines31/marketingskills")

    The concrete command executed differs per harness (see each subclass).
    """
    name: str = "*"
    url: str | None = None


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """The outcome of a completed agent run.

    Attributes:
        output:      Final text response from the agent.
        session_id:  Conversation session ID, if the harness exposes one.
        raw_events:  Parsed structured events (e.g. JSONL) emitted during a
                     streaming run.  Empty for harnesses that stream plain
                     text or for non-streaming runs.
        exit_code:   Process exit code from the sandbox command.
    """
    output: str
    session_id: str | None = None
    raw_events: list[dict] = field(default_factory=list)
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Stream processor
# ---------------------------------------------------------------------------

class StreamProcessor:
    """Incrementally consumes stdout from an agent CLI.

    The default implementation simply passes text through unchanged.  Harnesses
    with a structured output format (e.g. Claude Code's JSONL) provide their
    own subclass that buffers lines, parses them, and extracts text fragments
    plus metadata such as the session id.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def feed(self, data: str) -> list[str]:
        """Consume a chunk of stdout and return text to emit to the caller."""
        return [data] if data else []

    def flush(self) -> list[str]:
        """Emit any buffered text after the process exits."""
        return []

    @property
    def session_id(self) -> str | None:
        """Session id observed so far, if any."""
        return None


# ---------------------------------------------------------------------------
# Abstract session
# ---------------------------------------------------------------------------

class AgentSession(ABC):
    """Abstract base class managing an AI agent CLI inside an E2B sandbox.

    Subclasses implement :meth:`_env_vars` and :meth:`_build_command` at
    minimum, and may override the lifecycle hooks (:meth:`_setup_system_prompt`,
    :meth:`_install_skills`, :meth:`_register_mcps`) and the output parsers
    (:meth:`_parse_output`, :meth:`_make_stream_processor`) to fit the
    particular harness.

    Args:
        template:         E2B sandbox template name.  Falls back to the
                          subclass's :attr:`default_template` when ``None``.
        timeout:          Sandbox inactivity timeout in seconds
                          (default: 300 – 5 minutes).
        log_level:        Streaming verbosity. ``"default"`` emits only parsed
                          assistant text; ``"all"`` emits every raw stdout
                          line produced by the agent process.
        system_prompt:    Optional instruction block.  Each subclass decides
                          how to materialize it inside the sandbox (e.g.
                          ``CLAUDE.md`` for Claude Code, ``AGENTS.md`` for
                          Hermes).
        working_dir:      Working directory used for every command executed
                          inside the sandbox (default: ``/home/user``).
        skills:           Optional list of :class:`Skill` instances (or plain
                          strings for simple named skills) to install during
                          sandbox setup.
        mcps:             Optional list of :class:`McpServer` instances to
                          register during sandbox setup.
        env:              Optional environment variables to inject into the
                          sandbox in addition to harness defaults.
        upload_files:     Optional mapping of local host file paths to target
                          sandbox file paths. Each file is uploaded during
                          :meth:`open` before prompt/skill/MCP setup.

    Example::

        async with ClaudeAgentSession() as session:
            result = await session.run("Write a fizzbuzz in Rust")
            print(result.output)
    """

    #: Default sandbox template name for this harness.  Subclasses override.
    default_template: ClassVar[str] = ""

    def __init__(
        self,
        *,
        template: str | None = None,
        timeout: int = 300,
        log_level: Literal["default", "all"] = "default",
        system_prompt: str | None = None,
        working_dir: str = "/home/user",
        skills: list[Skill | str] | None = None,
        mcps: list[McpServer] | None = None,
        env: dict[str, str] | None = None,
        upload_files: dict[str, str] | None = None,
    ) -> None:
        self.template = template or self.default_template
        if not self.template:
            raise ValueError(
                f"{type(self).__name__} must set `default_template` or "
                f"receive a `template` argument."
            )
        self.timeout = timeout
        if log_level not in ("default", "all"):
            raise ValueError("log_level must be either 'default' or 'all'.")
        self.log_level: Literal["default", "all"] = log_level
        self.system_prompt = system_prompt
        self.working_dir = working_dir
        self.skills: list[Skill] = [
            Skill(name=s) if isinstance(s, str) else s
            for s in (skills or [])
        ]
        self.mcps: list[McpServer] = list(mcps or [])
        self.env: dict[str, str] = dict(env or {})
        self.upload_files: dict[str, str] = dict(upload_files or {})

        self.sandbox: AsyncSandbox | None = None
        self._session_id: str | None = None

    # ------------------------------------------------------------------
    # Abstract hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _env_vars(self) -> dict[str, str]:
        """Environment variables (API keys, etc.) to inject into the sandbox."""

    @abstractmethod
    def _build_command(self, prompt: str, *, streaming: bool) -> str:
        """Shell command that runs the agent CLI with *prompt*.

        Implementations should include any resume/continue flag needed to
        preserve multi-turn state, using :attr:`_session_id` if appropriate.
        """

    # ------------------------------------------------------------------
    # Overridable hooks (default: no-op)
    # ------------------------------------------------------------------

    async def _setup_system_prompt(self) -> None:  # noqa: B027
        """Write the system prompt somewhere the agent will pick it up.

        Default: no-op.  Subclasses override to write the appropriate file.
        """

    async def _install_skills(self) -> None:  # noqa: B027
        """Install every :class:`Skill` in :attr:`skills` into the sandbox.

        Default: no-op.  Subclasses override with their harness-specific
        installer.
        """

    async def _register_mcps(self) -> None:  # noqa: B027
        """Register every :class:`McpServer` in :attr:`mcps` with the agent.

        Default: no-op.  Subclasses override to invoke the appropriate CLI or
        write the appropriate config file.
        """

    async def _upload_files(self) -> None:
        """Upload host files specified in :attr:`upload_files` to sandbox."""
        if not self.upload_files:
            return

        for local_path, target_path in self.upload_files.items():
            target_dir = os.path.dirname(target_path)
            if target_dir:
                await self.sandbox.commands.run(  # type: ignore[union-attr]
                    f"mkdir -p {shlex.quote(target_dir)}",
                    cwd=self.working_dir,
                )
            with open(local_path, "rb") as file_obj:
                await self.sandbox.files.write(  # type: ignore[union-attr]
                    target_path,
                    file_obj,
                )

    # ------------------------------------------------------------------
    # Output parsing hooks
    # ------------------------------------------------------------------

    def _parse_output(self, stdout: str, exit_code: int) -> AgentResult:
        """Convert the non-streaming ``stdout`` into an :class:`AgentResult`.

        The default implementation returns the raw stdout as the output with
        no session id.  Subclasses with a structured output format should
        override this.
        """
        return AgentResult(output=stdout.strip(), exit_code=exit_code)

    def _make_stream_processor(self) -> StreamProcessor:
        """Return a fresh :class:`StreamProcessor` for a streaming run.

        The default pass-through processor is suitable for harnesses that
        stream plain text to stdout.
        """
        return StreamProcessor()

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AgentSession:
        await self.open()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Create and configure the underlying E2B sandbox.

        Called automatically by the async context manager.  Call manually only
        when you need explicit lifecycle control.
        """
        if self.sandbox is not None:
            LOGGER.debug("%s.open() skipped (already open).", type(self).__name__)
            return

        LOGGER.info(
            "Opening %s sandbox (template=%s, timeout=%ss).",
            type(self).__name__,
            self.template,
            self.timeout,
        )
        envs = self._env_vars()
        envs.update(self.env)
        self.sandbox = await AsyncSandbox.create(
            self.template,
            envs=envs,
            timeout=self.timeout,
        )

        await self._upload_files()
        await self._setup_system_prompt()
        await self._install_skills()
        await self._register_mcps()
        LOGGER.info("Sandbox ready for %s.", type(self).__name__)

    async def close(self) -> None:
        """Terminate the sandbox and free all resources."""
        if self.sandbox is not None:
            LOGGER.info("Closing sandbox for %s.", type(self).__name__)
            await self.sandbox.kill()
            self.sandbox = None
            self._session_id = None
        else:
            LOGGER.debug("%s.close() skipped (already closed).", type(self).__name__)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, prompt: str) -> AgentResult:
        """Execute the agent with *prompt* and wait for it to finish.

        Args:
            prompt:  Task description to hand to the agent.

        Returns:
            An :class:`AgentResult` with the final text and metadata.

        Raises:
            RuntimeError: If :meth:`open` hasn't been called yet.
        """
        self._require_open()
        cmd = self._build_command(prompt, streaming=False)
        LOGGER.info("Running non-streaming command for %s.", type(self).__name__)
        LOGGER.debug("Command: %s", cmd)
        # TODO: this actually will timeout when the agent runs a background server
        result = await self.sandbox.commands.run(  # type: ignore[union-attr]
            cmd,
            cwd=self.working_dir,
            timeout=self.timeout,
        )

        agent_result = self._parse_output(result.stdout, result.exit_code)
        LOGGER.info("Run completed with exit_code=%s.", result.exit_code)
        if agent_result.session_id:
            self._session_id = agent_result.session_id
            LOGGER.debug("Updated session_id=%s", self._session_id)
        return agent_result

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """Execute the agent and yield text chunks as they arrive.

        Args:
            prompt:  Task description to hand to the agent.

        Yields:
            Text fragments in roughly the order the agent produces them.

        Example::

            async for chunk in session.stream("Refactor the auth module"):
                print(chunk, end="", flush=True)
        """
        self._require_open()
        cmd = self._build_command(prompt, streaming=True)
        LOGGER.info("Running streaming command for %s.", type(self).__name__)
        LOGGER.debug("Command: %s", cmd)

        processor = self._make_stream_processor()
        out_queue: asyncio.Queue[str | object] = asyncio.Queue()
        stream_end = object()

        async def _on_stdout(data: str) -> None:
            for chunk in processor.feed(data):
                if chunk:
                    await out_queue.put(chunk)

        async def _run_command() -> None:
            try:
                # TODO: this actually will timeout when the agent runs a background server
                await self.sandbox.commands.run(  # type: ignore[union-attr]
                    cmd,
                    cwd=self.working_dir,
                    on_stdout=_on_stdout,
                    timeout=self.timeout,
                )
                for chunk in processor.flush():
                    if chunk:
                        await out_queue.put(chunk)
                LOGGER.info("Streaming command completed.")
            finally:
                await out_queue.put(stream_end)

        run_task = asyncio.create_task(_run_command())
        try:
            while True:
                item = await out_queue.get()
                if item is stream_end:
                    break
                yield str(item)
        finally:
            await run_task

        session_id = processor.session_id
        if session_id:
            self._session_id = session_id
            LOGGER.debug("Updated session_id=%s", self._session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_open(self) -> None:
        if self.sandbox is None:
            raise RuntimeError(
                f"{type(self).__name__} is not open.  Use it as an async "
                "context manager or call `await session.open()` first."
            )
