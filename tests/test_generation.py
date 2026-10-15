"""Конвейер генерации модуля (срезы E–F): шаги, возобновление, сборка seed.

Модель подставная (``ScriptedGenerator``): она отвечает по схеме шага, разбирая
сам запрос — какие разделы занятия просят, такие заголовки и вернёт. Ни сети,
ни ключа тестам не нужно.
"""

import json

import pytest

from llm_tutor.course import store
from llm_tutor.course.build import sources, steps
from llm_tutor.course.build.schemas import (
    BlockPlan,
    CoursePlan,
    ModuleOutline,
    CriterionPlan,
    FinalItem,
    ItemPlan,
    LessonPlan,
    ModulePlan,
    ModuleSurvey,
    SectionItems,
    SectionPlan,
    TheoryDoc,
)
from llm_tutor.course.seed import SeedError, load_course_data
from llm_tutor.llm.client import LLMError
from llm_tutor.web import courses


def _plan(lessons: int = 2, sections: int = 2) -> ModulePlan:
    return ModulePlan(
        title="Основы Python",
        intro="Вводный модуль: переменные, типы и первые шаги.",
        lessons=[
            LessonPlan(
                key=f"l{index + 1}",
                name=f"Занятие {index + 1}",
                description=f"О чём занятие {index + 1}",
                difficulty=0.3,
                sections=[
                    SectionPlan(key=f"s{index + 1}{pos + 1}", title=f"Раздел {index + 1}.{pos + 1}", goal="Цель")
                    for pos in range(sections)
                ],
            )
            for index in range(lessons)
        ],
    )


def _item(index: int, kind: str) -> ItemPlan:
    if kind == "choice":
        return ItemPlan(
            prompt=f"Вопрос {index}: что выведет код?",
            answer_type="choice",
            options=["Верно", "Тоже верно", "Неверно"],
            answer="0",
        )
    return ItemPlan(
        prompt=f"Код {index}: посчитай результат",
        answer_type="code",
        starter="result = None",
        setup="data = [1, 2, 3]",
        tests="assert result == 6",
        solution="result = sum(data)",
        criteria=[CriterionPlan(criterion="Результат посчитан верно", weight=1.0)],
    )


def _asked_headings(request: str) -> list[str]:
    """Заголовки, которые промпт потребовал от конспекта.

    Берём первый непрерывный список ``## …`` после требования: дальше в промпте
    (в починке конспекта) идёт ещё и прежний текст с его заголовками.
    """
    tail = request.split("второго уровня", 1)[-1]
    found: list[str] = []
    for line in tail.splitlines():
        if line.startswith("## "):
            found.append(line[3:].strip())
        elif found:
            break
    return found


class ScriptedGenerator:
    """Подставная модель: отвечает по схеме шага, разбирая сам запрос."""

    def __init__(self, *, lessons: int = 2, sections: int = 2, items: int = 2, fail_on: int | None = None):
        self.lessons = lessons
        self.sections = sections
        self.items = items
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        self.calls.append((schema.__name__, messages[-1].content))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise LLMError("модель недоступна")
        return self._answer(schema, messages[-1].content)

    def _answer(self, schema, request: str):
        name = schema.__name__
        if name == "ModulePlan":
            return _plan(self.lessons, self.sections)
        if name == "ModuleSurvey":
            plan = _plan(self.lessons, self.sections)
            return ModuleSurvey(
                level_question="Что ты уже знаешь про Python?",
                level_options=["Впервые вижу", "Немного знаю", "Уверенно"],
                blocks=[
                    BlockPlan(
                        key=lesson.key,
                        title=lesson.name,
                        question=f"Знаешь ли ты: {lesson.name}?",
                        example="",
                        lessons=[lesson.key],
                    )
                    for lesson in plan.lessons
                ],
            )
        if name == "TheoryDoc":
            titles = _asked_headings(request)
            body = "\n\n".join(f"## {title}\n\nПонемногу про {title}.\n\n```python\nx = 1\n```" for title in titles)
            first = titles[0] if titles else "Занятие"
            return TheoryDoc(markdown=f"# Занятие про {first}\n\n{body}")
        if name == "SectionItems":
            return SectionItems(items=[_item(index, "choice" if index == 1 else "code") for index in range(1, self.items + 1)])
        if name == "FinalItem":
            return FinalItem(item=_item(99, "code"))
        if name == "CoursePlan":
            return CoursePlan(
                title="Основы Python",
                intro="Курс для новичков",
                modules=[
                    ModuleOutline(number=1, title="Переменные", theme="Типы и присваивание", lessons=self.lessons),
                    ModuleOutline(number=2, title="Управление", theme="Условия и циклы", lessons=2),
                ],
            )
        raise AssertionError(f"неожиданная схема: {name}")


