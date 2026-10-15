"""Конвейер сборки модуля: план → анкета → конспекты → задания → seed.

Генерация — машина состояний на файлах. Каждый шаг сразу пишет свой артефакт в
``<каталог курса>/generation/moduleNN/``, поэтому сбой шага (или перезапуск
сервера) продолжает с упавшего шага, а не начинает модуль заново. Готовый
модуль пишется в каталог курса **только** после того, как весь курс целиком
прошёл ``build_course`` — иначе недособранный модуль сломал бы уже
опубликованный курс.

Id здесь не придумывает модель: концепты и разделы собираются из ``key``,
которые вернула модель, а задания, рубрики и критерии получают номера из
счётчиков курса (``store.allocate``) — монотонных, чтобы перегенерация не
переписала записи журнала ученика.
"""

import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from llm_tutor.course import store
from llm_tutor.course.build import prompts
from llm_tutor.course.build.material import Material
from llm_tutor.course.build.schemas import (
    CoursePlan,
    CriterionPlan,
    FinalItem,
    ItemPlan,
    ModulePlan,
    ModuleSurvey,
    SectionItems,
    TheoryDoc,
)
from llm_tutor.course.seed import (
    MAX_LESSONS,
    MAX_SECTIONS,
    MIN_SECTIONS,
    Seed,
    _parse_seed,
    build_course,
    theory_headings,
)
from llm_tutor.schemas import (
    Concept,
    Criterion,
    Edge,
    Item,
    Rubric,
    Section,
    SurveyBlock,
    SurveyConfig,
    Topic,
)

# Имена шагов. Шаги занятий и разделов включают ключ: по имени видно, что
# именно осталось доделать после сбоя.
STEP_PLAN = "план модуля"
STEP_SURVEY = "анкета модуля"
STEP_ASSEMBLE = "сборка модуля"

GENERATION_DIR = "generation"
RUN_NAME = "run.json"
# Структура курса лежит отдельно от прогонов модулей: она одна на курс и
# переживает любую генерацию.
COURSE_PLAN_NAME = "course_plan.json"
# Заголовок второго уровня в конспекте: по нему занятие понимает, где какой раздел.
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class StepError(ValueError):
    """Шаг не удался: текст показывается преподавателю в прогрессе генерации."""


class RunState(BaseModel):
    """Состояние прогона: его читает интерфейс и оно переживает перезапуск."""

    module: int
    status: Literal["running", "done", "failed", "cancelled"] = "running"
    # Имя шага → статус (pending | running | done).
    steps: dict[str, str] = {}
    current: str = ""
    error: str | None = None
    # Что не так с готовым модулем (например, тесты код-задания не работают):
    # модуль собран, но преподавателю об этом надо сказать.
    warnings: list[str] = []
    updated_at: float = 0.0

    @property
    def done(self) -> int:
        return sum(1 for status in self.steps.values() if status == "done")

    @property
    def total(self) -> int:
        return len(self.steps)

    def view(self) -> dict:
        """Состояние для интерфейса: поля плюс посчитанный прогресс."""
        return {**self.model_dump(), "done": self.done, "total": self.total}


def theory_step(lesson_key: str) -> str:
    return f"конспект: {lesson_key}"


def items_step(lesson_key: str, section_key: str) -> str:
    return f"задания: {lesson_key}/{section_key}"


def final_step(lesson_key: str) -> str:
    return f"итоговое: {lesson_key}"


def plan_artifact(root: str | Path, module: int) -> ModulePlan | None:
    """План модуля из артефактов прогона (``None`` — плана ещё нет)."""
    path = module_dir(root, module) / f"{module:02d}_{store.slugify(STEP_PLAN)}.json"
    if not path.is_file():
        return None
    try:
        return ModulePlan.model_validate(store.read_json(path))
    except (store.StoreError, ValidationError):
        return None


