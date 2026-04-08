"""
agent.py
~~~~~~~~~~~~~~~~
A clean Python abstraction for running AI agent harnesses inside E2B sandboxes.

Usage example:

    import asyncio
    from harnesdk.agent import AgentSession

    async def main():
        async with AgentSession() as session:
            result = await session.run("Create a hello world HTTP server in Go")
            print(result.output)

            # Stream output in real time
            async for chunk in session.stream("Add unit tests"):
                print(chunk, end="", flush=True)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

from e2b import AsyncSandbox


# ---------------------------------------------------------------------------
# Public enumerations
# ---------------------------------------------------------------------------

class AgentHarness(str, Enum):
    """Supported agent harnesses.

    New harnesses (e.g. Aider, Devin, OpenHands) can be added here later
    without changing the public API surface.
    """
    CLAUDE_CODE = "claude_code"


class SandboxTemplate(str, Enum):
    """E2B sandbox templates.

    Each harness has a sensible default (see _HARNESS_DEFAULTS), but callers
    can override this when a custom template has been built on top of the base.
    """
    CLAUDE = "claude"


# ---------------------------------------------------------------------------
# Internal defaults
# ---------------------------------------------------------------------------

_HARNESS_DEFAULTS: dict[AgentHarness, SandboxTemplate] = {
    AgentHarness.CLAUDE_CODE: SandboxTemplate.CLAUDE,
}

_HARNESS_ENTRY_POINTS: dict[AgentHarness, str] = {
    AgentHarness.CLAUDE_CODE: "claude",
}

_HARNESS_AGENT_IDS: dict[AgentHarness, str] = {
    AgentHarness.CLAUDE_CODE: "claude-code",
}


# ---------------------------------------------------------------------------
# Skill type
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    """A skill to install in the sandbox before running the agent.

    Two installation patterns are supported:

    * **Named skill** – installs from the registry::

        Skill(name="commit")
        # → npx skill add commit -a <harness-agent-id>

    * **URL skill** – installs from a GitHub (or other) URL::

        Skill(name="customer-research",
              url="https://github.com/coreyhaines31/marketingskills")
        # → npx skills add <url> --skill customer-research
    """
    name: str = "*"
    url: Optional[str] = None


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """The outcome of a completed agent run.

    Attributes:
        output:      Combined stdout text from the agent process.
        session_id:  Conversation session ID returned by Claude Code's JSON
                     output format.  ``None`` for harnesses that don't expose
                     one, or when the output could not be parsed.
        raw_events:  All parsed JSONL event objects emitted during a streaming
                     run.  Empty for non-streaming runs.
        exit_code:   Process exit code from the sandbox command.
    """
    output: str
    session_id: Optional[str] = None
    raw_events: list[dict] = field(default_factory=list)
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Core session class
# ---------------------------------------------------------------------------

class AgentSession:
    """Manages the lifecycle of an agent harness running inside an E2B sandbox.

    The session owns the sandbox: it creates it on ``__aenter__`` and kills it
    on ``__aexit__``.  This means you should always use it as an async context
    manager unless you call ``open()`` / ``close()`` manually.

    Args:
        harness:          Which agent harness to run (default: ``CLAUDE_CODE``).
        template:         E2B sandbox template to use.  Defaults to the
                          harness-specific default when omitted.
        api_key:          Anthropic API key.  Falls back to the
                          ``ANTHROPIC_API_KEY`` environment variable.
        timeout:          Sandbox inactivity timeout
                          (default: 300 — 5 minutes).
        system_prompt:    Optional instruction block written to ``CLAUDE.md``
                          inside the sandbox before the first run.
        working_dir:      Working directory used for all commands executed
                          inside the sandbox (default: ``/home/user``).
        skills:           Optional list of :class:`Skill` instances (or plain
                          strings for simple named skills) to install during
                          sandbox setup.  Named skills use
                          ``npx skill add <name> -a <agent-id>``; URL skills
                          use ``npx skills add <url> --skill <name>``.

    Examples::

        # Simple one-shot run
        async with AgentSession() as session:
            result = await session.run("Write a fizzbuzz in Rust")
            print(result.output)

        # Multi-turn conversation
        async with AgentSession(system_prompt="You write only Go code.") as s:
            r1 = await s.run("Create an HTTP server")
            r2 = await s.run("Add a /healthz endpoint")   # continues same chat
    """

    def __init__(
        self,
        *,
        harness: AgentHarness = AgentHarness.CLAUDE_CODE,
        template: Optional[SandboxTemplate] = None,
        api_key: Optional[str] = None,
        timeout: int = 300,
        system_prompt: Optional[str] = None,
        working_dir: str = "/home/user",
        skills: Optional[list[Skill | str]] = None,
    ) -> None:
        self.harness = harness
        self.template = template or _HARNESS_DEFAULTS[harness]
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        self.system_prompt = system_prompt
        self.working_dir = working_dir
        self.skills: list[Skill] = [
            Skill(name=s) if isinstance(s, str) else s
            for s in (skills or [])
        ]

        self.sandbox: Optional[AsyncSandbox] = None
        self._session_id: Optional[str] = None   # tracks last conversation id

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AgentSession":
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
            return

        self.sandbox = await AsyncSandbox.create(
            self.template.value,
            envs={"ANTHROPIC_API_KEY": self.api_key},
            timeout=self.timeout,
        )

        if self.system_prompt:
            await self.sandbox.files.write(
                f"{self.working_dir}/CLAUDE.md",
                self.system_prompt,
            )

        agent_id = _HARNESS_AGENT_IDS[self.harness]
        for skill in self.skills:
            if skill.url:
                cmd = f"npx skills add {skill.url} --skill {skill.name} -a {agent_id} -y"
            else:
                cmd = f"npx skill add {skill.name} -a {agent_id} -y"
            await self.sandbox.commands.run(cmd, cwd=self.working_dir)

    async def close(self) -> None:
        """Terminate the sandbox and free all resources.

        Called automatically by the async context manager.
        """
        if self.sandbox is not None:
            await self.sandbox.kill()
            self.sandbox = None
            self._session_id = None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
    ) -> AgentResult:
        """Execute the agent with *prompt* and wait for it to finish.

        Args:
            prompt:  The task description to hand to the agent.

        Returns:
            An :class:`AgentResult` with the full output text and metadata.

        Raises:
            RuntimeError: If :meth:`open` has not been called (or the context
                          manager has not been entered).
        """
        self._require_open()
        cmd = self._build_command(prompt, streaming=False)
        # TODO: this actually will timeout when claude code runs a background server
        result = await self.sandbox.commands.run(  # type: ignore[union-attr]
            cmd,
            cwd=self.working_dir,
        )

        agent_result = self._parse_json_output(result.stdout, result.exit_code)
        if agent_result.session_id:
            self._session_id = agent_result.session_id
        return agent_result

    async def stream(
        self,
        prompt: str,
    ) -> AsyncIterator[str]:
        """Execute the agent and yield text chunks as they arrive.

        The returned async-generator yields individual text fragments so you
        can print or process them in real time.  The conversation session ID
        (if any) is captured automatically after the stream is exhausted.

        Args:
            prompt:  The task description to hand to the agent.

        Yields:
            Raw text chunks from the agent's stdout stream.

        Example::

            async for chunk in session.stream("Refactor the auth module"):
                print(chunk, end="", flush=True)
        """
        self._require_open()
        cmd = self._build_command(prompt, streaming=True)

        events: list[dict] = []
        line_buf = ""
        out_queue: asyncio.Queue[str | object] = asyncio.Queue()
        stream_end = object()

        async def _emit_jsonl_line(line: str) -> None:
            if not line:
                return
            try:
                event = json.loads(line)
                events.append(event)
                if event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text = block["text"]
                            if text:
                                await out_queue.put(text)
            except json.JSONDecodeError:
                await out_queue.put(line)

        async def _on_stdout(data: str) -> None:
            nonlocal line_buf
            line_buf += data
            while "\n" in line_buf:
                raw_line, line_buf = line_buf.split("\n", 1)
                await _emit_jsonl_line(raw_line.strip())

        async def _run_command() -> None:
            nonlocal line_buf
            try:
                # TODO: this actually will timeout when claude code runs a background server
                await self.sandbox.commands.run(  # type: ignore[union-attr]
                    cmd,
                    cwd=self.working_dir,
                    on_stdout=_on_stdout,
                )
                tail = line_buf.strip()
                if tail:
                    await _emit_jsonl_line(tail)
                    line_buf = ""
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

        # After the run, extract the session id from any "result" event
        for event in events:
            if event.get("type") == "result" and "session_id" in event:
                self._session_id = event["session_id"]
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_open(self) -> None:
        if self.sandbox is None:
            raise RuntimeError(
                "AgentSession is not open.  Use it as an async context manager "
                "or call `await session.open()` first."
            )

    def _build_command(
        self,
        prompt: str,
        *,
        streaming: bool,
    ) -> str:
        """Construct the shell command string for the chosen harness."""
        entry = _HARNESS_ENTRY_POINTS[self.harness]

        parts = [
            entry,
            "--dangerously-skip-permissions",
        ]

        if streaming:
            parts += ["--output-format", "stream-json", "--verbose"]
        else:
            parts += ["--output-format", "json"]

        if self._session_id:
            parts += ["--resume", self._session_id]

        # Safely quote the prompt to avoid shell injection
        safe_prompt = prompt.replace('"', '\\"')
        parts += ["-p", f'"{safe_prompt}"']

        return " ".join(parts)

    def _parse_json_output(self, stdout: str, exit_code: int) -> AgentResult:
        """Parse Claude Code's JSON output format into an :class:`AgentResult`."""
        stdout = stdout.strip()
        session_id: Optional[str] = None
        output = stdout

        try:
            data = json.loads(stdout)
            session_id = data.get("session_id")
            # Claude Code puts the final answer in result.result
            output = data.get("result", stdout)
        except (json.JSONDecodeError, AttributeError):
            pass  # Treat raw stdout as the output

        return AgentResult(
            output=output,
            session_id=session_id,
            exit_code=exit_code,
        )
