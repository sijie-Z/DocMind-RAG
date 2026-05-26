"""RAG quality gate — validates eval dataset integrity and keyword recall.

This is a lightweight test that requires no external services (no LLM, no ES).
It verifies that the evaluation dataset is well-formed and that context snippets
contain sufficient keyword overlap with expected answers.
"""
import json
import os
from collections import defaultdict

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATASET_PATH = os.path.join(_HERE, "eval_dataset.jsonl")

# Minimum acceptable overall keyword recall
_RECALL_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_dataset(path: str) -> list[dict]:
    """Load JSONL eval dataset, one JSON object per line."""
    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"Invalid JSON on line {lineno}: {e}"
                ) from e
            entries.append(entry)
    return entries


def _extract_keywords(text: str) -> list[str]:
    """Split a keyword string into individual tokens.

    Keywords in expected_answer are space-separated (Chinese and English mixed).
    We split on whitespace and filter empty strings.
    """
    return [kw.strip() for kw in text.split() if kw.strip()]


def _compute_recall(keywords: list[str], context: str) -> tuple[float, int, int]:
    """Compute keyword recall: how many keywords appear in the context.

    Returns (recall, hits, total).
    """
    if not keywords:
        return 1.0, 0, 0
    context_lower = context.lower()
    hits = sum(1 for kw in keywords if kw.lower() in context_lower)
    return hits / len(keywords), hits, len(keywords)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRAGQualityGate:
    """Quality gate for the RAG evaluation dataset."""

    @pytest.fixture(scope="class")
    def dataset(self) -> list[dict]:
        if not os.path.exists(_DATASET_PATH):
            pytest.skip(f"Eval dataset not found: {_DATASET_PATH}")
        return _load_dataset(_DATASET_PATH)

    # -- Schema validation --------------------------------------------------

    def test_dataset_not_empty(self, dataset: list[dict]):
        assert len(dataset) >= 20, (
            f"Expected at least 20 eval entries, got {len(dataset)}"
        )

    def test_required_fields_present(self, dataset: list[dict]):
        required = {"question", "expected_answer", "context", "category"}
        for i, entry in enumerate(dataset):
            missing = required - set(entry.keys())
            assert not missing, (
                f"Entry {i} missing fields: {missing}"
            )

    def test_fields_are_non_empty_strings(self, dataset: list[dict]):
        for i, entry in enumerate(dataset):
            for field in ("question", "expected_answer", "context", "category"):
                val = entry[field]
                assert isinstance(val, str) and val.strip(), (
                    f"Entry {i}, field '{field}' must be a non-empty string"
                )

    def test_context_length_in_range(self, dataset: list[dict]):
        """Context snippets should be 100-300 chars (allow some slack)."""
        for i, entry in enumerate(dataset):
            ctx_len = len(entry["context"])
            assert 50 <= ctx_len <= 500, (
                f"Entry {i}: context length {ctx_len} outside acceptable range [50, 500]"
            )

    def test_valid_categories(self, dataset: list[dict]):
        valid_categories = {"事实检索", "技术概念", "配置参数", "操作流程", "故障排查"}
        for i, entry in enumerate(dataset):
            assert entry["category"] in valid_categories, (
                f"Entry {i}: unknown category '{entry['category']}'"
            )

    # -- Category coverage --------------------------------------------------

    def test_all_categories_covered(self, dataset: list[dict]):
        expected_cats = {"事实检索", "技术概念", "配置参数", "操作流程", "故障排查"}
        present = {e["category"] for e in dataset}
        missing = expected_cats - present
        assert not missing, f"Missing categories: {missing}"

    def test_minimum_per_category(self, dataset: list[dict]):
        counts: dict[str, int] = defaultdict(int)
        for e in dataset:
            counts[e["category"]] += 1
        for cat, count in counts.items():
            assert count >= 3, (
                f"Category '{cat}' has only {count} entries, expected >= 3"
            )

    # -- Keyword recall (core quality metric) -------------------------------

    def test_overall_keyword_recall(self, dataset: list[dict]):
        """Assert that overall keyword recall across all entries exceeds threshold."""
        total_hits = 0
        total_keywords = 0
        for entry in dataset:
            keywords = _extract_keywords(entry["expected_answer"])
            _, hits, n = _compute_recall(keywords, entry["context"])
            total_hits += hits
            total_keywords += n

        assert total_keywords > 0, "No keywords found in any expected_answer"
        overall_recall = total_hits / total_keywords
        assert overall_recall >= _RECALL_THRESHOLD, (
            f"Overall keyword recall {overall_recall:.2%} "
            f"below threshold {_RECALL_THRESHOLD:.0%} "
            f"({total_hits}/{total_keywords} keywords found in context)"
        )

    def test_per_category_keyword_recall(self, dataset: list[dict]):
        """Report per-category recall; no entry category should have zero recall."""
        category_stats: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

        for entry in dataset:
            cat = entry["category"]
            keywords = _extract_keywords(entry["expected_answer"])
            _, hits, n = _compute_recall(keywords, entry["context"])
            prev_hits, prev_total = category_stats[cat]
            category_stats[cat] = (prev_hits + hits, prev_total + n)

        failures = []
        for cat, (hits, total) in sorted(category_stats.items()):
            if total == 0:
                continue
            recall = hits / total
            status = "PASS" if recall >= _RECALL_THRESHOLD else "FAIL"
            line = (
                f"  {cat}: {recall:.2%} ({hits}/{total}) [{status}]"
            )
            if recall < _RECALL_THRESHOLD:
                failures.append(line)
            # Always print for visibility
            print(line)

        assert not failures, (
            f"Categories below {_RECALL_THRESHOLD:.0%} recall:\n"
            + "\n".join(failures)
        )

    def test_no_entry_has_zero_keyword_recall(self, dataset: list[dict]):
        """Every entry should have at least one keyword present in context."""
        zero_recall_entries = []
        for i, entry in enumerate(dataset):
            keywords = _extract_keywords(entry["expected_answer"])
            recall, hits, n = _compute_recall(keywords, entry["context"])
            if n > 0 and recall == 0.0:
                zero_recall_entries.append(
                    f"  Entry {i} [{entry['category']}]: "
                    f"Q={entry['question'][:40]}... "
                    f"keywords={keywords}"
                )

        assert not zero_recall_entries, (
            f"{len(zero_recall_entries)} entries have zero keyword recall:\n"
            + "\n".join(zero_recall_entries)
        )
