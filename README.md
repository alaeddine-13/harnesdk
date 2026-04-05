# harnesdk

![PyPI version](https://img.shields.io/pypi/v/harnesdk.svg)

Run major agents and harnesses programmatically, in a sandbox. Openclaw, Claude Code, Hermes agent,...

* [GitHub](https://github.com/alaeddine-13/harnesdk/) | [PyPI](https://pypi.org/project/harnesdk/) | [Documentation](https://alaeddine-13.github.io/harnesdk/)
* Created by [Alaeddine Abdessalem](https://github.com/alaeddine-13) | PyPI [@alaeddineabdessalem](https://pypi.org/user/alaeddineabdessalem/)
* MIT License

## Features

* TODO

## Documentation

Documentation is built with [Zensical](https://zensical.org/) and deployed to GitHub Pages.

* **Live site:** https://alaeddine-13.github.io/harnesdk/
* **Preview locally:** `just docs-serve` (serves at http://localhost:8000)
* **Build:** `just docs-build`

API documentation is auto-generated from docstrings using [mkdocstrings](https://mkdocstrings.github.io/).

Docs deploy automatically on push to `main` via GitHub Actions. To enable this, go to your repo's Settings > Pages and set the source to **GitHub Actions**.

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

Run tests:

```bash
uv run pytest
```

Run quality checks (format, lint, type check, test):

```bash
just qa
```

## Author

harnesdk was created in 2026 by Alaeddine Abdessalem.

Built with [Cookiecutter](https://github.com/cookiecutter/cookiecutter) and the [audreyfeldroy/cookiecutter-pypackage](https://github.com/audreyfeldroy/cookiecutter-pypackage) project template.