def regenerate_steps(
    root: str | Path,
    module: int,
    *,
    part: str,
    lesson: str = "",
    section: str = "",
) -> list[str]:
    """Какие шаги переделать: «конспект занятия», «задания раздела», «итоговое».

    Занятие и раздел адресуются так, как их видит интерфейс: id занятия
    (``t01_l1``) и id раздела (``sec2``). Внутри шаги называются ключами плана —
    переводим по порядку: занятие N в модуле это N-е занятие плана.
    """
    plan = plan_artifact(root, module)
    if plan is None:
        raise StepError("Модуль ещё не собран — переделывать нечего")
    everything = _step_names(plan)
    if part in ("plan", "all"):
        return everything
    if part == "survey":
        return [STEP_SURVEY]
    if not lesson:
        raise StepError("Не указано занятие")
    ids = [lesson_id(module, item.key) for item in plan.lessons]
    if lesson not in ids:
        raise StepError(f"В модуле нет занятия {lesson!r}")
    index = ids.index(lesson)
    lesson_plan = plan.lessons[index]
    if part == "theory":
        return [theory_step(lesson_plan.key)]
    if part == "final":
        return [final_step(lesson_plan.key)]
    if part == "items":
        if not section:
            raise StepError("Не указан раздел")
        positions = [section_id(pos) for pos in range(len(lesson_plan.sections))]
        if section not in positions:
            raise StepError(f"В занятии {lesson!r} нет раздела {section!r}")
        section_plan = lesson_plan.sections[positions.index(section)]
        return [items_step(lesson_plan.key, section_plan.key)]
    raise StepError(f"Неизвестная часть: {part!r}")


def module_dir(root: str | Path, module: int) -> Path:
    """Папка артефактов прогона (черновики шагов, не курс)."""
    return Path(root) / GENERATION_DIR / f"module{module:02d}"


def run_path(root: str | Path) -> Path:
    return Path(root) / GENERATION_DIR / RUN_NAME


def course_plan_path(root: str | Path) -> Path:
    return Path(root) / GENERATION_DIR / COURSE_PLAN_NAME


def load_course_plan(root: str | Path) -> CoursePlan | None:
    """Структура курса (``None`` — её ещё не составляли)."""
    path = course_plan_path(root)
    if not path.is_file():
        return None
    try:
        return CoursePlan.model_validate(store.read_json(path))
    except (store.StoreError, ValidationError):
        return None


def save_course_plan(root: str | Path, plan: CoursePlan) -> None:
    store.write_json(course_plan_path(root), plan.model_dump())


def check_course_plan(plan: CoursePlan) -> None:
    """Модули идут подряд с 1 — иначе номера разъедутся с файлами модулей."""
    if len(plan.modules) < 2:
        raise StepError("в курсе должно быть хотя бы два модуля")
    numbers = sorted(module.number for module in plan.modules)
    if len(set(numbers)) != len(numbers):
        raise StepError(f"в структуре повторяются номера модулей: {numbers}")
    if numbers != list(range(1, len(numbers) + 1)):
        raise StepError(f"модули должны идти подряд с 1, а в структуре: {numbers}")


def outline_for(plan: CoursePlan | None, module: int) -> str:
    """Место модуля в структуре курса — уходит в промпт плана модуля.

    Без него модуль выходит сам по себе: модель не знает, что было до и что
    будет после, и соседние модули повторяют друг друга.
    """
    if plan is None:
        return ""
    outline = next((item for item in plan.modules if item.number == module), None)
    if outline is None:
        return ""
    neighbours = "; ".join(
        f"{item.number}. {item.title} — {item.theme}" for item in plan.modules
    )
    return (
        f"Место модуля в курсе «{plan.title}»:\n"
        f"- этот модуль: {outline.title} — {outline.theme}\n"
        f"- занятий в нём: {outline.lessons} (ровно столько)\n"
        f"- весь курс по порядку: {neighbours}\n"
    )


async def generate_course_plan(
    root: str | Path,
    *,
    client,
    model: str,
    course_title: str,
    course_description: str = "",
    hints: str = "",
) -> CoursePlan:
    """Составляет структуру курса одним вызовом модели и сохраняет её.

    Один вызов, а не фоновая задача: ответ короткий, и преподаватель ждёт его
    прямо в интерфейсе.
    """
    root = Path(root)
    material = Material.load(root)
    try:
        text = "" if material is None else material.for_query(f"{course_title} {hints}")
        plan = await client.chat_structured(
            prompts.course_plan_messages(
                course_title=course_title,
                course_description=course_description,
                hints=hints,
                material=text,
            ),
            CoursePlan,
            model=model,
        )
    finally:
        if material is not None:
            material.close()
    check_course_plan(plan)
    save_course_plan(root, plan)
    return plan