@pytest.fixture
def course_root(courses_home):
    """Пустой созданный курс в tmp-каталоге."""
    courses.create_course(title="Основы Python", course_id="python-basics")
    course = courses.get_course("python-basics")
    assert course is not None and course.root is not None
    return course.root


async def generate(root, generator, **kwargs):
    return await steps.generate_module(
        root,
        module=kwargs.pop("module", 1),
        client=generator,
        model="test-model",
        module_title=kwargs.pop("module_title", "Основы Python"),
        **kwargs,
    )


# --- успешный прогон ---


async def test_module_is_assembled_and_validated(course_root):
    generator = ScriptedGenerator()
    state = await generate(course_root, generator)

    assert state.status == "done", state.error
    assert state.done == state.total
    data = load_course_data(list(store.seed_paths(course_root)))
    assert len(data.topics) == 1 and data.topics[0].number == 1
    assert [node.id for node in data.nodes] == ["t01_l1", "t01_l2"]
    assert [section.title for section in data.nodes[0].sections] == ["Раздел 1.1", "Раздел 1.2"]
    # На каждый раздел — свои задания плюс итоговое занятие.
    assert sum(1 for item in data.items if item.final) == 2
    assert all(item.section for item in data.items if not item.final)
    assert len(data.theory) == 2


async def test_module_files_are_written_where_the_course_expects(course_root):
    await generate(course_root, ScriptedGenerator())
    assert (course_root / "seed_topic01.json").is_file()
    theory = (course_root / "theory" / "topic01" / "t01_l1.md").read_text(encoding="utf-8")
    assert theory.startswith("# Занятие про Раздел 1.1")
    assert "## Раздел 1.2" in theory
    # theory в JSON не дублируется: конспекты живут файлами.
    payload = json.loads((course_root / "seed_topic01.json").read_text(encoding="utf-8"))
    assert "theory" not in payload


async def test_run_state_is_persisted(course_root):
    await generate(course_root, ScriptedGenerator())
    saved = steps.load_run(course_root)
    assert saved is not None
    assert saved.status == "done" and saved.module == 1
    assert set(saved.steps.values()) == {"done"}
    assert saved.updated_at > 0


async def test_progress_is_reported_while_running(course_root):
    seen = []
    await generate(course_root, ScriptedGenerator(), on_update=seen.append)
    assert seen and seen[-1].status == "done"
    assert any(state.current == steps.STEP_PLAN for state in seen)


# --- возобновление и сбои ---


async def test_failed_step_keeps_artifacts_and_resumes(course_root):
    broken = ScriptedGenerator(fail_on=3)
    state = await generate(course_root, broken)
    assert state.status == "failed" and state.error
    assert (course_root / "generation").is_dir()

    healthy = ScriptedGenerator()
    resumed = await generate(course_root, healthy)
    assert resumed.status == "done", resumed.error
    # Возобновление с упавшего шага: конспект первого занятия — первый же
    # вызов, а план и анкета взяты готовыми артефактами (три шага из общего
    # списка модель вообще не зовут: план, анкета и сборка).
    assert healthy.calls[0][0] == "TheoryDoc"
    assert len(healthy.calls) == resumed.total - 3


