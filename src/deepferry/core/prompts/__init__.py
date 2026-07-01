"""Prompt template system for LLM integration.

Provides PromptLoader, a unified interface for loading and rendering prompt
templates used by the Explore mode's natural-language-to-SQL pipeline.

Templates:
    system.txt    — System message instructing the LLM to act as a SQL expert.
    schema.j2     — Jinja2 template for rendering schema context.
    few_shot.json — Example question→SQL pairs for few-shot prompting.
    explore.j2    — Jinja2 template combining all sections into the final prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


class PromptLoader:
    """Loads and renders prompt templates for LLM-based SQL generation.

    All templates are resolved relative to this module's directory using
    ``pathlib.Path``, so the loader works regardless of the current working
    directory.

    Usage::

        loader = PromptLoader()
        system = loader.load_system()
        schema_text = loader.render_schema_context(sources=[...])
        examples = loader.load_few_shot()
        prompt = loader.render_explore_prompt(
            system_prompt=system,
            schema_context=schema_text,
            few_shot_examples=examples,
            question="Show me ...",
        )
    """

    _template_dir: Path
    _jinja_env: Environment

    def __init__(self, template_dir: str | Path | None = None) -> None:
        """Initialise the prompt loader.

        Args:
            template_dir: Optional custom template directory path.
                Defaults to the ``prompts/`` directory alongside this module.
        """
        if template_dir is None:
            self._template_dir = Path(__file__).resolve().parent
        else:
            self._template_dir = Path(template_dir).resolve()

        if not self._template_dir.is_dir():
            raise FileNotFoundError(
                f"Template directory not found: {self._template_dir}"
            )

        self._jinja_env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            autoescape=False,
            keep_trailing_newline=True,
        )

    def load_system(self) -> str:
        """Read the system message template.

        Returns:
            The contents of ``system.txt`` as a string.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        path = self._template_dir / "system.txt"
        if not path.is_file():
            raise FileNotFoundError(f"System prompt template not found: {path}")
        return path.read_text(encoding="utf-8")

    def load_few_shot(self) -> list[dict[str, str]]:
        """Read and parse the few-shot examples.

        Returns:
            A list of dicts, each with ``question`` and ``sql`` keys.

        Raises:
            FileNotFoundError: If the file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        path = self._template_dir / "few_shot.json"
        if not path.is_file():
            raise FileNotFoundError(f"Few-shot examples file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("few_shot.json must contain a JSON array")
        return data

    def render_schema_context(self, sources: list[dict[str, Any]]) -> str:
        """Render the schema context template with source metadata.

        Args:
            sources: A list of source descriptors, each a dict with:
                - ``source_id``: str — unique source identifier.
                - ``type``: str — source type (e.g. "mysql", "postgresql", "http").
                - ``tables``: list of dicts, each with:
                    - ``name``: str — table name.
                    - ``columns``: list of dicts with ``name`` and ``type`` keys.
                    - ``row_count``: optional int — approximate row count.

        Returns:
            Rendered schema context as plain text.
        """
        template = self._jinja_env.get_template("schema.j2")
        return template.render(sources=sources)

    def render_explore_prompt(
        self,
        system_prompt: str,
        schema_context: str,
        few_shot_examples: list[dict[str, str]],
        question: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> str:
        """Render the final combined explore prompt.

        Args:
            system_prompt: System message text (from ``load_system()``).
            schema_context: Rendered schema context (from ``render_schema_context()``).
            few_shot_examples: Parsed few-shot pairs (from ``load_few_shot()``).
            question: The user's natural-language question.
            conversation_history: Optional list of previous Q&A pairs.
                Each entry is a dict with ``question`` and ``answer`` keys.

        Returns:
            The complete prompt string ready to send to the LLM.
        """
        template = self._jinja_env.get_template("explore.j2")
        return template.render(
            system_prompt=system_prompt,
            schema_context=schema_context,
            few_shot_examples=few_shot_examples,
            question=question,
            conversation_history=conversation_history or [],
        )
