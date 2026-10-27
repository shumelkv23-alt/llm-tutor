"""Описания проектов курса — файл ``project.json`` в каталоге модулей (срез 97).

Нет файла — у курса нет проекта. Битый файл — понятная ошибка с именем файла,
как у seed: автор правит его руками.
"""

import json
from collections.abc import Collection
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.schemas import ProjectFile
from llm_tutor.student.project import TEMPLATES

PROJECT_FILE = "project.json"


class ProjectFileError(ValueError):
    """``project.json`` курса не читается или не проходит проверку."""


def load_project_file(directory: Path, node_ids: Collection[str]) -> ProjectFile | None:
    """Описания проектов курса; ``node_ids`` — занятия курса для проверки ссылок."""
    path = Path(directory) / PROJECT_FILE
    if not path.is_file():
        return None
    try:
        project = ProjectFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as error:
        raise ProjectFileError(f"{PROJECT_FILE}: {error}") from error
    if project.template not in TEMPLATES:
        raise ProjectFileError(f"{PROJECT_FILE}: неизвестный шаблон этапов {project.template!r}")
    ids = [brief.id for brief in project.briefs]
    dups = sorted({brief_id for brief_id in ids if ids.count(brief_id) > 1})
    if dups:
        raise ProjectFileError(f"{PROJECT_FILE}: дубли id описаний {dups}")
    known = set(node_ids)
    for brief in project.briefs:
        unknown = sorted(set(brief.lessons) - known)
        if unknown:
            raise ProjectFileError(
                f"{PROJECT_FILE}: описание {brief.id} ссылается на неизвестные занятия {unknown}"
            )
    return project
