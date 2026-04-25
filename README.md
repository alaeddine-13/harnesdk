# harnesdk

![PyPI version](https://img.shields.io/pypi/v/harnesdk.svg)

Run major agents and harnesses programmatically, in a sandbox. Openclaw, Claude Code, Hermes agent,...

* [GitHub](https://github.com/alaeddine-13/harnesdk/) | [PyPI](https://pypi.org/project/harnesdk/) | [Documentation](https://alaeddine-13.github.io/harnesdk/)
* Created by [Alaeddine Abdessalem](https://github.com/alaeddine-13)
* MIT License

## Installation

```bash
pip install harnesdk
```

## Setup

Set the required environment variables:

```bash
export E2B_API_KEY=your_e2b_api_key

# Claude Code
export ANTHROPIC_API_KEY=your_anthropic_api_key

# Hermes Agent (example: via OpenRouter)
export OPENROUTER_API_KEY=your_openrouter_api_key
```

> **Security Warning:** Use budgeted and short-lived API keys. AI agents living INSIDE a sandbox can be used to exfiltrate credentials with prompt injection. We're actively working on a solution.

## Usage

harnesdk ships one concrete session per supported harness. They all share the
same interface — ``open`` / ``close``, ``run``, ``stream``, async-context-manager
— defined by the abstract :class:`AgentSession` base class.

| Harness            | Class                 | Default template |
| ------------------ | --------------------- | ---------------- |
| Anthropic Claude   | `ClaudeAgentSession`  | `claude`         |
| Nous Hermes Agent  | `HermesAgentSession`  | `hermes-agent`   |

### Run Claude Code and get output

```python
import asyncio
from harnesdk import ClaudeAgentSession

async def main():
    async with ClaudeAgentSession() as session:
        result = await session.run("Create a hello world HTTP server in Go")
        print(result.output)

asyncio.run(main())
```

### Stream Claude Code output in real time

```python
import asyncio
from harnesdk import ClaudeAgentSession

async def main():
    async with ClaudeAgentSession() as session:
        async for chunk in session.stream("Create a hello world HTTP server in Go"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

### Run Hermes Agent (Kimi K2.6 via OpenRouter)

This mirrors the CLI invocation:

```bash
hermes chat --yolo -q "..." --model moonshotai/kimi-k2.6 --provider openrouter
```

```python
import asyncio
import os
from harnesdk import HermesAgentSession

async def main():
    async with HermesAgentSession(
        model="moonshotai/kimi-k2.6",
        provider="openrouter",
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=600,
    ) as session:
        result = await session.run(
            "/ugc-factory-skill create a skincare product ugc tiktok video. "
            "The skincare product is called MyMoist. Just imagine all details "
            "and don't get back to me with further questions. Just make sure "
            "to deliver the output video at /tmp/out.mp4"
        )
        print(result.output)

asyncio.run(main())
```

### Run and serve an app from the sandbox (Jupyter)

```python
from harnesdk import ClaudeAgentSession
from IPython.display import IFrame

async with ClaudeAgentSession() as session:
    async for chunk in session.stream(
        "build an 'introducing HarneSDK' html page, and serve it with python http server under port 8000. "
        "Use this pattern nohup your-server-command > /tmp/server.log 2>&1 < /dev/null &"
    ):
        print(chunk)
    page_url = session.sandbox.get_host(8000)
    print(f"app live at {page_url}")
    display(IFrame(f"https://{page_url}", width=700, height=400))
```

### Writing your own harness

Subclass :class:`AgentSession` and implement the two required hooks
(`_env_vars` and `_build_command`).  Override `_setup_system_prompt`,
`_install_skills`, `_register_mcps`, `_parse_output`, and
`_make_stream_processor` as needed to match the harness CLI — the base class
takes care of sandbox lifecycle and streaming orchestration.

Output:
```text
I'll create an introductory HTML page for HarneSDK and serve it using Python's HTTP server on port 8000.

The server is now running at **http://localhost:8000**

app live at 8000-7zerfgtyjcjpl79a141ez.e2b.app
```
Generated app:

<img src="docs/assets/app-demo.png" width="50%" />

## Development

To set up for local development:

```bash
# Clone your fork
git clone git@github.com:your_username/harnesdk.git
cd harnesdk

# Install in editable mode with live updates
uv tool install --editable .
```

This installs the CLI globally but with live updates - any changes you make to the source code are immediately available when you run `harnesdk`.


## Author

harnesdk was created in 2026 by Alaeddine Abdessalem.