def load_run(root: str | Path) -> RunState | None:
    """Состояние последнего прогона (``None`` — генераций ещё не было)."""
    path = run_path(root)
    if not path.is_file():
        return None
    try:
        return RunState.model_validate(store.read_json(path))
    except (store.StoreError, ValidationError):
        return None


def save_run(root: str | Path, state: RunState) -> None:
    state.updated_at = time.time()
    store.write_json(run_path(root), state.model_dump())


def _artifact(state: RunState, name: str) -> str:
    """Имя файла артефакта: по имени шага, без пробелов и двоеточий."""
    return f"{state.module:02d}_{store.slugify(name)}.json"


class _Runner:
    """Прогон одного модуля: вызовы модели, артефакты шагов, состояние."""

    def __init__(
        self,
        root: Path,
        *,
        module: int,
        module_title: str,
        hints: str,
        client,
        model: str,
        material: Material | None,
        force: bool,
        regenerate: set[str],
        cancelled: Callable[[], bool],
        on_update: Callable[[RunState], None] | None,
    ) -> None:
        self.root = root
        self.module = module
        self.module_title = module_title
        self.hints = hints
        self.client = client
        self.model = model
        self.material = material
        self.force = force
        # Шаги, которые надо переделать заново (остальные берём из артефактов).
        self.regenerate = regenerate
        self.cancelled = cancelled
        self.on_update = on_update
        self.dir = module_dir(root, module)
        self.state = load_run(root) or RunState(module=module)
        if self.state.module != module:
            self.state = RunState(module=module)

    # --- служебное ---

    def touch(self) -> None:
        self.state.updated_at = time.time()
        save_run(self.root, self.state)
        if self.on_update is not None:
            # Копия, а не сам объект: он меняется дальше, а слушатель должен
            # видеть состояние на момент шага (иначе у него будут одни и те же
            # «последние» данные вместо истории прогресса).
            self.on_update(self.state.model_copy(deep=True))

    def set_step(self, name: str, status: str) -> None:
        self.state.steps[name] = status
        self.state.current = name
        self.touch()

    def stop_check(self) -> None:
        if self.cancelled():
            self.state.status = "cancelled"
            self.state.error = None
            self.touch()

    async def ask(self, name: str, schema, messages, *, force: bool = False, check=None):
        """Ответ модели по схеме шага; готовый артефакт берётся с диска.

        ``check`` — содержательная проверка шага (не только схема). Она решает,
        можно ли **хранить** ответ: негодный ответ модели (одно занятие в плане,
        одно задание в разделе) не осядет на диске, иначе следующий запуск взял
        бы его из кэша, не спросив модель, и падал бы той же ошибкой вечно.
        """
        path = self.dir / _artifact(self.state, name)
        if not force and name not in self.regenerate and path.is_file():
            try:
                answer = schema.model_validate(store.read_json(path))
            except (store.StoreError, ValidationError):
                answer = None  # битый артефакт переделываем
            if answer is not None and self._usable(path, answer, check):
                self.set_step(name, "done")
                return answer

        self.stop_check()
        self.set_step(name, "running")
        try:
            answer = await self.client.chat_structured(messages, schema, model=self.model)
        except Exception as error:  # noqa: BLE001 — текст ошибки нужен преподавателю
            raise StepError(f"{name}: {error}") from error
        try:
            if check is not None:
                check(answer)
        except Exception as error:  # noqa: BLE001 — причина нужна преподавателю
            path.unlink(missing_ok=True)  # негодный ответ на диске не оставляем
            raise StepError(f"{name}: {error}") from error
        store.write_json(path, answer.model_dump())
        self.set_step(name, "done")
        return answer

    @staticmethod
    def _usable(path: Path, answer, check) -> bool:
        """Годится ли **сохранённый** ответ шага; негодный убираем, чтобы переспросить."""
        if check is None:
            return True
        try:
            check(answer)
        except Exception:  # noqa: BLE001 — переспросим модель, причина не нужна
            path.unlink(missing_ok=True)
            return False
        return True

    def material_for(self, query: str) -> str:
        return "" if self.material is None else self.material.for_query(query)


