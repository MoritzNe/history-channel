"""LLM script generation (Claude): research -> outline -> draft -> critique -> fact-check."""

from .generate import (
    Critique,
    ScriptGenerator,
    add_cli_arguments,
    cli,
    generate_script,
    load_prompt,
    run_from_args,
)
from .llm import ScriptGenError

__all__ = [
    "Critique",
    "ScriptGenError",
    "ScriptGenerator",
    "add_cli_arguments",
    "cli",
    "generate_script",
    "load_prompt",
    "run_from_args",
]
