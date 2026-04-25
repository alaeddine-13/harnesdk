"""
opencode_agent.py
~~~~~~~~~~~~~~~~~
Concrete :class:`~harnesdk.agent.AgentSession` implementation for the
`OpenCode <https://opencode.ai>`_ CLI.

This module keeps all OpenCode-specific wiring (``AGENTS.md`` system prompt,
``npx skills add -a opencode`` installer, ``opencode.json`` MCP registration,
one-shot ``opencode run`` / ``--session`` semantics, provider/model flags)
isolated from the harness-agnostic base class.

The basic CLI invocation that drives this class looks like::

    opencode run \\
        --format json \\
        --model anthropic/claude-3-5-sonnet-20241022 \\
        "<prompt>"

Example::

    import asyncio
    import os
    from harnesdk import OpenCodeAgentSession

    async def main():
        async with OpenCodeAgentSession(
            model="anthropic/claude-3-5-sonnet-20241022",
            api_key=os.environ["ANTHROPIC_API_KEY"],
        ) as session:
            result = await session.run("Create a hello world HTTP server in Go")
            print(result.output)

    asyncio.run(main())
"""

from __future__ import annotations

import json
import shlex
from typing import ClassVar, Literal

from harnesdk.agent import (
    AgentResult,
    AgentSession,
    StreamProcessor,
)

# ---------------------------------------------------------------------------
# Provider → env-var mapping
# ---------------------------------------------------------------------------

#: Canonical env variable OpenCode expects for each well-known provider.  Only
#: providers whose api-key env var is unambiguous are listed; users on other
#: providers should pass ``env=`` explicitly.
_PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "gemini": "GOOGLE_GENERATIVE_AI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "huggingface": "HF_TOKEN",
}


# ---------------------------------------------------------------------------
# Streaming processor for OpenCode's ``--format json`` JSONL output
# ---------------------------------------------------------------------------

