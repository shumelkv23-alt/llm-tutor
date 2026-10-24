"""Загрузка курса в БД: seed-файлы модулей (срез 4.1, модули — срез 24).

Курс — модули mlcourse.ai (сейчас пять, номера до 10), по seed-файлу на модуль
(``data/seed_topicNN.json``). В БД все файлы ложатся ОДНИМ проходом
``replace_seed``: по очереди нельзя — каждый следующий погасил бы предыдущий.
Перед записью курс валидируется целиком: ссылки между модулями, рёбра только
назад (``CourseGraph``), анкета модуля накрывает его темы, циклов нет. Модуль
в формате занятий (у узлов есть разделы, срез 53) проверяется ещё и по
правилам занятий: ``_check_lessons``.

CLI: ``python -m llm_tutor.course.seed [--seed PATH] [--db PATH]``.
"""

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, ValidationError

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Criterion, Edge, Item, Rubric, Topic

# Правила занятий (срез 53, план dense-lessons §3).
MAX_LESSONS = 10  # занятий в модуле
MIN_SECTIONS, MAX_SECTIONS = 2, 4  # разделов в занятии
MIN_SECTION_ITEMS = 2  # заданий на раздел в опубликованном модуле

# Корень проекта (src/llm_tutor/course/seed.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_SEED_PATH = DATA_DIR / "seed_topic01.json"
# Имя seed-файла модуля: seed_topicNN.json (суффикс — для тестовых модулей).
_SEED_NAME_RE = re.compile(r"seed_topic(\d{2})(?:_\w+)?\.json")
# В курс из data/ идут только файлы без суффикса: черновики и бэкапы
# (seed_topic02_draft.json) рядом с модулем в него не попадают.
_COURSE_NAME_RE = re.compile(r"seed_topic\d{2}\.json")
# Номер модуля не из двух цифр (seed_topic2.json) — опечатка, а не черновик:
# молча пропущенный модуль пропал бы из курса (24-L3).
_MISNAMED_RE = re.compile(r"seed_topic(?!\d{2}(?:_\w+)?\.json)\d+(?:_\w+)?\.json")


class LegacyTarget(BaseModel):
    """Куда переехала старая тема: занятие и его раздел (срез 53)."""

    node: str
    section: str


class Seed(BaseModel):
    """Seed-файл модуля: модуль, его граф, банк заданий и рубрики."""

    course: str
    topic: Topic
    nodes: list[Concept]
    edges: list[Edge]
    items: list[Item] = Field(default_factory=list)
    rubrics: list[Rubric] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    # Старая тема → (занятие, раздел); ``None`` — тема убрана без замены.
    # По этой таблице переносится прогресс учеников (срез 56).
    legacy: dict[str, LegacyTarget | None] = Field(default_factory=dict)
    # Конспекты узлов: не из JSON, а из файлов data/theory/<тема>/<узел>.md.
    theory: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class Course:
    """Курс целиком: модули и склеенные разделы их seed-файлов."""

    topics: tuple[Topic, ...]
    nodes: tuple[Concept, ...]
    edges: tuple[Edge, ...]
    items: tuple[Item, ...]
    rubrics: tuple[Rubric, ...]
    criteria: tuple[Criterion, ...]
    # Не ошибки, но автору стоит посмотреть: жёсткие межмодульные рёбра.
    warnings: tuple[str, ...] = ()
    # Конспекты узлов всех модулей (id узла → Markdown).
    theory: dict[str, str] = field(default_factory=dict)
    # Таблица переноса старых тем всех модулей (``Seed.legacy``).
    legacy: dict[str, LegacyTarget | None] = field(default_factory=dict)


class SeedError(ValueError):
    """Курс внутренне несогласован: битые ссылки между разделами или модулями."""


def _check_unique(title: str, ids: Sequence) -> None:
    """Дубли id молча схлопнулись бы в upsert — содержимое разошлось бы с файлом."""
    if len(set(ids)) != len(ids):
        dups = sorted({value for value in ids if ids.count(value) > 1}, key=str)
        raise SeedError(f"Дубли id {title}: {dups}")


