

# harnesdk

![PyPI version](https://img.shields.io/pypi/v/harnesdk.svg)

Ejecuta agentes y harness principales de forma programática, en un sandbox. Openclaw, Claude Code, Hermes agent,...

* [GitHub](https://github.com/alaeddine-13/harnesdk/) | [PyPI](https://pypi.org/project/harnesdk/) | [Documentación](https://alaeddine-13.github.io/harnesdk/)
* Creado por [Alaeddine Abdessalem](https://github.com/alaeddine-13)
* Licencia MIT

## Instalación

```bash
pip install harnesdk
```

## Configuración

Establece las variables de entorno requeridas:

```bash
export ANTHROPIC_API_KEY=your_anthropic_api_key
export E2B_API_KEY=your_e2b_api_key
```

> **Advertencia de seguridad:** Utiliza claves API con presupuesto y de corta duración. Los agentes de IA que viven DENTRO de un sandbox pueden ser utilizados para exfiltrar credenciales mediante inyección de prompts. Estamos trabajando activamente en una solución.

## Uso

### Ejecutar un agente y obtener la salida

```python
import asyncio
from harnesdk.agent import AgentSession

async with AgentSession() as session:
    result = await session.run("Create a hello world HTTP server in Go")
    print(result.output)
```

### Transmitir la salida en tiempo real

```python
import asyncio
from harnesdk.agent import AgentSession

async with AgentSession() as session:
    async for chunk in session.stream("Create a hello world HTTP server in Go"):
        print(chunk, end="", flush=True)
```

### Ejecutar y servir una aplicación desde el sandbox (Jupyter)

```python
from harnesdk.agent import AgentSession
from IPython.display import IFrame

async with AgentSession() as session:
    async for chunk in session.stream(
        "build an 'introducing HarneSDK' html page, and serve it with python http server under port 8000. "
        "Use this pattern nohup your-server-command > /tmp/server.log 2>&1 < /dev/null &"
    ):
        print(chunk)
    page_url = session.sandbox.get_host(8000)
    print(f"app live at {page_url}")
    display(IFrame(f"https://{page_url}", width=700, height=400))
```

Salida:
```text
I'll create an introductory HTML page for HarneSDK and serve it using Python's HTTP server on port 8000.

The server is now running at **http://localhost:8000**

app live at 8000-7zerfgtyjcjpl79a141ez.e2b.app
```
Aplicación generada:

<img src="docs/assets/app-demo.png" width="50%" />

## Desarrollo

Para configurar el entorno de desarrollo local:

```bash
# Clone your fork
git clone git@github.com:your_username/harnesdk.git
cd harnesdk

# Install in editable mode with live updates
uv tool install --editable .
```

Esto instala la CLI globalmente, pero con actualizaciones en vivo: cualquier cambio que realices en el código fuente estará disponible inmediatamente cuando ejecutes `harnesdk`.


## Autor

harnesdk fue creado en 2026 por Alaeddine Abdessalem.