# --- проверки содержимого от модели ---


def _check_plan(plan: ModulePlan, module: int, expected: int | None = None) -> None:
    if not 2 <= len(plan.lessons) <= MAX_LESSONS:
        raise StepError(f"в плане {len(plan.lessons)} занятий, а нужно от 2 до {MAX_LESSONS}")
    if expected is not None and len(plan.lessons) != expected:
        # Число занятий задаёт структура курса: преподаватель видел его, когда
        # принимал структуру, и модуль обязан ей соответствовать.
        raise StepError(
            f"в структуре курса у модуля {expected} занятий, а модель собрала {len(plan.lessons)}"
        )
    keys = [lesson.key for lesson in plan.lessons]
    if len(set(keys)) != len(keys):
        raise StepError("в плане повторяются ключи занятий")
    for lesson in plan.lessons:
        count = len(lesson.sections)
        if not MIN_SECTIONS <= count <= MAX_SECTIONS:
            raise StepError(
                f"в занятии «{lesson.name}» {count} разделов, а нужно от {MIN_SECTIONS} до {MAX_SECTIONS}"
            )
        section_keys = [section.key for section in lesson.sections]
        if len(set(section_keys)) != len(section_keys):
            raise StepError(f"в занятии «{lesson.name}» повторяются ключи разделов")
        titles = [section.title.strip() for section in lesson.sections]
        if len(set(titles)) != len(titles):
            raise StepError(f"в занятии «{lesson.name}» повторяются названия разделов")
    # Ключи латиницей могут сойтись в один id после транслита — тогда занятия
    # столкнутся id-ами уже в сборке курса.
    ids = [lesson_id(module, lesson.key) for lesson in plan.lessons]
    if len(set(ids)) != len(ids):
        raise StepError(f"ключи занятий дают одинаковые id: {ids}")


def _check_headings(answer: TheoryDoc, titles: Sequence[str]) -> None:
    """Конспект обязан нести заголовки разделов ровно в порядке плана."""
    if not _headings_ok(answer.markdown, titles):
        raise StepError(
            "заголовки разделов не совпали с планом "
            f"(нашлись: {', '.join(headings(answer.markdown)) or 'ни одного'})"
        )


def _check_items(answer: SectionItems) -> None:
    """Заданий в разделе должно хватать для публикации модуля."""
    if len(answer.items) < prompts.MIN_ITEMS_PER_SECTION:
        raise StepError(
            f"в разделе {len(answer.items)} заданий, а нужно не меньше "
            f"{prompts.MIN_ITEMS_PER_SECTION}"
        )


def _check_final(answer: FinalItem) -> None:
    """Итоговое задание занятия — всегда с кодом."""
    if answer.item.answer_type != "code":
        raise StepError("итоговое задание занятия должно быть с кодом")


def headings(markdown: str) -> list[str]:
    """Заголовки второго уровня в конспекте — по ним тьютор находит раздел.

    Разбор тот же, что у валидатора курса (``seed.theory_headings``, CommonMark):
    иначе заголовок внутри блока кода прошёл бы проверку шага и упал на сборке.
    """
    return [title.strip() for title in theory_headings(markdown)]


def _headings_ok(markdown: str, titles: Sequence[str]) -> bool:
    return headings(markdown) == [title.strip() for title in titles]


def lesson_id(module: int, key: str) -> str:
    """Id занятия из ключа модели: короткий, латиницей, с номером модуля."""
    return f"t{module:02d}_{store.slugify(key)}"


def section_id(index: int) -> str:
    return f"sec{index + 1}"


# --- сборка seed ---


def _block_key(key: str) -> str:
    """Ключ блока анкеты без столкновения с ключом уровня (``t01_level``)."""
    slug = store.slugify(key)
    return "level-topic" if slug == "level" else slug