def _check_references(
    nodes: Sequence[Concept],
    items: Sequence[Item],
    rubrics: Sequence[Rubric],
    criteria: Sequence[Criterion],
) -> None:
    """Проверяет ссылки между разделами курса.

    Опечатка в id рубрики или концепта иначе всплыла бы сырым ``IntegrityError``
    на старте бота (или, хуже, падением при ответе ученика) — а не подсказкой
    автору, который правит банк руками.
    """
    for title, ids in (
        ("узлов", [node.id for node in nodes]),
        ("рубрик", [rubric.id for rubric in rubrics]),
        ("критериев", [criterion.id for criterion in criteria]),
        ("заданий", [item.id for item in items]),
    ):
        _check_unique(title, ids)

    rubric_ids = {rubric.id for rubric in rubrics}
    node_ids = {node.id for node in nodes}
    for criterion in criteria:
        if criterion.rubric_id not in rubric_ids:
            raise SeedError(
                f"Критерий {criterion.id} ссылается на неизвестную рубрику "
                f"{criterion.rubric_id}"
            )
    for item in items:
        if item.rubric_id is not None and item.rubric_id not in rubric_ids:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестную рубрику {item.rubric_id}"
            )
        unknown = sorted(set(item.concept_weights) - node_ids)
        if unknown:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестные концепты: {unknown}"
            )


def theory_dir_for(seed_path: str | Path) -> Path:
    """Каталог конспектов seed-файла: ``seed_topic01.json`` → ``theory/topic01``."""
    source = Path(seed_path)
    return source.parent / "theory" / source.stem.removeprefix("seed_")


def _read_theory(seed_path: Path, node_ids: list[str]) -> dict[str, str]:
    """Конспекты узлов из файлов; файл без узла в графе не читается."""
    directory = theory_dir_for(seed_path)
    theory: dict[str, str] = {}
    for node_id in node_ids:
        path = directory / f"{node_id}.md"
        if path.is_file():
            # utf-8-sig: BOM от Блокнота иначе спрятал бы первый заголовок (аудит 53, L1).
            text = path.read_text(encoding="utf-8-sig").strip()
            if text:
                theory[node_id] = text
    return theory


def _check_topics(seeds: Sequence[Seed]) -> list[str]:
    """Правила модулей; отдаёт предупреждения (жёсткие межмодульные рёбра).

    Направление рёбер (только назад) проверяет ``CourseGraph`` — здесь его
    повторять незачем.
    """
    _check_unique("модулей", [seed.topic.number for seed in seeds])
    keys = [key for seed in seeds for key in seed.topic.survey.keys]
    if len(set(keys)) != len(keys):
        dups = sorted({key for key in keys if keys.count(key) > 1})
        raise SeedError(f"Дубли ключей анкеты между модулями: {dups}")

    topic_of = {node.id: node.topic_id for seed in seeds for node in seed.nodes}
    warnings: list[str] = []
    for seed in seeds:
        number = seed.topic.number
        if not seed.nodes:
            raise SeedError(f"В модуле {number} нет тем")
        for edge in seed.edges:
            src, dst = topic_of[edge.from_id], topic_of[edge.to_id]
            # Модуль отвечает только за входы в свои темы: иначе файл модуля 2
            # мог бы поменять порядок и блокировки модуля 1.
            if dst != number:
                raise SeedError(
                    f"Ребро {edge.from_id} -> {edge.to_id} в файле модуля {number} "
                    f"ведёт в тему чужого модуля {dst}"
                )
            # Не-requires рёбра в граф не идут, и CourseGraph их не видит:
            # направление проверяем здесь для всех типов (24-L6).
            if src > dst:
                raise SeedError(
                    f"Ребро {edge.from_id} -> {edge.to_id} ведёт вперёд: из модуля "
                    f"{src} в модуль {dst}"
                )
            if edge.type == "requires" and edge.hard and src < dst:
                warnings.append(
                    f"Жёсткое межмодульное ребро {edge.from_id} -> {edge.to_id} "
                    f"(модуль {src} -> {dst})"
                )
        own = {node.id for node in seed.nodes}
        covered = [c for block in seed.topic.survey.blocks for c in block.concepts]
        foreign = sorted(set(covered) - own)
        if foreign:
            raise SeedError(f"Анкета модуля {number} ссылается на чужие темы: {foreign}")
        missing = sorted(own - set(covered))
        if missing:
            raise SeedError(f"Анкета модуля {number} не накрывает темы: {missing}")
        twice = sorted({c for c in covered if covered.count(c) > 1})
        if twice:
            raise SeedError(f"Темы в нескольких блоках анкеты модуля {number}: {twice}")
    return warnings


