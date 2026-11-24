"""Версии содержания курса: снимок, список и откат."""

import json
from pathlib import Path

import pytest

from llm_tutor.course import store
from llm_tutor.course.build import versions


def _course(root: Path) -> Path:
    root.mkdir()
    (root / "course.json").write_text('{"id": "demo", "title": "Демо"}', encoding="utf-8")
    (root / "seed_topic01.json").write_text('{"topic": {"number": 1}}', encoding="utf-8")
    theory = root / "theory" / "topic01"
    theory.mkdir(parents=True)
    (theory / "one.md").write_text("# Один\n", encoding="utf-8")
    return root


def test_snapshot_restore_and_safety_version(tmp_path) -> None:
    root = _course(tmp_path / "course")

    first = versions.snapshot(root, "до правки", now=1_000.0)
    (root / "seed_topic01.json").write_text('{"topic": {"number": 2}}', encoding="utf-8")
    (root / "theory" / "topic01" / "two.md").write_text("# Два\n", encoding="utf-8")
    second = versions.snapshot(root, "после правки", now=2_000.0)

    listed = versions.list_versions(root)
    assert [item["id"] for item in listed] == [second["id"], first["id"]]

    restored = versions.restore(root, first["id"], now=3_000.0)

    assert restored["id"] == first["id"]
    assert json.loads((root / "seed_topic01.json").read_text(encoding="utf-8")) == {
        "topic": {"number": 1}
    }
    assert not (root / "theory" / "topic01" / "two.md").exists()
    # Перед откатом сохранено текущее состояние.
    assert any(
        item["label"] == f"перед откатом к {first['id']}"
        for item in versions.list_versions(root)
    )
    assert (root / "versions" / first["id"] / "version.json").is_file()


def test_restore_rejects_path_traversal(tmp_path) -> None:
    root = _course(tmp_path / "course")
    versions.snapshot(root, "v", now=1.0)

    with pytest.raises(versions.VersionError):
        versions.restore(root, "../outside", now=2.0)
    with pytest.raises(versions.VersionError):
        versions.restore(root, "nope", now=2.0)


def test_snapshot_requires_course_json(tmp_path) -> None:
    root = tmp_path / "empty"
    root.mkdir()

    with pytest.raises(versions.VersionError):
        versions.snapshot(root)


def test_restore_keeps_publication_and_id_counters(tmp_path) -> None:
    """Откат содержания не снимает курс с публикации и не откатывает счётчики."""
    root = _course(tmp_path / "course")
    meta = store.read_meta(root)
    meta.status = "draft"
    meta.next_item_id = 10
    meta.next_rubric_id = 20
    meta.next_criterion_id = 30
    store.write_meta(root, meta)
    first = versions.snapshot(root, "черновик", now=1_000.0)

    meta = store.read_meta(root)
    meta.status = "published"
    meta.next_item_id = 50
    meta.next_rubric_id = 60
    meta.next_criterion_id = 70
    store.write_meta(root, meta)

    versions.restore(root, first["id"], now=3_000.0)

    restored = store.read_meta(root)
    assert restored.status == "published"
    assert restored.next_item_id == 50
    assert restored.next_rubric_id == 60
    assert restored.next_criterion_id == 70
    assert restored.updated_at == 3_000.0
    # Содержание версии при этом вернулось.
    assert restored.title == "Демо"


def test_restore_rejects_version_of_another_course(tmp_path) -> None:
    root = _course(tmp_path / "course")
    first = versions.snapshot(root, "версия demo", now=1_000.0)

    meta = store.read_meta(root)
    meta.id = "other"
    store.write_meta(root, meta)

    with pytest.raises(versions.VersionError):
        versions.restore(root, first["id"], now=2_000.0)
