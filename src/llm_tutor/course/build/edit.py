"""Правка модуля курса руками: чтение seed, проверка всего курса, запись.

Редактор в админке правит модуль как JSON (формы в браузере собирают его из
тех же полей, что в seed-файле). Здесь — единственный вход записи: **сначала
проверка всего курса целиком** (``build_course``), потом запись. Негодный
модуль в каталог не попадает, иначе он сломал бы и уже опубликованные занятия.

Пока курс в черновике, его модули могут быть неполными: содержательные правила
seed (`≥2` задания на раздел, итоговое задание, конспект) применяются только к
модулю с ``published: true``. Публикация (``publish_course``) выставляет флаг
всем модулям сразу и тем самым включает строгую проверку.
"""

import shutil
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.course import store
from llm_tutor.course.seed import (
    Seed,
    SeedError,
    _check_lessons,
    _parse_seed,
    _read_theory,
    _theory_section_gaps,
    build_course,
)


def module_path(root: str | Path, module: int) -> Path:
    """Файл модуля в каталоге курса."""
    return Path(root) / f"seed_topic{module:02d}.json"


def theory_dir(root: str | Path, module: int) -> Path:
    """Папка конспектов модуля (её же читает ``seed._read_theory``)."""
    return Path(root) / "theory" / f"topic{module:02d}"


def module_numbers(root: str | Path) -> list[int]:
    """Номера модулей курса, по возрастанию."""
    return sorted(int(path.name[10:12]) for path in store.seed_paths(root))


def read_module(root: str | Path, module: int) -> dict:
    """Модуль как словарь для редактора (конспекты в него не кладём — они в файлах)."""
    path = module_path(root, module)
    if not path.is_file():
        raise store.StoreError(f"Нет модуля {module} в курсе")
    return store.read_json(path)


def read_theory(root: str | Path, module: int, lesson_id: str) -> str:
    """Конспект занятия (пустая строка — конспекта ещё нет)."""
    path = theory_dir(root, module) / f"{lesson_id}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _validate_course(root: Path, candidate: Seed, target: Path) -> None:
    """Проверяет весь курс с новым модулем вместо прежнего (``SeedError``)."""
    others = [_parse_seed(path) for path in store.seed_paths(root) if path != target]
    build_course([*others, candidate])


def _checked(root: Path, payload: dict, module: int) -> tuple[Seed, Path]:
    """Разбирает присланный модуль и сверяет его с номером из адреса."""
    target = module_path(root, module)
    if not target.is_file():
        raise store.StoreError(f"Нет модуля {module} в курсе")
    try:
        candidate = Seed.model_validate(payload)
    except ValidationError as error:
        raise store.StoreError(f"Модуль не собран: {error}") from error
    if candidate.topic.number != module:
        raise store.StoreError(f"В файле модуль {candidate.topic.number}, а правим {module}")
    # Конспекты приходят из файлов, а не из формы: без них опубликованный модуль
    # не прошёл бы проверку, хотя конспекты на диске есть.
    candidate.theory = _read_theory(target, [node.id for node in candidate.nodes])
    return candidate, target


def write_module(root: str | Path, module: int, payload: dict) -> None:
    """Записывает модуль после проверки всего курса; негодный не сохраняется."""
    root = Path(root)
    candidate, target = _checked(root, payload, module)
    _validate_course(root, candidate, target)  # SeedError, если курс развалится
    before = target.read_text(encoding="utf-8")
    store.write_json(target, candidate.model_dump(mode="json", exclude={"theory"}))
    try:
        # Контрольное чтение с диска: файл должен читаться ровно так же.
        build_course([_parse_seed(path) for path in store.seed_paths(root)])
    except Exception:
        store.atomic_write_text(target, before)
        raise