# Эталон «Объясни новичку» (срез 85): столько идей и загвоздок на модуль.
EXPLAIN_MIN_IDEAS = 5
EXPLAIN_MAX_IDEAS = 8
EXPLAIN_MIN_REQUIRED = 3
EXPLAIN_MIN_PITFALLS = 2


def _check_explain(seeds: Sequence[Seed]) -> list[str]:
    """Эталон «Объясни новичку»: формат — ошибка, отсутствие — предупреждение.

    Как с недописанным содержанием: опубликованный модуль без эталона валиден,
    режим для него просто недоступен.
    """
    warnings: list[str] = []
    for seed in seeds:
        number = seed.topic.number
        explain = seed.topic.explain
        if explain is None:
            if seed.topic.published:
                warnings.append(f"У модуля {number} нет эталона «Объясни новичку»")
            continue
        ideas, pitfalls = explain.ideas, explain.pitfalls
        if not EXPLAIN_MIN_IDEAS <= len(ideas) <= EXPLAIN_MAX_IDEAS:
            raise SeedError(
                f"В эталоне модуля {number} {len(ideas)} идей, нужно "
                f"{EXPLAIN_MIN_IDEAS}–{EXPLAIN_MAX_IDEAS}"
            )
        required = sum(1 for idea in ideas if idea.required)
        if required < EXPLAIN_MIN_REQUIRED:
            raise SeedError(
                f"В эталоне модуля {number} {required} обязательных идей, нужно "
                f"не меньше {EXPLAIN_MIN_REQUIRED}"
            )
        if len(pitfalls) < EXPLAIN_MIN_PITFALLS:
            raise SeedError(
                f"В эталоне модуля {number} {len(pitfalls)} загвоздок, нужно "
                f"не меньше {EXPLAIN_MIN_PITFALLS}"
            )
        _check_unique(f"эталона модуля {number}", [entry.id for entry in [*ideas, *pitfalls]])
        own = {node.id for node in seed.nodes}
        foreign = sorted({entry.lesson for entry in [*ideas, *pitfalls]} - own)
        if foreign:
            raise SeedError(f"Эталон модуля {number} ссылается на чужие занятия: {foreign}")
    return warnings


def main_concept(item: Item) -> str | None:
    """Главный концепт задания — с наибольшим весом (при равенстве — первый).

    Раздел задания — раздел именно этого концепта: по нему задание выдаётся
    в занятии.
    """
    if not item.concept_weights:
        return None
    return max(item.concept_weights, key=item.concept_weights.__getitem__)


# Тот же разбор CommonMark, что рендерит конспект в вебе: заголовок для
# валидатора — ровно то, что ученик увидит заголовком (аудит среза 53, L1).
_MARKDOWN = MarkdownIt("commonmark")


def _h2(text: str) -> list[tuple[str, int]]:
    """Заголовки второго уровня: (текст, номер первой строки)."""
    tokens = _MARKDOWN.parse(text)
    return [
        (tokens[index + 1].content.strip(), token.map[0] if token.map else 0)
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2"
    ]


def theory_headings(text: str) -> list[str]:
    """Заголовки второго уровня конспекта (вне блоков кода, по CommonMark)."""
    return [title for title, _ in _h2(text)]


