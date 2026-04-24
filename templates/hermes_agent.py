from e2b import Template

template = (
    Template()
    # Hermes Agent requires a Python-based environment.
    .from_python_image("3.12")
    .apt_install(["curl", "git", "ripgrep"])
    .run_cmd("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup")
    # Simple install smoke test at build time.
    .run_cmd("hermes --help")
)

# build.py
from e2b import Template, default_build_logger

Template.build(template, 'hermes-agent',
    cpu_count=1,
    memory_mb=1024,
    on_build_logs=default_build_logger(),
)