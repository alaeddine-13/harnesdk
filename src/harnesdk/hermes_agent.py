"""
hermes_agent.py
~~~~~~~~~~~~~~~
Concrete :class:`~harnesdk.agent.AgentSession` implementation for Nous
Research's *Hermes Agent* CLI.

This module keeps all Hermes-specific wiring (``AGENTS.md`` system prompt,
``hermes skills install`` installer, ``~/.hermes/config.yaml`` MCP registration,
one-shot ``hermes chat -q`` / ``--continue`` semantics, provider/model flags)
isolated from the harness-agnostic base class.

The basic CLI invocation that drives this class looks like::

    hermes chat --yolo -Q \
        -q "<prompt>" \
        --model moonshotai/kimi-k2.6 \
        --provider openrouter

Example::

    import asyncio
    import os
    from harnesdk import HermesAgentSession

    async def main():
        async with HermesAgentSession(
            model="moonshotai/kimi-k2.6",
            provider="openrouter",
            api_key=os.environ["OPENROUTER_API_KEY"],
        ) as session:
            result = await session.run("What's 2+2?")
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

#: Canonical env variable Hermes expects for each supported provider.  Only
#: providers whose api-key env var is unambiguous are listed; users on other
#: providers should pass ``env=`` explicitly.
_PROVIDER_ENV_VARS: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "OPENAI_API_KEY",
    "nous": "NOUS_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google-gemini-cli": "GEMINI_API_KEY",
    "huggingface": "HF_TOKEN",
    "zai": "ZAI_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
    "kimi-coding-cn": "KIMI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "grok": "XAI_API_KEY",
    "alibaba": "DASHSCOPE_API_KEY",
    "arcee": "ARCEE_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "qwen-oauth": "QWEN_API_KEY",
}

#: Sentinel stored in ``_session_id`` once the Hermes sandbox has produced any
#: output.  The next :meth:`run` / :meth:`stream` invocation picks this up and
#: adds ``--continue`` so the conversation survives across turns.  Hermes does
#: not print its session id on stdout, so we rely on "most recent session in
#: this sandbox" semantics – which is safe because the sandbox is dedicated to
#: a single :class:`HermesAgentSession`.
_HERMES_CONTINUE_SENTINEL = "__hermes_latest__"


# ---------------------------------------------------------------------------
# Streaming processor
# ---------------------------------------------------------------------------

class _HermesStreamProcessor(StreamProcessor):
    """Pass-through processor that also flips the *continue next* flag.

    Hermes streams plain text, so we yield each stdout chunk unchanged.  As a
    side effect, we signal that a session has been started (via the
    :attr:`session_id` sentinel) so :class:`HermesAgentSession` knows to pass
    ``--continue`` on the next run.
    """

    def __init__(self) -> None:
        super().__init__()
        self._has_output = False

    def feed(self, data: str) -> list[str]:
        if not data:
            return []
        self._has_output = True
        return [data]

    @property
    def session_id(self) -> str | None:
        return _HERMES_CONTINUE_SENTINEL if self._has_output else None


# ---------------------------------------------------------------------------
# Hermes session
# ---------------------------------------------------------------------------

class HermesAgentSession(AgentSession):
    """Agent session wired up for Nous Research's *Hermes Agent* CLI.

    Typical invocation produced for each :meth:`run` call::

        hermes chat --yolo -Q \\
            -q "<prompt>" \\
            --model <model> \\
            --provider <provider>

    Args:
        model:       Model id (e.g. ``"moonshotai/kimi-k2.6"``).  Mapped to
                     ``--model``.
        provider:    Provider id (e.g. ``"openrouter"``).  Mapped to
                     ``--provider``.
        api_key:     Convenience shortcut for the provider's api-key env var
                     (see :data:`_PROVIDER_ENV_VARS`).  Ignored when
                     ``provider`` is unknown.
        env:         Extra environment variables to inject into the sandbox
                     (merged with ``api_key`` / ``provider``).
        toolsets:    Optional list of Hermes toolsets (``--toolsets``).
        worktree:    If ``True``, run each invocation inside an isolated git
                     worktree (``--worktree``).  Defaults to ``False``.
        extra_args:  Additional raw CLI flags to append to every command.
        (see :class:`~harnesdk.agent.AgentSession` for common kwargs)

    Notes:
        * System prompt is written to ``<working_dir>/AGENTS.md``.
        * Skills are installed via ``hermes skills install <name>``.  URL-based
          skills are not supported for Hermes (use a named registry skill).
        * MCP servers are registered by writing ``~/.hermes/config.yaml``
          directly.  Both HTTP (``url``/``headers``) and stdio
          (``command``/``args``/``env``) transports are supported.
    """

    default_template: ClassVar[str] = "hermes-agent"
    _entrypoint: ClassVar[str] = "hermes"

    def __init__(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        log_level: Literal["default", "all"] = "default",
        env: dict[str, str] | None = None,
        toolsets: list[str] | None = None,
        worktree: bool = False,
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
        self.toolsets = list(toolsets) if toolsets else None
        self.worktree = worktree
        self.extra_args = list(extra_args) if extra_args else []

        # Resolve the environment for the sandbox: start with the user-supplied
        # dict, then layer in the provider api-key convenience.
        resolved = dict(env or {})
        if api_key and provider:
            key_name = _PROVIDER_ENV_VARS.get(provider)
            if key_name and key_name not in resolved:
                resolved[key_name] = api_key
        elif api_key and not provider:
            # No provider hint: fall back to the most common key name.
            resolved.setdefault("OPENROUTER_API_KEY", api_key)
        self._env = resolved

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _env_vars(self) -> dict[str, str]:
        return dict(self._env)

    async def _setup_system_prompt(self) -> None:
        if not self.system_prompt:
            return
        # AGENTS.md is the project-scoped context file Hermes injects into the
        # system prompt at session start.
        await self.sandbox.files.write(  # type: ignore[union-attr]
            f"{self.working_dir}/AGENTS.md",
            self.system_prompt,
        )

    async def _install_skills(self) -> None:
        for skill in self.skills:
            if skill.url:
                raise NotImplementedError(
                    f"HermesAgentSession does not yet support URL-based skills "
                    f"(got {skill.url!r}). Use a registry skill name instead, "
                    f"e.g. Skill(name='official/security/1password')."
                )
            cmd = f"hermes skills install {shlex.quote(skill.name)} --force"
            await self.sandbox.commands.run(cmd, cwd=self.working_dir)  # type: ignore[union-attr]

    async def _register_mcps(self) -> None:
        if not self.mcps:
            return

        servers: dict[str, dict] = {}
        for mcp in self.mcps:
            entry: dict = {}
            if mcp.url:
                entry["url"] = mcp.url
                if mcp.transport and mcp.transport != "http":
                    entry["transport"] = mcp.transport
                if mcp.headers:
                    entry["headers"] = dict(mcp.headers)
            elif mcp.command:
                entry["command"] = mcp.command
                if mcp.args:
                    entry["args"] = list(mcp.args)
            else:
                raise ValueError(
                    f"McpServer {mcp.name!r}: must provide either `url` "
                    f"(HTTP/SSE) or `command` (stdio)."
                )
            if mcp.env:
                entry["env"] = dict(mcp.env)
            servers[mcp.name] = entry

        # JSON is a subset of YAML, so we can serialize the config as JSON and
        # Hermes's YAML parser will load it fine.
        config_body = json.dumps({"mcp_servers": servers}, indent=2)
        heredoc = (
            'mkdir -p "$HOME/.hermes" && '
            "cat > \"$HOME/.hermes/config.yaml\" << 'HARNESDK_EOF'\n"
            f"{config_body}\n"
            "HARNESDK_EOF"
        )
        await self.sandbox.commands.run(heredoc, cwd=self.working_dir)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Command building / output parsing
    # ------------------------------------------------------------------

    def _build_command(self, prompt: str, *, streaming: bool) -> str:
        parts: list[str] = [
            self._entrypoint,
            "chat",
            "--yolo",
            "-Q",  # programmatic mode: suppress banner/spinner/tool previews
        ]

        if self.model:
            parts += ["--model", shlex.quote(self.model)]
        if self.provider:
            parts += ["--provider", shlex.quote(self.provider)]
        if self.toolsets:
            parts += ["--toolsets", shlex.quote(",".join(self.toolsets))]
        if self.worktree:
            parts += ["--worktree"]

        if self._session_id == _HERMES_CONTINUE_SENTINEL:
            parts += ["--continue"]

        if streaming:
            parts += ["--verbose"]

        parts += self.extra_args

        safe_prompt = prompt.replace('"', '\\"')
        parts += ["-q", f'"{safe_prompt}"']

        return " ".join(parts)

    def _parse_output(self, stdout: str, exit_code: int) -> AgentResult:
        output = stdout.strip()
        # Mark that a session has been started so the next call uses
        # --continue.  Hermes does not print its session id on stdout.
        session_id = _HERMES_CONTINUE_SENTINEL if output else None
        return AgentResult(
            output=output,
            session_id=session_id,
            exit_code=exit_code,
        )

    def _make_stream_processor(self) -> StreamProcessor:
        return _HermesStreamProcessor()
