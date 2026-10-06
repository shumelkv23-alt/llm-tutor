"""Тесты разбора markdown и загрузки чанков (Срез 3.1)."""

from pathlib import Path

import httpx
import respx

from llm_tutor.course.ingest import (
    chunk_markdown,
    ingest_file,
    ingest_text,
    ingest_url,
    main,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- разбор ---


def test_rehash_inside_code_fence_is_not_a_section() -> None:
    """'#' внутри код-блока — комментарий, а не заголовок."""
    md = "# Title\n\n## Real\n\ntext\n\n```python\n# not a heading\nx = 1\n```\n\nmore\n"
    sections = {c.section for c in chunk_markdown(md, "u")}
    assert "Real" in sections
    assert "not a heading" not in sections


def test_figure_directive_is_skipped() -> None:
    md = "# T\n\n```{figure} img.png\n:name: f\n```\n\nполезный текст\n"
    joined = "\n".join(c.content for c in chunk_markdown(md, "u"))
    assert "полезный текст" in joined
    assert "img.png" not in joined
    assert ":name: f" not in joined


def test_code_cell_content_is_kept() -> None:
    md = "# T\n\n```{code-cell} ipython3\nimport pandas as pd\nx = 1\n```\n"
    joined = "\n".join(c.content for c in chunk_markdown(md, "u"))
    assert "import pandas as pd" in joined


def test_frontmatter_is_stripped() -> None:
    md = "---\njupytext:\n  formats: md:myst\n---\n\n# T\n\nhello\n"
    joined = "\n".join(c.content for c in chunk_markdown(md, "u"))
    assert "jupytext" not in joined
    assert "hello" in joined


# --- чанкинг ---


def test_seq_is_sequential_from_zero() -> None:
    md = "\n\n".join(f"## Sect{i}\n\n" + ("слово " * 200) for i in range(5))
    chunks = chunk_markdown(md, "u")
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_long_section_splits_into_multiple_chunks() -> None:
    md = "## Big\n\n" + "\n\n".join("абзац " * 200 for _ in range(5))
    chunks = chunk_markdown(md, "u", max_chars=500)
    assert len(chunks) > 1
    assert all(c.section == "Big" for c in chunks)


def test_chunk_carries_section_and_source() -> None:
    (chunk,) = chunk_markdown("# T\n\n## S\n\ntext here\n", "http://x")
    assert chunk.source_url == "http://x"
    assert chunk.section == "S"


# --- загрузка в БД ---


def test_ingest_text_is_idempotent(conn) -> None:
    """Повторный ingest того же источника не плодит чанки."""
    md = "# T\n\n## S\n\nalpha beta gamma\n"
    first = ingest_text(conn, md, "http://u")
    second = ingest_text(conn, md, "http://u")
    assert first == second
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == first


def test_ingest_text_populates_fts(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\npandas groupby aggregation\n", "http://u")
    hits = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'groupby'"
    ).fetchone()[0]
    assert hits >= 1


def test_ingest_file_reads_utf8(conn, tmp_path) -> None:
    path = tmp_path / "m.md"
    path.write_text("# T\n\n## S\n\nрусский текст\n", encoding="utf-8")
    count = ingest_file(conn, path, source_url="file://m")
    assert count >= 1


def test_sample_fixture_yields_expected_sections(conn) -> None:
    count = ingest_file(conn, FIXTURES / "topic01_sample.md", source_url="fixture://t1")
    assert count >= 2
    sections = {
        row["section"]
        for row in conn.execute("SELECT section FROM chunks").fetchall()
    }
    assert "1. Demonstration" in sections
    assert "2. Churn prediction" in sections
    # код-комментарий не стал разделом
    assert not any("not become a heading" in (s or "") for s in sections)


# --- сеть и CLI ---


@respx.mock
def test_ingest_url_fetches_and_loads(conn) -> None:
    respx.get("http://course/t.md").mock(
        return_value=httpx.Response(200, text="# T\n\n## S\n\ngroupby stuff\n")
    )
    count = ingest_url(conn, "http://course/t.md")
    assert count >= 1
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == count


def test_cli_main_ingests_file(tmp_path, capsys) -> None:
    md = tmp_path / "m.md"
    md.write_text("# T\n\n## S\n\ncontent alpha\n", encoding="utf-8")

    code = main([str(md), "--db", str(tmp_path / "t.db")])

    assert code == 0
    assert "Загружено чанков" in capsys.readouterr().out


@respx.mock
def test_cli_main_ingests_url(tmp_path, capsys) -> None:
    respx.get("http://course/u.md").mock(
        return_value=httpx.Response(200, text="# T\n\n## S\n\nbeta\n")
    )
    code = main(["http://course/u.md", "--db", str(tmp_path / "t.db")])
    assert code == 0
    assert "Загружено чанков" in capsys.readouterr().out


# --- краевые случаи парсинга (по итогам ревью) ---


def test_tilde_fence_is_treated_as_code() -> None:
    """~~~-фенсы тоже код: '#' внутри них — не раздел."""
    md = "# T\n\n~~~python\n# not a heading\ny = 2\n~~~\n"
    chunks = chunk_markdown(md, "u")
    assert any("y = 2" in c.content for c in chunks)
    assert all("not a heading" not in (c.section or "") for c in chunks)


def test_html_source_is_converted_to_markdown(conn) -> None:
    """HTML-страница конвертируется в markdown (bs4 + markdownify)."""
    html = "<html><body><h1>Title</h1><p>Some pandas text</p></body></html>"
    count = ingest_text(conn, html, "http://u")
    assert count >= 1
    content = conn.execute("SELECT content FROM chunks LIMIT 1").fetchone()["content"]
    assert "<h1>" not in content
    assert "pandas text" in content


def test_single_oversized_block_is_split() -> None:
    """Одиночный блок длиннее бюджета режется, а не остаётся гигантским чанком."""
    md = "# T\n\n" + "word " * 2000
    chunks = chunk_markdown(md, "u", max_chars=500)
    assert len(chunks) > 1
    assert max(len(c.content) for c in chunks) <= 500


def test_bom_before_frontmatter_is_stripped() -> None:
    """BOM в начале файла не мешает распознать frontmatter."""
    md = "﻿---\ntitle: x\n---\n\n# T\n\nhello\n"
    joined = "\n".join(c.content for c in chunk_markdown(md, "u"))
    assert "title: x" not in joined
    assert "hello" in joined


def test_section_label_includes_parent_headings() -> None:
    """Подпись секции — путь по уровням ≥2 (Grouping > Sorting)."""
    md = "# Doc\n\n## Grouping\n\n### Sorting\n\nsort text\n"
    (chunk,) = chunk_markdown(md, "u")
    assert chunk.section == "Grouping > Sorting"


def test_ingest_file_idempotent_across_path_forms(conn, tmp_path, monkeypatch) -> None:
    """Один файл под относительным и абсолютным путём — один источник."""
    path = tmp_path / "m.md"
    path.write_text("# T\n\n## S\n\ngroupby\n", encoding="utf-8")

    ingest_file(conn, path)  # абсолютный путь
    monkeypatch.chdir(tmp_path)
    ingest_file(conn, "m.md")  # относительный путь

    assert conn.execute("SELECT count(DISTINCT source_url) FROM chunks").fetchone()[0] == 1
