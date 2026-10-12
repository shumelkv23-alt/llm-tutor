"""Курс в старом формате (103 темы) для тестов движка (срез 56).

Курс в data/ перестроен в занятия с разделами, а движок по-прежнему умеет
темы без разделов (план §1.7). Тесты ядра и ученика, написанные на темах
(``groupby``, ``python_basics``…), идут на замороженной копии старых seed —
``tests/fixtures/legacy_v1``: их утверждения сохраняются как есть. Тесты
данных курса и веба — на новых занятиях.

Те же имена, что в ``llm_tutor.course.seed``, но по умолчанию — старые файлы.
"""

from collections.abc import Sequence
from pathlib import Path

from llm_tutor.course import seed as _seed

LEGACY_DIR = Path(__file__).parent / "fixtures" / "legacy_v1"
DEFAULT_SEED_PATH = LEGACY_DIR / "seed_topic01.json"
LEGACY_PATHS = sorted(LEGACY_DIR.glob("seed_topic0?.json"))


def course_paths(data_dir: Path | None = None) -> list[Path]:
    return _seed.course_paths(LEGACY_DIR if data_dir is None else data_dir)


def load_seed(conn, path: str | Path = DEFAULT_SEED_PATH) -> _seed.Seed:
    return _seed.load_seed(conn, path)


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> _seed.Seed:
    return _seed.load_seed_data(path)


def load_course(conn, paths: Sequence[str | Path] | None = None) -> _seed.Course:
    return _seed.load_course(conn, LEGACY_PATHS if paths is None else paths)


def load_course_data(paths: Sequence[str | Path] | None = None) -> _seed.Course:
    return _seed.load_course_data(LEGACY_PATHS if paths is None else paths)