def _survey_config(survey: ModuleSurvey, plan: ModulePlan, module: int) -> SurveyConfig:
    """Анкета модуля; неполную анкету достраиваем правилом, а не падаем.

    ``_check_topics`` требует, чтобы блоки накрывали **ровно** занятия модуля:
    пропущенное занятие — ошибка курса. Дотянуть блок на занятие дешевле, чем
    переспрашивать модель и терять весь прогон.
    """
    by_key = {lesson.key: lesson for lesson in plan.lessons}
    # Ключи анкеты уникальны во всём курсе (``_check_topics``), поэтому каждый
    # несёт номер модуля — как в модулях настоящего курса (t02_level, t02_basics).
    prefix = f"t{module:02d}_"
    blocks: list[SurveyBlock] = []
    used: set[str] = set()
    for block in survey.blocks:
        lessons = [key for key in block.lessons if key in by_key and key not in used]
        if not lessons:
            continue
        used.update(lessons)
        blocks.append(
            SurveyBlock(
                key=f"{prefix}{_block_key(block.key)}",
                title=block.title.strip(),
                question=block.question.strip(),
                example=block.example.strip(),
                concepts=tuple(lesson_id(module, key) for key in lessons),
            )
        )
    for lesson in plan.lessons:
        if lesson.key in used:
            continue
        blocks.append(
            SurveyBlock(
                key=f"{prefix}{_block_key(lesson.key)}",
                title=lesson.name,
                question=f"Насколько уверенно ты разбираешься: {lesson.name.lower()}?",
                example="",
                concepts=(lesson_id(module, lesson.key),),
            )
        )
    unique: list[SurveyBlock] = []
    keys: set[str] = set()
    for block in blocks:
        key = block.key
        while key in keys:  # ключи анкеты уникальны во всём курсе
            key = f"{key}-2"
        keys.add(key)
        unique.append(block.model_copy(update={"key": key}))
    blocks = unique
    options = [option.strip() for option in survey.level_options if option.strip()]
    while len(options) < 3:  # ровно три варианта уровня — правило схемы
        options.append(("Впервые вижу", "С подсказками", "Уверенно")[len(options)])
    return SurveyConfig(
        level_key=f"{prefix}level",
        level_question=survey.level_question.strip() or f"Что ты уже знаешь про {plan.title.lower()}?",
        level_options=tuple(options[:3]),
        blocks=tuple(blocks),
    )


def _item(
    plan: ItemPlan,
    *,
    item_id: int,
    lesson: str,
    section: str | None,
    rubric_id: int | None,
    final: bool,
) -> Item:
    """Задание из плана модели; кривое задание — ошибка шага, а не мусор в курсе."""
    try:
        return Item(
            id=item_id,
            prompt=plan.prompt.strip(),
            answer_type=plan.answer_type,
            concept_weights={lesson: 1.0},
            difficulty=plan.difficulty,
            options=[option.strip() for option in plan.options],
            answer=plan.answer.strip() if isinstance(plan.answer, str) else plan.answer,
            rubric_id=rubric_id,
            starter=plan.starter,
            setup=plan.setup,
            tests=plan.tests,
            solution=plan.solution,
            section=section,
            final=final,
        )
    except ValidationError as error:
        raise StepError(f"задание «{plan.prompt[:60]}»: {error}") from error