async def test_theory_headings_are_repaired_once(course_root):
    class ForgetfulGenerator(ScriptedGenerator):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.theory_calls = 0

        def _answer(self, schema, request):
            if schema.__name__ == "TheoryDoc":
                self.theory_calls += 1
                if self.theory_calls == 1:
                    return TheoryDoc(markdown="# Занятие\n\n## Не тот раздел\n\nтекст")
            return super()._answer(schema, request)

    state = await generate(course_root, ForgetfulGenerator())
    assert state.status == "done", state.error


class StubbornTheory(ScriptedGenerator):
    """Конспект с чужими заголовками: и заход на починку не помогает."""

    def _answer(self, schema, request):
        if schema.__name__ == "TheoryDoc":
            return TheoryDoc(markdown="# Занятие\n\n## Чужой заголовок\n\nтекст")
        return super()._answer(schema, request)


async def test_theory_that_never_matches_fails_with_headings(course_root):
    state = await generate(course_root, StubbornTheory())
    assert state.status == "failed"
    assert "заголовки разделов не совпали" in (state.error or "")


async def test_too_few_items_fails_the_step(course_root):
    state = await generate(course_root, ScriptedGenerator(items=1))
    assert state.status == "failed"
    assert "нужно не меньше" in (state.error or "")


async def test_plan_with_too_many_lessons_is_rejected(course_root):
    state = await generate(course_root, ScriptedGenerator(lessons=11))
    assert state.status == "failed"
    assert "занятий" in (state.error or "")


async def test_broken_item_does_not_reach_the_course(course_root):
    class BadItems(ScriptedGenerator):
        def _answer(self, schema, request):
            if schema.__name__ == "SectionItems":
                # choice без вариантов: задание не соберётся, и в курс не попадёт.
                return SectionItems(
                    items=[ItemPlan(prompt="Что-то", answer_type="choice"), _item(2, "code")]
                )
            return super()._answer(schema, request)

    state = await generate(course_root, BadItems())
    assert state.status == "failed"
    assert not (course_root / "seed_topic01.json").exists()


async def test_cancelled_run_stops_before_finishing(course_root):
    state = await generate(course_root, ScriptedGenerator(), cancelled=lambda: True)
    assert state.status in ("cancelled", "done")
    assert not (course_root / "seed_topic01.json").exists()


async def test_existing_module_needs_force(course_root):
    await generate(course_root, ScriptedGenerator())
    again = await generate(course_root, ScriptedGenerator())
    assert again.status == "failed" and "уже есть" in (again.error or "")

    forced = await generate(course_root, ScriptedGenerator(), force=True)
    assert forced.status == "done", forced.error


# --- анкета, id, материал ---


async def test_survey_covers_every_lesson(course_root):
    class PartialSurvey(ScriptedGenerator):
        def _answer(self, schema, request):
            if schema.__name__ == "ModuleSurvey":
                answer = super()._answer(schema, request)
                return ModuleSurvey(
                    level_question=answer.level_question,
                    level_options=answer.level_options,
                    blocks=answer.blocks[:1],  # второе занятие модель забыла
                )
            return super()._answer(schema, request)

    state = await generate(course_root, PartialSurvey())
    assert state.status == "done", state.error
    data = load_course_data(list(store.seed_paths(course_root)))
    survey = data.topics[0].survey
    covered = [concept for block in survey.blocks for concept in block.concepts]
    assert sorted(covered) == ["t01_l1", "t01_l2"]


async def test_ids_do_not_repeat_between_modules(course_root):
    await generate(course_root, ScriptedGenerator())
    second = await generate(course_root, ScriptedGenerator(), module=2, module_title="Второй модуль")
    assert second.status == "done", second.error
    data = load_course_data(list(store.seed_paths(course_root)))
    ids = [item.id for item in data.items]
    assert len(ids) == len(set(ids))
    assert len(data.topics) == 2


async def test_material_from_sources_reaches_the_prompt(course_root):
    sources.add_file_source(
        course_root,
        "Пандас.md",
        "# Пандас\n\nГруппировка по городам считается методом groupby.",
        max_bytes=4096,
        max_count=3,
    )
    generator = ScriptedGenerator()
    state = await generate(course_root, generator, module_title="Группировка по городам")
    assert state.status == "done", state.error
    prompts = " ".join(request for _, request in generator.calls)
    assert "Группировка по городам считается методом groupby" in prompts


