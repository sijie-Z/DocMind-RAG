from __future__ import annotations

from pathlib import Path

from scripts.make_fixture_docs import copy_fixtures, resolve_source, select_documents


def _make(root: Path, name: str, size: int = 10) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


class TestSelectDocuments:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert select_documents(tmp_path / "nope") == []

    def test_md_before_docx_before_pdf(self, tmp_path: Path) -> None:
        _make(tmp_path, "b.pdf")
        _make(tmp_path, "a.docx")
        _make(tmp_path, "c.md")
        got = [p.suffix for p in select_documents(tmp_path)]
        assert got == [".md", ".docx", ".pdf"]

    def test_limit_respected(self, tmp_path: Path) -> None:
        for i in range(5):
            _make(tmp_path, f"{i}.md")
        assert len(select_documents(tmp_path, limit=3)) == 3

    def test_oversized_and_empty_skipped(self, tmp_path: Path) -> None:
        _make(tmp_path, "big.md", 2048)
        _make(tmp_path, "empty.md", 0)
        _make(tmp_path, "ok.md", 10)
        got = [p.name for p in select_documents(tmp_path, max_bytes=1024)]
        assert got == ["ok.md"]

    def test_unsupported_extensions_ignored(self, tmp_path: Path) -> None:
        _make(tmp_path, "a.txt")
        _make(tmp_path, "b.html")
        assert select_documents(tmp_path) == []

    def test_stable_order(self, tmp_path: Path) -> None:
        for n in ["c.md", "a.md", "b.md"]:
            _make(tmp_path, n)
        assert [p.name for p in select_documents(tmp_path)] == ["a.md", "b.md", "c.md"]


class TestCopyFixtures:
    def test_prefixed_and_copied(self, tmp_path: Path) -> None:
        src = _make(tmp_path / "src", "doc.md", 5)
        dest = tmp_path / "out"
        written = copy_fixtures([src], dest)
        assert written[0].name == "01_doc.md"
        assert written[0].read_bytes() == b"x" * 5


class TestResolveSource:
    def test_none_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("DOCMIND_FIXTURE_SRC", raising=False)
        assert resolve_source(None) is None

    def test_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert resolve_source(str(tmp_path / "missing")) is None