def assemble(
    *,
    module: int,
    plan: ModulePlan,
    survey: ModuleSurvey,
    theories: dict[str, str],
    items: dict[tuple[str, str], list[ItemPlan]],
    finals: dict[str, ItemPlan],
    allocate: Callable[[str, int], list[int]],
) -> Seed:
    """Собирает seed модуля из ответов модели (без обращения к диску)."""
    nodes: list[Concept] = []
    edges: list[Edge] = []
    for index, lesson in enumerate(plan.lessons):
        node_id = lesson_id(module, lesson.key)
        nodes.append(
            Concept(
                id=node_id,
                name=lesson.name.strip(),
                difficulty=lesson.difficulty,
                description=lesson.description.strip() or None,
                topic_id=module,
                sections=[
                    Section(id=section_id(position), title=section.title.strip())
                    for position, section in enumerate(lesson.sections)
                ],
            )
        )
        if index:
            # Мягкая связь «следующее занятие опирается на предыдущее»: блокировать
            # маршрут она не должна — это подсказка планировщику, а не пререквизит.
            edges.append(
                Edge(
                    from_id=lesson_id(module, plan.lessons[index - 1].key),
                    to_id=node_id,
                    type="requires",
                    hard=False,
                    weight=0.3,
                )
            )

    rubrics: list[Rubric] = []
    criteria: list[Criterion] = []
    built: list[Item] = []
    for lesson in plan.lessons:
        node_id = lesson_id(module, lesson.key)
        for position, section in enumerate(lesson.sections):
            plans = items.get((lesson.key, section.key), [])
            if len(plans) < prompts.MIN_ITEMS_PER_SECTION:
                raise StepError(
                    f"раздел «{section.title}» занятия «{lesson.name}»: "
                    f"{len(plans)} заданий, а нужно не меньше {prompts.MIN_ITEMS_PER_SECTION}"
                )
            for item_plan in plans:
                rubric_id = _add_rubric(item_plan, rubrics, criteria, allocate)
                item_id = allocate("item", 1)[0]
                built.append(
                    _item(
                        item_plan,
                        item_id=item_id,
                        lesson=node_id,
                        section=section_id(position),
                        rubric_id=rubric_id,
                        final=False,
                    )
                )
        final_plan = finals[lesson.key]
        rubric_id = _add_rubric(final_plan, rubrics, criteria, allocate)
        item_id = allocate("item", 1)[0]
        built.append(
            _item(final_plan, item_id=item_id, lesson=node_id, section=None, rubric_id=rubric_id, final=True)
        )

    return Seed(
        course=plan.title,
        topic=Topic(
            number=module,
            title=plan.title.strip(),
            intro=plan.intro.strip(),
            survey=_survey_config(survey, plan, module),
            # Модуль сгенерирован целиком: содержательные правила seed он обязан
            # пройти сразу, иначе в курс попадёт занятие без итогового задания.
            published=True,
        ),
        nodes=nodes,
        edges=edges,
        items=built,
        rubrics=rubrics,
        criteria=criteria,
        theory={
            lesson_id(module, lesson.key): theories[lesson.key].strip() for lesson in plan.lessons
        },
    )


def _add_rubric(
    plan: ItemPlan, rubrics: list[Rubric], criteria: list[Criterion], allocate
) -> int | None:
    """Рубрика задания (``None`` — задание проверяется кодом без рубрики)."""
    if plan.answer_type not in ("open", "code") or not plan.criteria:
        return None
    rubric_id = allocate("rubric", 1)[0]
    rubrics.append(Rubric(id=rubric_id, name=f"Рубрика: {plan.prompt.strip()[:60]}"))
    for criterion in plan.criteria:
        criteria.append(_criterion(criterion, rubric_id, allocate))
    return rubric_id


def _criterion(plan: CriterionPlan, rubric_id: int, allocate) -> Criterion:
    return Criterion(
        id=allocate("criterion", 1)[0],
        rubric_id=rubric_id,
        criterion=plan.criterion.strip(),
        weight=plan.weight,
        positive_example=plan.positive_example,
        negative_example=plan.negative_example,
    )


# --- запись готового модуля ---


