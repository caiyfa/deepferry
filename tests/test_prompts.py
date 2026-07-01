"""Tests for the prompt template system (core.prompts).

Verifies that all template files exist, are correctly loaded, and render
expected output for typical inputs.
"""

from __future__ import annotations

import json

import pytest

from deepferry.core.prompts import PromptLoader


@pytest.fixture
def loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def sample_sources() -> list[dict]:
    return [
        {
            "source_id": "main_db",
            "type": "mysql",
            "tables": [
                {
                    "name": "customers",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "name", "type": "VARCHAR(255)"},
                        {"name": "email", "type": "VARCHAR(255)"},
                    ],
                    "row_count": 1000,
                },
                {
                    "name": "orders",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "customer_id", "type": "INTEGER"},
                        {"name": "order_date", "type": "DATETIME"},
                        {"name": "total", "type": "DECIMAL(10,2)"},
                    ],
                    "row_count": 5000,
                },
            ],
        },
        {
            "source_id": "reports",
            "type": "postgresql",
            "tables": [
                {
                    "name": "monthly_summary",
                    "columns": [
                        {"name": "year", "type": "INTEGER"},
                        {"name": "month", "type": "INTEGER"},
                        {"name": "revenue", "type": "DECIMAL(12,2)"},
                    ],
                    "row_count": 36,
                },
            ],
        },
    ]


@pytest.fixture
def sample_few_shot() -> list[dict[str, str]]:
    return [
        {"question": "Get all customers", "sql": "SELECT * FROM customers LIMIT 100"},
        {"question": "Count orders", "sql": "SELECT COUNT(*) FROM orders LIMIT 1"},
    ]


def test_system_txt_is_non_empty(loader: PromptLoader) -> None:
    text = loader.load_system()
    assert len(text) > 0, "system.txt must not be empty"
    assert "SELECT" in text, "system.txt must reference SQL SELECT"


def test_few_shot_has_minimum_entries(loader: PromptLoader) -> None:
    examples = loader.load_few_shot()
    assert len(examples) >= 5, f"Expected at least 5 examples, got {len(examples)}"
    for entry in examples:
        assert "question" in entry, "Each entry must have a 'question' key"
        assert "sql" in entry, "Each entry must have a 'sql' key"
        assert isinstance(entry["question"], str)
        assert isinstance(entry["sql"], str)
        assert len(entry["sql"]) > 0, "SQL must not be empty"


def test_few_shot_is_valid_json() -> None:
    path = PromptLoader()._template_dir / "few_shot.json"
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, list), "few_shot.json must be a JSON array"


def test_schema_renders_table_names(loader: PromptLoader, sample_sources: list[dict]) -> None:
    rendered = loader.render_schema_context(sample_sources)
    assert "main_db" in rendered
    assert "customers" in rendered
    assert "orders" in rendered
    assert "reports" in rendered
    assert "monthly_summary" in rendered


def test_schema_renders_column_names(loader: PromptLoader, sample_sources: list[dict]) -> None:
    rendered = loader.render_schema_context(sample_sources)
    assert "id" in rendered
    assert "name" in rendered
    assert "email" in rendered
    assert "customer_id" in rendered
    assert "total" in rendered
    assert "year" in rendered
    assert "revenue" in rendered
    assert "INTEGER" in rendered
    assert "VARCHAR(255)" in rendered
    assert "DECIMAL" in rendered


def test_schema_renders_row_counts(loader: PromptLoader, sample_sources: list[dict]) -> None:
    rendered = loader.render_schema_context(sample_sources)
    assert "1000 rows" in rendered
    assert "5000 rows" in rendered
    assert "36 rows" in rendered


def test_schema_empty_sources(loader: PromptLoader) -> None:
    rendered = loader.render_schema_context([])
    assert "No schema information" in rendered


def test_explore_renders_question(
    loader: PromptLoader,
    sample_sources: list[dict],
    sample_few_shot: list[dict[str, str]],
) -> None:
    system = loader.load_system()
    schema_text = loader.render_schema_context(sample_sources)
    question = "Show me total revenue by customer for last month"

    prompt = loader.render_explore_prompt(
        system_prompt=system,
        schema_context=schema_text,
        few_shot_examples=sample_few_shot,
        question=question,
    )

    assert question in prompt
    assert "Get all customers" in prompt
    assert "SQL expert" in prompt or "SELECT" in prompt


def test_explore_with_conversation_history(
    loader: PromptLoader,
    sample_sources: list[dict],
    sample_few_shot: list[dict[str, str]],
) -> None:
    system = loader.load_system()
    schema_text = loader.render_schema_context(sample_sources)
    history = [
        {"question": "How many customers?", "answer": "SELECT COUNT(*) FROM customers LIMIT 1"},
    ]

    prompt = loader.render_explore_prompt(
        system_prompt=system,
        schema_context=schema_text,
        few_shot_examples=sample_few_shot,
        question="What about orders?",
        conversation_history=history,
    )

    assert "Previous Conversation" in prompt
    assert "How many customers?" in prompt
    assert "What about orders?" in prompt


def test_explore_without_schema_context(
    loader: PromptLoader, sample_few_shot: list[dict[str, str]]
) -> None:
    system = loader.load_system()

    prompt = loader.render_explore_prompt(
        system_prompt=system,
        schema_context="",
        few_shot_examples=sample_few_shot,
        question="Show me tables",
    )

    assert "Show me tables" in prompt


def test_prompt_not_exceeding_token_limit(
    loader: PromptLoader,
    sample_sources: list[dict],
) -> None:
    system = loader.load_system()
    schema_text = loader.render_schema_context(sample_sources)
    examples = loader.load_few_shot()
    question = "Find all customers who placed more than 5 orders in the last quarter"

    prompt = loader.render_explore_prompt(
        system_prompt=system,
        schema_context=schema_text,
        few_shot_examples=examples,
        question=question,
    )

    estimated_tokens = len(prompt) / 4
    assert estimated_tokens < 4000, (
        f"Prompt estimated at {estimated_tokens:.0f} tokens ({len(prompt)} chars), "
        "exceeds 4000 token budget"
    )