def section_text(text: str, title: str) -> str | None:
    """Текст раздела конспекта: от ``## <title>`` до следующего h2 (срез 54).

    В промпт идёт текущий раздел, а не весь конспект: иначе длинное занятие не
    влезло бы в ``THEORY_PROMPT_CHARS``. ``None`` — такого заголовка нет.
    """
    headings = _h2(text)
    lines = text.splitlines()
    for index, (heading, start) in enumerate(headings):
        if heading == title:
            end = headings[index + 1][1] if index + 1 < len(headings) else len(lines)
            return "\n".join(lines[start:end]).strip()
    return None


def _theory_section_gaps(node: Concept, text: str) -> list[str]:
    """Разделы, которых нет в конспекте (``## <title>``), — предупреждения.

    Конспект пишется вместе с заданиями: недостающий раздел — недописанное
    содержание, а не ошибка формата.
    """
    headings = theory_headings(text)
    position = 0
    gaps: list[str] = []
    for section in node.sections:
        try:
            position = headings.index(section.title, position) + 1
        except ValueError:
            gaps.append(
                f"В конспекте занятия {node.id} нет заголовка «## {section.title}» "
                f"(или он стоит не по порядку разделов)"
            )
    return gaps


def _section_items(items: Sequence[Item]) -> dict[tuple[str, str], int]:
    """Сколько заданий у каждого раздела: (занятие, раздел) → число."""
    counts: dict[tuple[str, str], int] = {}
    # Погашенное задание не выдаётся — в разделе его нет (аудит среза 53, M1).
    for item in (item for item in items if item.active):
        concept = main_concept(item)
        if concept is not None and item.section is not None:
            counts[(concept, item.section)] = counts.get((concept, item.section), 0) + 1
    return counts


def _check_lessons(seeds: Sequence[Seed], items: Sequence[Item]) -> list[str]:
    """Правила занятий с разделами (план dense-lessons §3).

    Модуль либо целиком в старом формате (темы без разделов, правила занятий
    его не касаются), либо целиком в формате занятий.

    Формат — ошибка (``SeedError``): столько-то разделов, уникальные id и
    названия, задание лежит в разделе своего занятия. Недописанное содержание
    опубликованного модуля — предупреждение: курс пишется по частям, и
    открыть модуль, где ещё не все задания, автор вправе. Ученика
    такая недописанность не запирает: раздел без заданий занятие не держит, а
    итоговое, которого нет, не требуется (``guide.is_node_closed``).
    """
    nodes = {node.id: node for seed in seeds for node in seed.nodes}
    warnings: list[str] = []
    for seed in seeds:
        number = seed.topic.number
        lessons = [node for node in seed.nodes if node.sections]
        if not lessons:
            continue
        flat = sorted(node.id for node in seed.nodes if not node.sections)
        if flat:
            raise SeedError(f"В модуле {number} разделы есть не у всех занятий: нет у {flat}")
        if len(lessons) > MAX_LESSONS:
            raise SeedError(
                f"В модуле {number} {len(lessons)} занятий, а можно не больше {MAX_LESSONS}"
            )
        for node in lessons:
            if not MIN_SECTIONS <= len(node.sections) <= MAX_SECTIONS:
                raise SeedError(
                    f"В занятии {node.id} {len(node.sections)} разделов, "
                    f"а нужно от {MIN_SECTIONS} до {MAX_SECTIONS}"
                )
            _check_unique(f"разделов занятия {node.id}", [s.id for s in node.sections])
            # По названию раздел ищется в конспекте: дубль сделал бы это неоднозначным.
            _check_unique(
                f"(названия) разделов занятия {node.id}", [s.title for s in node.sections]
            )

    for item in items:
        concept = main_concept(item)
        node = nodes.get(concept) if concept is not None else None
        if node is None or not node.sections:
            if item.section is not None or item.final:
                raise SeedError(
                    f"Задание {item.id}: раздел или «итоговое» есть только у заданий "
                    f"занятия с разделами, а главный концепт {concept} — без разделов"
                )
            continue
        if item.final:
            continue
        if item.section is None:
            raise SeedError(f"Задание {item.id} занятия {node.id} без раздела и не итоговое")
        if item.section not in {s.id for s in node.sections}:
            raise SeedError(
                f"Задание {item.id}: раздела {item.section!r} нет в занятии {node.id} "
                f"(раздел задания — раздел его главного концепта)"
            )

    counts = _section_items(items)
    finals = {main_concept(item) for item in items if item.final and item.active}
    for seed in seeds:
        if not seed.topic.published:
            continue
        for node in seed.nodes:
            if not node.sections:
                continue
            for section in node.sections:
                if counts.get((node.id, section.id), 0) < MIN_SECTION_ITEMS:
                    warnings.append(
                        f"В разделе {node.id}/{section.id} опубликованного модуля "
                        f"{seed.topic.number} меньше {MIN_SECTION_ITEMS} заданий: "
                        f"спросить по нему будет почти нечего"
                    )
            if node.id not in finals:
                warnings.append(
                    f"У занятия {node.id} нет итогового задания: "
                    f"закрыть его можно будет по разделам"
                )
            if node.id not in seed.theory:
                warnings.append(f"У занятия {node.id} нет конспекта")
            else:
                warnings.extend(_theory_section_gaps(node, seed.theory[node.id]))
    return warnings