def write_module(root: str | Path, seed: Seed, *, force: bool = False) -> Path:
    """Пишет модуль в каталог курса, проверив **весь** курс целиком.

    Сначала проверка в памяти (с новым модулем и уже готовыми), потом запись и
    контрольное чтение с диска. Негодный модуль в каталог не попадает: он сломал
    бы и уже опубликованные занятия курса.
    """
    root = Path(root)
    target = root / f"seed_topic{seed.topic.number:02d}.json"
    # Сначала «такой модуль уже есть», потом всё остальное: иначе вместо
    # понятного отказа преподаватель получил бы «дубль id узла».
    before = target.read_text(encoding="utf-8") if target.exists() else None

    # Перегенерация заменяет свой файл: в сборке курса он не участвует, иначе
    # новый модуль столкнулся бы с прежним по id узлов.
    others = [_parse_seed(path) for path in store.seed_paths(root) if path != target]
    build_course([*others, seed])  # SeedError, если модуль ломает курс

    theory_dir = root / "theory" / f"topic{seed.topic.number:02d}"
    theory_dir.mkdir(parents=True, exist_ok=True)
    for node_id, text in seed.theory.items():
        store.atomic_write_text(theory_dir / f"{node_id}.md", text)
    store.write_json(target, seed.model_dump(mode="json", exclude={"theory"}))

    try:
        build_course([_parse_seed(path) for path in store.seed_paths(root)])
    except Exception:
        # Возвращаем прежнее содержимое (или убираем файл, если его не было):
        # при перегенерации поверх готового модуля удаление потеряло бы его.
        if before is None:
            target.unlink(missing_ok=True)
        else:
            store.atomic_write_text(target, before)
        raise
    return target


# --- прогон ---


