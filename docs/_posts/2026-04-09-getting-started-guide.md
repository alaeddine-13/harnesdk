---
layout: post
title: "Getting Started with HarneSDK"
date: 2026-04-09
tag: tutorial
excerpt: "A step-by-step guide to running your first AI agent session with HarneSDK — from installation to streaming live output."
---

This guide walks you through everything you need to run your first agent session with HarneSDK.

## Prerequisites

- Python 3.10 or newer
- An [Anthropic API key](https://console.anthropic.com/)
- An [E2B API key](https://e2b.dev/)

## Installation

```bash
pip install harnesdk
```

## Setting up API keys

Export your keys as environment variables:

```bash
export ANTHROPIC_API_KEY=your_anthropic_key
export E2B_API_KEY=your_e2b_key
```

> **Security tip:** Use short-lived keys with strict budget limits. Agents run inside sandboxes but can still be vectors for prompt injection.

## Your first run

The simplest usage is `AgentSession.run()` — it waits for the agent to complete and returns the full output:

```python
import asyncio
from harnesdk.agent import AgentSession

async def main():
    async with AgentSession() as session:
        result = await session.run("Write and run a Python script that prints the Fibonacci sequence")
        print(result.output)

asyncio.run(main())
```

## Streaming output

For long-running tasks, stream output as it arrives:

```python
async def main():
    async with AgentSession() as session:
        async for chunk in session.stream("Build a FastAPI app with two endpoints"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

## Serving a live application

Agents can build and serve web applications. Expose the sandbox port to get a public URL:

```python
async def main():
    async with AgentSession() as session:
        async for chunk in session.stream(
            "Build a simple todo list app with HTML/CSS/JS "
            "and serve it with Python's HTTP server on port 8000. "
            "Use: nohup python -m http.server 8000 > /tmp/s.log 2>&1 < /dev/null &"
        ):
            print(chunk)

        url = session.sandbox.get_host(8000)
        print(f"\nApp live at: https://{url}")

asyncio.run(main())
```

## CLI usage

HarneSDK also ships with a CLI for quick one-off runs:

```bash
harnesdk run "Create a hello world web page"
```

## Next steps

- Check the [GitHub repo](https://github.com/alaeddine-13/harnesdk) for examples
- Browse the [CHANGELOG](https://github.com/alaeddine-13/harnesdk/tree/main/CHANGELOG) for the latest updates
- Open an issue if you hit a problem
