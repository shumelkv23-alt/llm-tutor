"""Схемы ответов модели на каждом шаге генерации модуля.

Модель не придумывает id и не пишет seed: она отвечает по этим схемам
(``chat_structured`` проверяет их pydantic-ом), а id, граф и файлы собирает код
(``steps``). Ссылки между частями — через ``key``, которые модель придумывает
сама и обязана повторять: по ним код связывает занятие, разделы и анкету.
"""

from typing import Literal

from pydantic import BaseModel, Field

from llm_tutor.course.seed import MAX_LESSONS, MAX_SECTIONS, MIN_SECTIONS


class SectionPlan(BaseModel):
    """Раздел занятия: то, что ученик проходит за один заход."""

    key: str = Field(description="Короткий ключ раздела внутри занятия, например «loc»")
    title: str = Field(description="Название раздела, как оно пойдёт в конспект заголовком")
    goal: str = Field(default="", description="Что ученик сможет после раздела")


class LessonPlan(BaseModel):
    """Занятие модуля."""

    key: str = Field(description="Короткий ключ занятия латиницей, например «selection»")
    name: str = Field(description="Название занятия")
    description: str = Field(default="", description="О чём занятие, одной фразой")
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    sections: list[SectionPlan] = Field(
        description=f"От {MIN_SECTIONS} до {MAX_SECTIONS} разделов, по порядку прохождения"
    )


class ModulePlan(BaseModel):
    """План модуля: из него собирается граф занятий."""

    title: str = Field(description="Название модуля")
    intro: str = Field(default="", description="Вводная для ученика: зачем этот модуль")
    lessons: list[LessonPlan] = Field(
        description=f"От 2 до {MAX_LESSONS} занятий, от простого к сложному"
    )


class BlockPlan(BaseModel):
    """Блок анкеты модуля: тема, о которой спрашиваем на входе."""

    key: str = Field(description="Ключ блока латиницей")
    title: str = Field(description="Название темы в анкете")
    question: str = Field(description="Вопрос ученику про эту тему")
    example: str = Field(default="", description="Пример, который помогает вспомнить")
    lessons: list[str] = Field(description="Ключи занятий, которые закрывает этот блок")


class ModuleSurvey(BaseModel):
    """Анкета модуля: три варианта общего уровня и блоки тем."""

    level_question: str = Field(description="Первый вопрос — общий уровень владения")
    level_options: list[str] = Field(
        description="Ровно три варианта ответа: от «с нуля» до «уверенно»"
    )
    blocks: list[BlockPlan] = Field(description="Темы модуля: одна-две на занятие")


class TheoryDoc(BaseModel):
    """Конспект занятия: markdown с заголовками разделов."""

    markdown: str = Field(
        description="Конспект занятия: h1 — название занятия, дальше h2 — разделы в заданном порядке"
    )


class CriterionPlan(BaseModel):
    """Критерий рубрики: атомарная проверка ответа."""

    criterion: str = Field(description="Одно проверяемое утверждение")
    weight: float = Field(default=1.0, ge=0.0, description="Вес критерия в итоговом балле")
    positive_example: str | None = Field(default=None, description="Ответ, где критерий выполнен")
    negative_example: str | None = Field(default=None, description="Ответ, где критерий нарушен")


class ItemPlan(BaseModel):
    """Задание: с проверяемым ответом или с рубрикой."""

    prompt: str = Field(description="Формулировка задания")
    answer_type: Literal["choice", "short", "code", "open", "spoken"] = Field(
        description="choice и short проверяет код, open, spoken и code — рубрика"
    )
    options: list[str] = Field(default=[], description="Варианты для choice, 3–5 штук")
    answer: str | None = Field(
        default=None,
        description="Для choice — номер верного варианта строкой («0»), для short — эталон",
    )
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    starter: str | None = Field(default=None, description="Заготовка кода для code")
    setup: str | None = Field(default=None, description="Подготовка данных для code")
    tests: str | None = Field(
        default=None,
        description="Проверки для code: assert-ы, падающие на заготовке",
    )
    solution: str | None = Field(
        default=None,
        description="Эталонное решение для code: без него тесты задания нечем проверить",
    )
    criteria: list[CriterionPlan] = Field(
        default=[], description="Рубрика задания: только для open и code"
    )


class SectionItems(BaseModel):
    """Задания раздела: их должно быть не меньше двух."""

    items: list[ItemPlan] = Field(description="От 2 до 4 заданий раздела")


class FinalItem(BaseModel):
    """Итоговое задание занятия: всегда с кодом."""

    item: ItemPlan = Field(description="Задание с кодом, закрывающее занятие")


class SectionItemsPlan(BaseModel):
    """Задания одного раздела в ответе на всё занятие."""

    key: str = Field(description="Ключ раздела ровно как в плане занятия")
    items: list[ItemPlan] = Field(description="От 2 до 4 заданий раздела")


class LessonItems(BaseModel):
    """Все задания занятия одним ответом: разделы по ключам и итоговое.

    Один вызов модели на занятие вместо вызова на каждый раздел и ещё одного
    на итоговое: у модуля из трёх занятий по два раздела было девять вызовов,
    стало три. Меньше ожидания и меньше повторов общего материала в промптах.
    """

    sections: list[SectionItemsPlan] = Field(
        description="По элементу на каждый раздел занятия, в порядке плана"
    )
    final: ItemPlan = Field(description="Итоговое задание с кодом, закрывающее занятие")


# Сколько модулей может быть в курсе: больше — это уже не курс, а программа.
MAX_MODULES = 10


class ModuleOutline(BaseModel):
    """Модуль в структуре курса: о чём он и сколько в нём занятий."""

    number: int = Field(ge=1, le=MAX_MODULES, description="Номер модуля по порядку, с 1")
    title: str = Field(description="Название модуля")
    theme: str = Field(default="", description="Что изучается в модуле, одной-двумя фразами")
    lessons: int = Field(
        default=4, ge=2, le=MAX_LESSONS, description="Сколько занятий в модуле (2–10)"
    )


class CoursePlan(BaseModel):
    """Структура курса: из каких модулей он состоит.

    Составляется **до** модулей: по ней генерация знает, о чём каждый модуль и
    сколько в нём занятий, поэтому модули не повторяют друг друга и идут по
    возрастанию сложности.
    """

    title: str = Field(description="Название курса")
    intro: str = Field(default="", description="Для кого курс и что в нём, для ученика")
    modules: list[ModuleOutline] = Field(description="Модули курса по порядку")