async def generate_module(
    root: str | Path,
    *,
    module: int,
    client,
    model: str,
    module_title: str = "",
    hints: str = "",
    plan: CoursePlan | None = None,
    regenerate: Sequence[str] = (),
    force: bool = False,
    cancelled: Callable[[], bool] | None = None,
    on_update: Callable[[RunState], None] | None = None,
) -> RunState:
    """Собирает модуль курса из источников. Возвращает состояние прогона."""
    root = Path(root)
    target = root / f"seed_topic{module:02d}.json"
    # Переделка куска — это правка уже собранного модуля: файл на месте, и это
    # нормально.
    partial = bool(regenerate)
    if target.exists() and not force and not partial:
        # Отказ сразу, а не после всего прогона: иначе за гарантированную
        # ошибку платили бы десятками вызовов модели.
        state = RunState(
            module=module,
            status="failed",
            error=(
                f"Модуль {module} уже есть. Правьте его в редакторе или включите "
                "«перегенерировать заново» — тогда он соберётся с нуля."
            ),
        )
        save_run(root, state)
        return state
    material = None
    runner = _Runner(
        root,
        module=module,
        module_title=module_title,
        hints=hints,
        client=client,
        model=model,
        material=material,
        force=force,
        regenerate=set(regenerate),
        cancelled=cancelled or (lambda: False),
        on_update=on_update,
    )
    if STEP_PLAN in runner.regenerate:
        # План переписывают — всё, что по нему собрано, больше не подходит:
        # занятия, разделы и задания станут другими. Чистим артефакты, кроме
        # самого плана.
        for old in runner.dir.glob("*.json"):
            if old.name != f"{module:02d}_{store.slugify(STEP_PLAN)}.json":
                old.unlink()
    state = runner.state
    state.status = "running"
    state.error = None
    try:
        runner.material = Material.load(root)
        outline = outline_for(plan, module)
        entry = next(
            (item for item in plan.modules if item.number == module), None
        ) if plan is not None else None
        expected = None if entry is None else entry.lessons
        outline_title = "" if entry is None else entry.title
        plan_ = await runner.ask(
            STEP_PLAN,
            ModulePlan,
            prompts.plan_messages(
                module_number=module,
                module_title=module_title or f"Модуль {module}",
                hints=hints,
                material=runner.material_for(module_title or hints),
                outline=outline,
            ),
            force=force,
            check=lambda answer: _check_plan(answer, module, expected),
        )
        if outline_title:
            # Название модуля утвердил преподаватель в структуре курса: модель
            # может предложить своё, но в курсе остаётся принятое.
            plan_ = plan_.model_copy(update={"title": outline_title})
        state.steps = {name: state.steps.get(name, "pending") for name in _step_names(plan_)}
        runner.touch()

        survey = await runner.ask(
            STEP_SURVEY,
            ModuleSurvey,
            prompts.survey_messages(
                module_title=plan_.title, lessons=[(l.key, l.name) for l in plan_.lessons]
            ),
            force=force,
        )

        theories: dict[str, str] = {}
        for lesson in plan_.lessons:
            titles = [section.title for section in lesson.sections]
            name = theory_step(lesson.key)
            doc = await runner.ask(
                name,
                TheoryDoc,
                prompts.theory_messages(
                    module_title=plan_.title,
                    lesson_name=lesson.name,
                    sections=titles,
                    material=runner.material_for(f"{lesson.name} {' '.join(titles)}"),
                ),
                # Без строгой проверки: заголовки чинит отдельный заход ниже.
                force=force,
            )
            if not _headings_ok(doc.markdown, titles):
                # Один заход на починку: заголовки разделов — не украшение, по ним
                # тьютор ведёт занятие по частям.
                doc = await runner.ask(
                    name,
                    TheoryDoc,
                    prompts.theory_repair_messages(
                        lesson_name=lesson.name, headings=titles, markdown=doc.markdown
                    ),
                    force=True,
                    check=lambda answer, titles=titles: _check_headings(answer, titles),
                )
                if not _headings_ok(doc.markdown, titles):
                    raise StepError(
                        f"конспект занятия «{lesson.name}»: заголовки разделов не совпали с планом "
                        f"({', '.join(headings(doc.markdown)) or 'заголовков нет'})"
                    )
            theories[lesson.key] = doc.markdown

        items: dict[tuple[str, str], list[ItemPlan]] = {}
        for lesson in plan_.lessons:
            for section in lesson.sections:
                answer = await runner.ask(
                    items_step(lesson.key, section.key),
                    SectionItems,
                    prompts.items_messages(
                        lesson_name=lesson.name,
                        section_title=section.title,
                        goal=section.goal,
                        material=runner.material_for(f"{lesson.name} {section.title}"),
                    ),
                    force=force,
                    check=_check_items,
                )
                if len(answer.items) < prompts.MIN_ITEMS_PER_SECTION:
                    raise StepError(
                        f"раздел «{section.title}»: модель дала {len(answer.items)} заданий, "
                        f"нужно не меньше {prompts.MIN_ITEMS_PER_SECTION}"
                    )
                items[(lesson.key, section.key)] = answer.items

        finals: dict[str, ItemPlan] = {}
        for lesson in plan_.lessons:
            answer = await runner.ask(
                final_step(lesson.key),
                FinalItem,
                prompts.final_messages(
                    lesson_name=lesson.name,
                    sections=[section.title for section in lesson.sections],
                    material=runner.material_for(f"{lesson.name} итоговое задание"),
                ),
                force=force,
                check=_check_final,
            )
            if answer.item.answer_type != "code":
                raise StepError(f"итоговое задание занятия «{lesson.name}»: нужен код")
            finals[lesson.key] = answer.item

        runner.stop_check()
        if state.status == "cancelled":
            return state
        runner.set_step(STEP_ASSEMBLE, "running")
        seed = assemble(
            module=module,
            plan=plan_,
            survey=survey,
            theories=theories,
            items=items,
            finals=finals,
            allocate=lambda kind, count: store.allocate_ids(root, kind, count),
        )
        write_module(root, seed, force=force or partial)
        runner.set_step(STEP_ASSEMBLE, "done")
        # Проверяем код-задания запуском: тесты могли не заработать, а ученик
        # упрётся в это первым.
        from llm_tutor.course.build import check

        state.warnings = [problem.as_text() for problem in check.check_module(root, module)]
        state.status = "done"
    except StepError as error:
        state.status = "failed"
        state.error = str(error)
    except Exception as error:  # noqa: BLE001 — прогон не должен падать молча
        state.status = "failed"
        state.error = f"неожиданная ошибка: {error}"
    finally:
        if material is not None:
            material.close()
        runner.touch()
    return state


def _step_names(plan: ModulePlan) -> list[str]:
    """План шагов модуля — по нему интерфейс считает прогресс."""
    names = [STEP_PLAN, STEP_SURVEY]
    for lesson in plan.lessons:
        names.append(theory_step(lesson.key))
        names.extend(items_step(lesson.key, section.key) for section in lesson.sections)
        names.append(final_step(lesson.key))
    names.append(STEP_ASSEMBLE)
    return names