def _merge_legacy(
    seeds: Sequence[Seed], nodes: Sequence[Concept]
) -> dict[str, LegacyTarget | None]:
    """Таблицы переноса модулей в одну; цели проверены, старые id не живые.

    Запись лежит в файле модуля, куда тема переезжает: так один файл модуля
    проверяется и грузится сам по себе (``--seed``, ``load_seed``; аудит
    среза 53, L3). Тема, убранная без замены (``null``), — в файле модуля,
    где она жила.
    """
    by_id = {node.id: node for node in nodes}
    legacy: dict[str, LegacyTarget | None] = {}
    for seed in seeds:
        for old, target in seed.legacy.items():
            if old in legacy:
                raise SeedError(f"Старая тема {old} в таблице переноса нескольких модулей")
            if old in by_id:
                raise SeedError(f"Старая тема {old} из таблицы переноса — живой узел курса")
            if target is not None:
                node = by_id.get(target.node)
                if node is None:
                    raise SeedError(
                        f"Старая тема {old} переносится в неизвестное занятие {target.node}"
                    )
                if node.topic_id != seed.topic.number:
                    raise SeedError(
                        f"Старая тема {old} переносится в занятие {target.node} модуля "
                        f"{node.topic_id}: запись — в файле этого модуля, а не "
                        f"{seed.topic.number}"
                    )
                if target.section not in {section.id for section in node.sections}:
                    raise SeedError(
                        f"Старая тема {old} переносится в неизвестный раздел "
                        f"{target.node}/{target.section}"
                    )
            legacy[old] = target
    return legacy