async def test_module_without_sources_is_still_generated(course_root):
    """Ручной курс без материалов: генерация идёт по названию модуля."""
    state = await generate(course_root, ScriptedGenerator())
    assert state.status == "done", state.error


# --- защита валидацией ---


async def test_failed_second_module_keeps_the_first(course_root):
    """Неудачная генерация второго модуля не трогает готовый первый."""
    await generate(course_root, ScriptedGenerator())
    before = (course_root / "seed_topic01.json").read_text(encoding="utf-8")

    state = await generate(course_root, StubbornTheory(), module=2, module_title="Второй модуль")
    assert state.status == "failed"
    assert (course_root / "seed_topic01.json").read_text(encoding="utf-8") == before
    assert not (course_root / "seed_topic02.json").exists()
    load_course_data(list(store.seed_paths(course_root)))  # курс по-прежнему читается


def test_write_module_validates_before_writing(course_root):
    """Негодный модуль в каталог курса не попадает вовсе."""
    plan = _plan()
    seed = steps.assemble(
        module=1,
        plan=plan,
        survey=ScriptedGenerator()._answer(ModuleSurvey, ""),
        theories={lesson.key: "" for lesson in plan.lessons},  # конспект пустой
        items={
            (lesson.key, section.key): [_item(1, "choice"), _item(2, "code")]
            for lesson in plan.lessons
            for section in lesson.sections
        },
        finals={lesson.key: _item(99, "code") for lesson in plan.lessons},
        allocate=lambda kind, count: list(range(1, count + 1)),
    )
    with pytest.raises(SeedError):
        steps.write_module(course_root, seed)
    assert not (course_root / "seed_topic01.json").exists()
    assert not (course_root / "theory").exists()


async def test_unexpected_error_of_the_model_is_reported(course_root):
    class Exploding(ScriptedGenerator):
        async def chat_structured(self, messages, schema, *, model=None, temperature=None):
            self.calls.append((schema.__name__, messages[-1].content))
            raise RuntimeError("провайдер упал")

    state = await generate(course_root, Exploding())
    assert state.status == "failed" and "провайдер упал" in (state.error or "")


# --- находки аудита срезов G–J ---


async def test_bad_step_answer_is_not_kept_on_disk(course_root):
    """Негодный ответ модели не кэшируется: иначе повтор не спросит её снова.

    Модель, дающая одно задание в разделе, — обычное дело; артефакт такого шага
    оседал бы на диске, и каждый следующий запуск падал бы той же ошибкой, не
    обращаясь к модели (то есть починить модуль из интерфейса было бы нельзя).
    """
    broken = ScriptedGenerator(items=1)
    first = await generate(course_root, broken)
    assert first.status == "failed"

    healthy = ScriptedGenerator()
    second = await generate(course_root, healthy)
    assert second.status == "done", second.error
    assert healthy.calls, "модель обязана быть спрошена заново"


async def test_repeat_module_is_refused_without_calling_the_model(course_root):
    """"Модуль уже есть" — отказ до прогона, а не после десятков вызовов."""
    await generate(course_root, ScriptedGenerator())
    again = ScriptedGenerator()
    state = await generate(course_root, again)
    assert state.status == "failed" and "уже есть" in (state.error or "")
    assert again.calls == []


async def test_survey_block_key_does_not_clash_with_the_level(course_root):
    """Блок анкеты с ключом «level» не должен столкнуться с ключом уровня."""

    class LevelBlock(ScriptedGenerator):
        def _answer(self, schema, request):
            answer = super()._answer(schema, request)
            if schema.__name__ == "ModuleSurvey":
                answer.blocks[0].key = "level"
            return answer

    state = await generate(course_root, LevelBlock())
    assert state.status == "done", state.error
    data = load_course_data(list(store.seed_paths(course_root)))
    keys = [key for block in data.topics[0].survey.blocks for key in [block.key]]
    assert len(set(keys)) == len(keys)