def write_theory(root: str | Path, module: int, lesson_id: str, markdown: str) -> list[str]:
    """Пишет конспект занятия. Возвращает заголовки разделов из него.

    Проверка — по всему курсу: у опубликованного модуля ``_check_theory_sections``
    требует ``## <название раздела>`` в порядке seed, и конспект, который их не
    содержит, курс бы сломал.
    """
    root = Path(root)
    target = module_path(root, module)
    if not target.is_file():
        raise store.StoreError(f"Нет модуля {module} в курсе")
    seed = _parse_seed(target)
    if lesson_id not in {node.id for node in seed.nodes}:
        raise store.StoreError(f"В модуле {module} нет занятия {lesson_id!r}")

    folder = theory_dir(root, module)
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / f"{lesson_id}.md"
    before = saved.read_text(encoding="utf-8") if saved.is_file() else None
    node = next(node for node in seed.nodes if node.id == lesson_id)
    if seed.topic.published:
        # У опубликованного модуля конспект обязан держать ``## <раздел>`` в
        # порядке seed: загрузка курса это лишь предупредит, а здесь — отказ.
        gaps = _theory_section_gaps(node, markdown)
        if gaps:
            raise SeedError("; ".join(gaps))
    store.atomic_write_text(saved, markdown.strip() + "\n")
    try:
        build_course([_parse_seed(path) for path in store.seed_paths(root)])
    except Exception:
        if before is None:  # конспекта не было — возвращаем как было
            saved.unlink(missing_ok=True)
        else:
            store.atomic_write_text(saved, before)
        raise
    from llm_tutor.course.build.steps import headings

    return headings(markdown)


def _strict_content(seeds: list[Seed]) -> None:
    """Содержательные правила занятий — ошибкой, а не предупреждением.

    Курс у ученика грузится мягко: недописанный раздел или конспект — лишь
    предупреждение (курс пишется по частям). Но публиковать из админки
    недособранное нельзя: преподаватель должен узнать, чего не хватает.
    """
    items = [item for seed in seeds for item in seed.items]
    problems = _check_lessons(seeds, items)
    if problems:
        raise SeedError("; ".join(problems))


def unpublish_course(root: str | Path) -> None:
    """Возвращает модули в черновики: ``topic.published = false``.

    Иначе «снять с публикации, чтобы поправить» не работает: у модуля остаётся
    ``published: true``, а с ним действуют строгие правила seed (≥2 задания на
    раздел, итоговое задание, конспект) — и недособранное занятие не сохранить.
    Флаг лишь снимается, поэтому повторная проверка не нужна.
    """
    for path in store.seed_paths(root):
        seed = _parse_seed(path)
        if not seed.topic.published:
            continue
        lowered = seed.model_copy(
            update={"topic": seed.topic.model_copy(update={"published": False})}
        )
        store.write_json(path, lowered.model_dump(mode="json", exclude={"theory"}))


def publish_course(root: str | Path) -> None:
    """Готовит курс к публикации: у всех модулей ``published: true``.

    Сначала строгая проверка в памяти (она и включает содержательные правила
    seed), и только потом флаг пишется в файлы. Не прошло — курс остаётся
    черновиком, а ошибка уходит преподавателю.
    """
    root = Path(root)
    paths = store.seed_paths(root)
    if not paths:
        raise SeedError("В курсе ещё нет ни одного модуля")
    seeds = []
    for path in paths:
        seed = _parse_seed(path)
        seeds.append(seed.model_copy(update={"topic": seed.topic.model_copy(update={"published": True})}))
    build_course(seeds)  # формат и ссылки
    # Содержание — строго: загрузка курса недописанное лишь предупреждает.
    _strict_content(seeds)
    for path, seed in zip(paths, seeds):
        store.write_json(path, seed.model_dump(mode="json", exclude={"theory"}))


def swap_modules(root: str | Path, a: int, b: int):
    """Меняет два модуля местами — и в структуре курса, и файлами.

    Номер модуля — это номер его файла (``seed_topicNN.json``) и каталога
    конспектов, поэтому «поменять местами» значит переименовать файлы и
    переписать ``topic.number`` внутри. Через временный каталог: иначе при
    обмене первый файл затёр бы второй.
    """
    root = Path(root)
    from llm_tutor.course.build import steps

    plan = steps.load_course_plan(root)
    if plan is None:
        raise store.StoreError("Сначала составьте структуру курса")
    numbers = [module.number for module in plan.modules]
    if a not in numbers or b not in numbers:
        raise store.StoreError(f"В структуре курса нет модулей {a} и {b}")
    if a == b:
        return plan

    staging = root / "generation" / "swap"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for number, target in ((a, b), (b, a)):
            path = module_path(root, number)
            if path.is_file():
                shutil.move(str(path), str(staging / f"seed{target:02d}.json"))
            theory = theory_dir(root, number)
            if theory.is_dir():
                shutil.move(str(theory), str(staging / f"theory{target:02d}"))
        for target in (a, b):
            staged = staging / f"seed{target:02d}.json"
            if staged.is_file():
                payload = store.read_json(staged)
                payload["topic"]["number"] = target
                for node in payload.get("nodes", []):
                    node["topic_id"] = target
                store.write_json(module_path(root, target), payload)
            staged_theory = staging / f"theory{target:02d}"
            if staged_theory.is_dir():
                destination = theory_dir(root, target)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.move(str(staged_theory), str(destination))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    swapped = plan.model_copy(
        update={
            "modules": sorted(
                (
                    module.model_copy(update={"number": b if module.number == a else a})
                    if module.number in (a, b)
                    else module
                    for module in plan.modules
                ),
                key=lambda module: module.number,  # список — всегда по порядку
            )
        }
    )
    steps.save_course_plan(root, swapped)
    return swapped