def _parse_seed(path: str | Path) -> Seed:
    """Читает seed-файл модуля; темам проставляет модуль по файлу."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Seed-файл не найден: {source}")
    try:
        seed = Seed.model_validate(json.loads(source.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as error:
        # Без имени файла автор искал бы опечатку во всех десяти модулях.
        raise SeedError(f"{source.name}: {error}") from error
    number = seed.topic.number
    match = _SEED_NAME_RE.fullmatch(source.name)
    if match and int(match.group(1)) != number:
        raise SeedError(
            f"{source.name}: в файле модуль {number}, а в имени — {int(match.group(1))}"
        )
    seed.theory = _read_theory(source, [node.id for node in seed.nodes])
    return _stamp_topic(seed)


def _stamp_topic(seed: Seed) -> Seed:
    """Модуль темы задаёт файл, а не поле темы: тема не «уедет» в чужой модуль."""
    number = seed.topic.number
    return seed.model_copy(
        update={"nodes": [node.model_copy(update={"topic_id": number}) for node in seed.nodes]}
    )


def build_course(seeds: Sequence[Seed]) -> Course:
    """Склеивает модули и валидирует курс целиком (``SeedError`` на ошибке)."""
    # Модуль проставляется здесь, а не при чтении файла: так его получает и
    # курс, собранный из ``Seed`` напрямую (24-L5).
    ordered = sorted((_stamp_topic(seed) for seed in seeds), key=lambda seed: seed.topic.number)
    nodes = [node for seed in ordered for node in seed.nodes]
    edges = [edge for seed in ordered for edge in seed.edges]
    items = [item for seed in ordered for item in seed.items]
    rubrics = [rubric for seed in ordered for rubric in seed.rubrics]
    criteria = [criterion for seed in ordered for criterion in seed.criteria]
    _check_references(nodes, items, rubrics, criteria)
    CourseGraph(nodes, edges)  # цикл, битое ребро, ребро вперёд между модулями
    warnings = _check_topics(ordered)
    warnings.extend(_check_lessons(ordered, items))
    warnings.extend(_check_explain(ordered))
    legacy = _merge_legacy(ordered, nodes)
    return Course(
        topics=tuple(seed.topic for seed in ordered),
        nodes=tuple(nodes),
        edges=tuple(edges),
        items=tuple(items),
        rubrics=tuple(rubrics),
        criteria=tuple(criteria),
        warnings=tuple(warnings),
        theory={node_id: text for seed in ordered for node_id, text in seed.theory.items()},
        legacy=legacy,
    )


def course_paths(data_dir: Path | None = None) -> list[Path]:
    """Seed-файлы модулей курса (черновики и прочие файлы не берутся)."""
    folder = DATA_DIR if data_dir is None else data_dir
    misnamed = sorted(
        path.name for path in folder.glob("seed_topic*.json") if _MISNAMED_RE.fullmatch(path.name)
    )
    if misnamed:
        raise SeedError(f"Номер модуля в имени — две цифры (seed_topic02.json): {misnamed}")
    return sorted(
        path for path in folder.glob("seed_topic*.json") if _COURSE_NAME_RE.fullmatch(path.name)
    )


def load_course_data(paths: Sequence[str | Path] | None = None) -> Course:
    """Читает и валидирует модули курса (без записи в БД)."""
    chosen = course_paths() if paths is None else [Path(path) for path in paths]
    if not chosen:
        raise FileNotFoundError(f"Нет seed-файлов модулей в {DATA_DIR}")
    return build_course([_parse_seed(path) for path in chosen])


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Читает и валидирует один seed-файл как курс из одного модуля."""
    seed = _parse_seed(path)
    build_course([seed])
    return seed


def apply_course(conn: sqlite3.Connection, course: Course) -> None:
    """Записывает уже прочитанный курс в БД (идемпотентно, истина — seed)."""
    repos.replace_seed(
        conn,
        course.nodes,
        course.edges,
        course.items,
        course.rubrics,
        course.criteria,
        theory=course.theory,
        topics=course.topics,
    )


def load_course(
    conn: sqlite3.Connection, paths: Sequence[str | Path] | None = None
) -> Course:
    """Идемпотентно приводит курс в БД к seed-файлам модулей (истина — seed)."""
    course = load_course_data(paths)
    apply_course(conn, course)
    return course