async def test_plan_keys_that_slugify_to_one_id_are_rejected(course_root):
    """Ключи латиницей могут сойтись в один id — это ловится до сборки."""

    class SameKeys(ScriptedGenerator):
        def _answer(self, schema, request):
            answer = super()._answer(schema, request)
            if schema.__name__ == "ModulePlan":
                answer.lessons[0].key = "日语"
                answer.lessons[1].key = "选择"
            return answer

    state = await generate(course_root, SameKeys())
    assert state.status == "failed"
    assert "одинаковые id" in (state.error or "")


async def test_broken_manifest_is_reported_as_a_run_error(course_root):
    """Битый манифест источников — ошибка прогона, а не падение мимо состояния."""
    sources.add_file_source(course_root, "a.md", "# Тема\n\nТекст.", max_bytes=4096, max_count=3)
    (course_root / "sources" / "manifest.json").write_text("{не json", encoding="utf-8")

    state = await generate(course_root, ScriptedGenerator())
    assert state.status == "failed"
    assert "манифест" in (state.error or "").lower() or "Manifest" in (state.error or "")


# --- структура курса (срез L) ---


async def test_course_plan_is_generated_and_saved(course_root):
    generator = ScriptedGenerator()
    plan = await steps.generate_course_plan(
        course_root, client=generator, model="test-model", course_title="Основы Python"
    )
    assert [module.title for module in plan.modules] == ["Переменные", "Управление"]
    assert steps.load_course_plan(course_root) == plan
    assert [name for name, _ in generator.calls] == ["CoursePlan"]


def test_broken_course_plan_is_rejected():
    async def noop():  # pragma: no cover - заглушка для типа
        return None

    for modules in ([ModuleOutline(number=1, title="Один", theme="", lessons=2)],
                    [ModuleOutline(number=1, title="A", theme="", lessons=2),
                     ModuleOutline(number=1, title="B", theme="", lessons=2)],
                    [ModuleOutline(number=1, title="A", theme="", lessons=2),
                     ModuleOutline(number=3, title="B", theme="", lessons=2)]):
        with pytest.raises(steps.StepError):
            steps.check_course_plan(CoursePlan(title="Курс", intro="", modules=list(modules)))


def test_outline_tells_the_module_its_place(course_root):
    plan = CoursePlan(
        title="Основы Python",
        intro="",
        modules=[
            ModuleOutline(number=1, title="Переменные", theme="Типы", lessons=3),
            ModuleOutline(number=2, title="Управление", theme="Условия и циклы", lessons=2),
        ],
    )
    text = steps.outline_for(plan, 2)
    assert "Управление" in text and "Условия и циклы" in text
    assert "занятий в нём: 2" in text
    assert "1. Переменные" in text  # соседние модули видны
    assert steps.outline_for(plan, 9) == "" and steps.outline_for(None, 1) == ""


async def test_module_follows_the_structure(course_root):
    """Число занятий задаёт структура: разошлось — шаг падает с понятным текстом."""
    plan = CoursePlan(
        title="Основы Python",
        intro="",
        modules=[
            ModuleOutline(number=1, title="Переменные", theme="Типы", lessons=3),
            ModuleOutline(number=2, title="Управление", theme="Циклы", lessons=2),
        ],
    )
    state = await generate(course_root, ScriptedGenerator(lessons=2), plan=plan)
    assert state.status == "failed"
    assert "в структуре курса у модуля 3 занятий" in (state.error or "")


async def test_outline_reaches_the_model_prompt(course_root):
    plan = CoursePlan(
        title="Основы Python",
        intro="",
        modules=[
            ModuleOutline(number=1, title="Переменные и типы", theme="Присваивание", lessons=2),
            ModuleOutline(number=2, title="Управление", theme="Циклы", lessons=2),
        ],
    )
    generator = ScriptedGenerator()
    state = await generate(course_root, generator, plan=plan)
    assert state.status == "done", state.error
    first_prompt = generator.calls[0][1]
    assert "Переменные и типы" in first_prompt and "Присваивание" in first_prompt
    assert "Управление" in first_prompt  # соседний модуль тоже в промпте