def structure_problems(root: str | Path, plan) -> list[str]:
    """Что не сходится между структурой и уже собранными модулями.

    Номер модуля — это его файл. Если в структуре под номером N стоит не тот
    модуль, что собран, значит план правили в обход перестановки: так модуль
    потеряется (файл останется, а структура будет врать).
    """
    problems: list[str] = []
    for module in plan.modules:
        path = module_path(root, module.number)
        if not path.is_file():
            continue
        title = _parse_seed(path).topic.title.strip()
        if title != module.title.strip():
            problems.append(
                f"под номером {module.number} уже собран модуль «{title}», "
                f"а в структуре там «{module.title}»"
            )
    for number in missing_modules(root, plan):
        problems.append(f"модуль {number} уже собран, но его нет в структуре")
    return problems


def move_module(root: str | Path, src: int, dst: int) -> None:
    """Переносит модуль с номера ``src`` на ``dst`` (файл, конспекты, номер)."""
    root = Path(root)
    path = module_path(root, src)
    if path.is_file():
        payload = store.read_json(path)
        payload["topic"]["number"] = dst
        for node in payload.get("nodes", []):
            node["topic_id"] = dst
        store.write_json(module_path(root, dst), payload)
        path.unlink()
    source_theory = theory_dir(root, src)
    if source_theory.is_dir():
        destination = theory_dir(root, dst)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(source_theory), str(destination))


def delete_module(root: str | Path, number: int):
    """Убирает модуль из курса: файлы, конспекты и строку структуры.

    Остальные модули сдвигаются на его место — номера остаются подряд с 1, иначе
    структура разошлась бы с файлами. Прогресс учеников не трогаем: события
    журнала остаются, просто занятий этого модуля в курсе больше нет.
    """
    root = Path(root)
    from llm_tutor.course.build import steps

    if not module_path(root, number).is_file():
        raise store.StoreError(f"В курсе нет модуля {number}")
    plan = steps.load_course_plan(root)
    later = sorted(
        int(path.name[10:12]) for path in store.seed_paths(root) if int(path.name[10:12]) > number
    )
    # Сначала убираем сам модуль, и только потом сдвигаем хвост: иначе перенос
    # записал бы следующий модуль поверх удаляемого и тут же его стёр.
    module_path(root, number).unlink(missing_ok=True)
    shutil.rmtree(theory_dir(root, number), ignore_errors=True)
    for higher in later:  # сдвигаем хвост на место удалённого, начиная с ближнего
        move_module(root, higher, higher - 1)

    if plan is not None:
        remaining = [item for item in plan.modules if item.number != number]
        renumbered = [
            item.model_copy(update={"number": item.number - 1 if item.number > number else item.number})
            for item in remaining
        ]
        steps.save_course_plan(
            root,
            plan.model_copy(
                update={"modules": sorted(renumbered, key=lambda item: item.number)}
            ),
        )
    return plan


def missing_modules(root: str | Path, plan) -> list[int]:
    """Номера модулей, которые есть файлами, но пропали из структуры.

    Такое удалять нельзя молча: в модуле может быть чужой труд и прогресс
    учеников.
    """
    saved = {int(path.name[10:12]) for path in store.seed_paths(root)}
    planned = {module.number for module in plan.modules}
    return sorted(saved - planned)