def load_seed(conn: sqlite3.Connection, path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Один модуль как весь курс — для тестов и отладки одного файла.

    Всё, чего нет в файле, гасится: в том числе другие модули.
    """
    seed = _parse_seed(path)
    apply_course(conn, build_course([seed]))
    return seed


def nodes_without_theory(seed: Seed | Course) -> list[str]:
    """Узлы без конспекта — вкладка «Теория» у них пуста."""
    return [node.id for node in seed.nodes if node.id not in seed.theory]


def items_without_rubric(seed: Seed | Course) -> list[int]:
    """Открытые и код-задания без рубрики: проверить их нечем, но они грузятся.

    Задание не выдаётся (рубрики нет), но автору об этом надо сказать вслух —
    иначе вопрос просто пропадёт из практики.
    """
    return sorted(
        item.id
        for item in seed.items
        if item.answer_type in ("open", "code") and item.rubric_id is None
    )


def nodes_without_items(seed: Seed | Course) -> list[str]:
    """«Несущие» узлы без задания в банке — диагностика их проверить не сможет.

    Возвращаются только узлы с зависимыми: непроверенная база запирает весь
    маршрут, тогда как лист без задания — терпимо.
    """
    graph = CourseGraph(seed.nodes, seed.edges)
    covered = {concept_id for item in seed.items for concept_id in item.concept_weights}
    return sorted(
        node_id
        for node_id in graph.node_ids
        if node_id not in covered and graph.dependents(node_id)
    )


def thin_sections(course: Seed | Course) -> list[str]:
    """Разделы занятий, где заданий меньше ``MIN_SECTION_ITEMS`` (``занятие/раздел``).

    Опубликованный модуль с такими разделами не загрузится; у черновика это
    список того, что осталось написать.
    """
    counts = _section_items(course.items)
    return [
        f"{node.id}/{section.id}"
        for node in course.nodes
        for section in node.sections
        if counts.get((node.id, section.id), 0) < MIN_SECTION_ITEMS
    ]


def lessons_without_final(course: Seed | Course) -> list[str]:
    """Занятия с разделами без итогового задания."""
    finals = {main_concept(item) for item in course.items if item.final and item.active}
    return [node.id for node in course.nodes if node.sections and node.id not in finals]


def main(argv: list[str] | None = None) -> int:
    """CLI: проверить курс (или загрузить его в указанную БД)."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Проверка и загрузка курса в БД.")
    # Курсов в проекте два, и модули у них в разных каталогах: либо один
    # файл, либо каталог модулей одного курса (срез 1 курса ИСИТ).
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--seed",
        default=None,
        help=(
            "один seed-файл модуля — только для отладки: остальные модули в БД "
            "гаснут; по умолчанию — все модули из data/"
        ),
    )
    source.add_argument(
        "--data-dir",
        default=None,
        help="каталог модулей курса (например, data/isit); по умолчанию — data/",
    )
    parser.add_argument(
        "--db",
        # Веб грузит курс в БД ученика сам; CLI — проверка курса (что без
        # заданий, рубрик, конспектов) или загрузка в указанную БД.
        default=":memory:",
        help="путь к БД (по умолчанию — в памяти: только проверка курса)",
    )
    args = parser.parse_args(argv)

    if args.seed is not None:
        paths: list[Path] | None = [Path(args.seed)]
        folder = None
    elif args.data_dir is not None:
        # Относительный путь — от корня проекта: так же ищет БД get_conn.
        folder = Path(args.data_dir)
        folder = folder if folder.is_absolute() else _PROJECT_ROOT / folder
        paths = course_paths(folder)
        if not paths:
            raise FileNotFoundError(f"Нет seed-файлов модулей в {folder}")
    else:
        paths, folder = None, DATA_DIR

    conn = get_conn(args.db)
    try:
        migrate(conn)
        course = load_course(conn, paths)
    finally:
        conn.close()

    print(f"Модули курса: {folder if folder is not None else args.seed}")
    print(
        f"Загружено: {len(course.topics)} модулей, {len(course.nodes)} концептов, "
        f"{len(course.edges)} рёбер, {len(course.items)} заданий"
    )
    for warning in course.warnings:
        print(f"Предупреждение: {warning}")
    missing = nodes_without_items(course)
    if missing:
        print(f"Без заданий (урок их не проверит): {', '.join(missing)}")
    without_rubric = items_without_rubric(course)
    if without_rubric:
        print(f"Открытые задания без рубрики (проверить нечем): {without_rubric}")
    no_theory = nodes_without_theory(course)
    if no_theory:
        print(f"Без конспекта (вкладка «Теория» пуста): {', '.join(no_theory)}")
    thin = thin_sections(course)
    if thin:
        print(f"Разделы, где меньше {MIN_SECTION_ITEMS} заданий: {', '.join(thin)}")
    no_final = lessons_without_final(course)
    if no_final:
        print(f"Занятия без итогового задания: {', '.join(no_final)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
