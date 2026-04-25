"""
claude_agent.py
~~~~~~~~~~~~~~~
Concrete :class:`~harnesdk.agent.AgentSession` implementation for Anthropic's
*Claude Code* harness.

This module keeps all Claude-specific wiring (``CLAUDE.md`` system prompt,
``npx skills add`` installer, ``claude mcp add`` registration, the JSON /
JSONL output formats) isolated from the harness-agnostic base class.

Example::

    import asyncio
    from harnesdk import ClaudeAgentSession

    async def main():
        async with ClaudeAgentSession(model="sonnet") as session:
            result = await session.run("Write a fizzbuzz in Rust")
            print(result.output)

    asyncio.run(main())
"""

from __future__ import annotations

import json
import os
import shlex
from typing import ClassVar, Literal

from harnesdk.agent import (
    AgentResult,
    AgentSession,
    StreamProcessor,
)

# ---------------------------------------------------------------------------
# Streaming processor for Claude's ``stream-json`` format
# ---------------------------------------------------------------------------

class _ClaudeStreamProcessor(StreamProcessor):
    """Parses Claude Code's JSONL ``stream-json`` stdout.

    Text is extracted from ``assistant`` events and the conversation
    ``session_id`` is captured from the final ``result`` event.
    """

    def __init__(self, *, log_level: Literal["default", "all"] = "default") -> None:
        super().__init__()
        self._buf = ""
        self._session_id: str | None = None
        self._log_level: Literal["default", "all"] = log_level

    def feed(self, data: str) -> list[str]:
        out: list[str] = []
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            out.extend(self._process_line(line.strip()))
        return out

    def flush(self) -> list[str]:
        tail = self._buf.strip()
        self._buf = ""
        return self._process_line(tail) if tail else []

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _process_line(self, line: str) -> list[str]:
        if not line:
            return []
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return [line]

        self.events.append(event)
        if self._log_level == "all":
            return [line]
        out: list[str] = []

        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text = block.get("text")
                    if text:
                        out.append(text)
        elif event.get("type") == "result" and "session_id" in event:
            self._session_id = event["session_id"]

        return out


# ---------------------------------------------------------------------------
# Claude Code session
# ---------------------------------------------------------------------------

class ClaudeAgentSession(AgentSession):
    """Agent session wired up for Anthropic's *Claude Code* CLI.

    Args:
        model:            Optional Claude Code model id or alias (e.g. ``"sonnet"``,
                          ``"opus"``, or a full model name).  Mapped to ``--model``.
        api_key:          Anthropic API key.  Falls back to the
                          ``ANTHROPIC_API_KEY`` environment variable.
        log_level:        Streaming verbosity. Set to ``"all"`` to emit every
                          raw JSONL event line from Claude Code.
        (see :class:`~harnesdk.agent.AgentSession` for common kwargs)

    Notes:
        * System prompt is written to ``<working_dir>/CLAUDE.md``.
        * Skills are installed via ``npx skills add`` against the
          ``claude-code`` agent id.
        * MCP servers are registered via ``claude mcp add``.
    """

    default_template: ClassVar[str] = "claude"
    #: CLI entrypoint name.
    _entrypoint: ClassVar[str] = "claude"
    #: Agent id used by the ``npx skills`` installer.
    _skills_agent_id: ClassVar[str] = "claude-code"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        log_level: Literal["default", "all"] = "default",
        env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            log_level=log_level,
            env=env,
            **kwargs,
        )
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _env_vars(self) -> dict[str, str]:
        return {"ANTHROPIC_API_KEY": self.api_key}

    async def _setup_system_prompt(self) -> None:
        if not self.system_prompt:
            return
        await self.sandbox.files.write(  # type: ignore[union-attr]
            f"{self.working_dir}/CLAUDE.md",
            self.system_prompt,
        )

    async def _install_skills(self) -> None:
        for skill in self.skills:
            if skill.url:
                cmd = (
                    f"npx skills add {skill.url} --skill {skill.name} "
                    f"-a {self._skills_agent_id} -y"
                )
            else:
                cmd = f"npx skills add {skill.name} -a {self._skills_agent_id} -y"
            await self.sandbox.commands.run(cmd, cwd=self.working_dir)  # type: ignore[union-attr]

    async def _register_mcps(self) -> None:
        for mcp in self.mcps:
            if not mcp.url:
                raise ValueError(
                    f"ClaudeAgentSession only supports HTTP/SSE MCPs "
                    f"(got stdio server {mcp.name!r}). Provide `url`."
                )
            cmd = (
                f"claude mcp add --transport {mcp.transport}"
                f" -s {mcp.scope}"
                f" {mcp.name}"
                f" {mcp.url}"
            )
            if mcp.headers:
                for header_name, header_value in mcp.headers.items():
                    safe_value = header_value.replace('"', '\\"')
                    cmd += f' -H "{header_name}: {safe_value}"'
            await self.sandbox.commands.run(cmd, cwd=self.working_dir)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Command building / output parsing
    # ------------------------------------------------------------------

    def _build_command(self, prompt: str, *, streaming: bool) -> str:
        parts = [
            self._entrypoint,
            "--dangerously-skip-permissions",
            '--disallowedTools "Agent,AskUserQuestion,CronCreate,CronDelete,CronList,EnterWorktree,ExitWorktree,NotebookEdit,PowerShell,SendMessage,TaskCreate,TaskGet,TaskList,TaskOutput,TaskStop,TaskUpdate,TeamCreate,TeamDelete"'
        ]

        if self.model:
            parts += ["--model", shlex.quote(self.model)]

        if streaming:
            parts += ["--output-format", "stream-json", "--verbose"]
        else:
            parts += ["--output-format", "json"]

        if self._session_id:
            parts += ["--resume", self._session_id]

        safe_prompt = prompt.replace('"', '\\"')
        parts += ["-p", f'"{safe_prompt}"']

        return " ".join(parts)

    def _parse_output(self, stdout: str, exit_code: int) -> AgentResult:
        stdout = stdout.strip()
        session_id: str | None = None
        output = stdout

        try:
            data = json.loads(stdout)
            session_id = data.get("session_id")
            output = data.get("result", stdout)
        except (json.JSONDecodeError, AttributeError):
            pass

        return AgentResult(
            output=output,
            session_id=session_id,
            exit_code=exit_code,
        )

    def _make_stream_processor(self) -> StreamProcessor:
        return _ClaudeStreamProcessor(log_level=self.log_level)