class _OpenCodeStreamProcessor(StreamProcessor):
    """Parses OpenCode's JSONL ``--format json`` stdout.

    Text is extracted from ``text`` events and the conversation
    ``sessionID`` is captured from any event that carries it (every event
    emitted by ``opencode run --format json`` includes a ``sessionID`` field).
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
            # Pass through any non-JSON line so the caller still sees it.
            return [line]

        self.events.append(event)
        if self._log_level == "all":
            return [line]

        # Every JSONL event carries ``sessionID``; capture it the first time
        # we see it so subsequent runs can resume the conversation.
        sid = event.get("sessionID")
        if sid and not self._session_id:
            self._session_id = sid

        if event.get("type") == "text":
            text = event.get("part", {}).get("text")
            if text:
                return [text]

        return []


# ---------------------------------------------------------------------------
# OpenCode session
# ---------------------------------------------------------------------------

class OpenCodeAgentSession(AgentSession):
    """Agent session wired up for the OpenCode CLI.

    Typical invocation produced for each :meth:`run` call::

        opencode run \\
            --format json \\
            --model <provider>/<model> \\
            "<prompt>"

    Args:
        model:       Model id in OpenCode's ``provider/model`` form
                     (e.g. ``"anthropic/claude-3-5-sonnet-20241022"``).
                     Mapped to ``--model``.
        provider:    Optional provider id used purely to resolve which
                     environment variable should receive ``api_key`` (see
                     :data:`_PROVIDER_ENV_VARS`).  When ``model`` is passed
                     in ``provider/model`` form and ``provider`` is omitted,
                     the leading segment is used as a hint.
        api_key:     Convenience shortcut for the provider's api-key env var.
                     Ignored when no provider can be resolved.
        log_level:   Streaming verbosity. Set to ``"all"`` to emit every raw
                     JSONL event line from OpenCode.
        env:         Extra environment variables to inject into the sandbox
                     (merged with ``api_key`` / ``provider``).
        agent:       Optional OpenCode agent name (``--agent``).
        extra_args:  Additional raw CLI flags appended to every ``opencode
                     run`` invocation.
        (see :class:`~harnesdk.agent.AgentSession` for common kwargs)

    Notes:
        * System prompt is written to ``<working_dir>/AGENTS.md``.
        * Skills are installed via ``npx skills add ... -a opencode -y``.
          Both registry names and Git URLs are supported.
        * MCP servers are registered by writing
          ``<working_dir>/opencode.json`` directly.  Both remote
          (``url``/``headers``) and local (``command``/``args``/``env``)
          transports are supported.
    """

    default_template: ClassVar[str] = "opencode"
    _entrypoint: ClassVar[str] = "opencode"
    #: Agent id used by the ``npx skills`` installer.
    _skills_agent_id: ClassVar[str] = "opencode"

    def __init__(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        log_level: Literal["default", "all"] = "default",
        env: dict[str, str] | None = None,
        agent: str | None = None,
        extra_args: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            log_level=log_level,
            env=env,
            **kwargs,
        )
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.agent = agent
        self.extra_args = list(extra_args) if extra_args else []

        # If the caller did not pass ``provider`` explicitly but the model is
        # in ``provider/model`` form, infer the provider from the prefix.
        provider_hint = provider
        if not provider_hint and model and "/" in model:
            provider_hint = model.split("/", 1)[0]

        # Resolve the environment for the sandbox: start with the user-supplied
        # dict, then layer in the provider api-key convenience.
        resolved = dict(env or {})
        if api_key and provider_hint:
            key_name = _PROVIDER_ENV_VARS.get(provider_hint)
            if key_name and key_name not in resolved:
                resolved[key_name] = api_key
        elif api_key and not provider_hint:
            # No provider hint at all: fall back to the most common key name.
            resolved.setdefault("ANTHROPIC_API_KEY", api_key)
        self._env = resolved

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _env_vars(self) -> dict[str, str]:
        return dict(self._env)

    async def _setup_system_prompt(self) -> None:
        if not self.system_prompt:
            return
        # AGENTS.md is OpenCode's project-scoped rules file (it also reads
        # CLAUDE.md as a fallback, but AGENTS.md is the canonical location).
        await self.sandbox.files.write(  # type: ignore[union-attr]
            f"{self.working_dir}/AGENTS.md",
            self.system_prompt,
        )

    async def _install_skills(self) -> None:
        for skill in self.skills:
            if skill.url:
                cmd = (
                    f"npx -y skills add {shlex.quote(skill.url)} "
                    f"--skill {shlex.quote(skill.name)} "
                    f"-a {self._skills_agent_id} -y"
                )
            else:
                cmd = (
                    f"npx -y skills add {shlex.quote(skill.name)} "
                    f"-a {self._skills_agent_id} -y"
                )
            await self.sandbox.commands.run(cmd, cwd=self.working_dir)  # type: ignore[union-attr]

    async def _register_mcps(self) -> None:
        if not self.mcps:
            return

        servers: dict[str, dict] = {}
        for mcp in self.mcps:
            entry: dict = {"enabled": True}
            if mcp.url:
                entry["type"] = "remote"
                entry["url"] = mcp.url
                if mcp.headers:
                    entry["headers"] = dict(mcp.headers)
            elif mcp.command:
                entry["type"] = "local"
                command_parts: list[str] = [mcp.command]
                if mcp.args:
                    command_parts += list(mcp.args)
                entry["command"] = command_parts
                if mcp.env:
                    entry["environment"] = dict(mcp.env)
            else:
                raise ValueError(
                    f"McpServer {mcp.name!r}: must provide either `url` "
                    f"(remote) or `command` (local)."
                )
            servers[mcp.name] = entry

        config_body = json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": servers,
            },
            indent=2,
        )
        # Write the project-scoped opencode.json next to where commands run.
        await self.sandbox.files.write(  # type: ignore[union-attr]
            f"{self.working_dir}/opencode.json",
            config_body,
        )

    # ------------------------------------------------------------------
    # Command building / output parsing
    # ------------------------------------------------------------------

    def _build_command(self, prompt: str, *, streaming: bool) -> str:
        # ``streaming`` is unused: we always request JSON output so we can
        # reliably parse text + sessionID, and stream it to the caller as
        # events arrive.  The base class' streaming machinery just forwards
        # the per-event text we extract in the processor.
        del streaming

        parts: list[str] = [
            self._entrypoint,
            "run",
            "--format",
            "json",
        ]

        if self.model:
            parts += ["--model", shlex.quote(self.model)]
        if self.agent:
            parts += ["--agent", shlex.quote(self.agent)]

        if self._session_id:
            parts += ["--session", shlex.quote(self._session_id)]

        parts += self.extra_args

        safe_prompt = prompt.replace('"', '\\"')
        parts += [f'"{safe_prompt}"']

        return " ".join(parts)

    def _parse_output(self, stdout: str, exit_code: int) -> AgentResult:
        # ``opencode run --format json`` emits one JSON object per line.  We
        # walk every line, capturing the session id and concatenating the
        # text fragments.  Non-JSON lines (rare) are appended verbatim so the
        # caller never silently loses output.
        session_id: str | None = None
        text_parts: list[str] = []
        raw_events: list[dict] = []

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                text_parts.append(line)
                continue

            raw_events.append(event)

            sid = event.get("sessionID")
            if sid and not session_id:
                session_id = sid

            if event.get("type") == "text":
                text = event.get("part", {}).get("text")
                if text:
                    text_parts.append(text)

        output = "".join(text_parts).strip() or stdout.strip()

        return AgentResult(
            output=output,
            session_id=session_id,
            raw_events=raw_events,
            exit_code=exit_code,
        )

    def _make_stream_processor(self) -> StreamProcessor:
        return _OpenCodeStreamProcessor(log_level=self.log_level)
