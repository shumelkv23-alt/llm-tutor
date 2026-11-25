"""Суть раздела конспекта (срез 106): коротко и только главное, без модели."""

import json
from pathlib import Path

import pytest

from llm_tutor.course.seed import section_text
from llm_tutor.web.gist import GIST_CHARS, answer_gist, clip_sentences, section_gist

DATA = Path(__file__).resolve().parents[1] / "data"


def test_main_subsection_first_paragraph() -> None:
    md = (
        "## Энтропия\n\nВводный абзац раздела, который не о главном.\n\n### Главное\n\n"
        "**Энтропия** — мера неопределённости группы объектов. Чем смешаннее классы, "
        "тем она больше.\n\n### Пример\n\n```python\nx = 1\n```\n"
    )
    assert section_gist(md) == (
        "**Энтропия** — мера неопределённости группы объектов. Чем смешаннее классы, "
        "тем она больше."
    )


def test_without_main_takes_first_paragraph() -> None:
    # Курсы из админки: «Главного» нет; короткий первый абзац дополняется следующим.
    md = "## Протоколы\n\nПротокол — набор методов.\n\nЕщё абзац.\n"
    assert section_gist(md) == "Протокол — набор методов. Ещё абзац."


def test_long_first_paragraph_is_not_extended() -> None:
    first = "Специальные методы — обычные методы с именами вида `__len__`, их зовёт Python."
    md = f"## Протоколы\n\n{first}\n\nВторой абзац не нужен.\n"
    assert section_gist(md) == first


def test_paragraph_lines_are_joined() -> None:
    md = "## Узлы\n\n### Главное\n\nДерево состоит\nиз узлов и листьев, а лист хранит самый частый класс.\n"
    assert section_gist(md) == "Дерево состоит из узлов и листьев, а лист хранит самый частый класс."


def test_colon_then_list_inlines_items() -> None:
    md = (
        '## Groupby\n\n### Главное\n\n`df.groupby("k")` работает в три шага:\n\n'
        "- **разбиение** на группы;\n- **применение** функции;\n"
        "- **объединение** результатов.\n"
    )
    assert section_gist(md) == (
        '`df.groupby("k")` работает в три шага: **разбиение** на группы; '
        "**применение** функции; **объединение** результатов."
    )


def test_colon_list_item_keeps_first_sentence_only() -> None:
    md = (
        "## Осмотр\n\n### Главное\n\nОсмотр таблицы — три шага:\n\n"
        "1. `df.shape` — размер. Подробно: строки и столбцы.\n"
        "2. `df.dtypes` — типы.\n"
    )
    assert section_gist(md) == "Осмотр таблицы — три шага: `df.shape` — размер; `df.dtypes` — типы."


def test_short_label_then_list_inlines_items() -> None:
    # rf_properties/pros_cons: «**Достоинства.**», за ним список, потом «**Недостатки.**».
    md = (
        "## Плюсы\n\n### Главное\n\n**Достоинства.**\n\n- Работает из коробки.\n"
        "- Хорошо параллелится.\n\n**Недостатки.**\n\n- Труднее объяснить.\n"
    )
    assert section_gist(md) == "**Достоинства.** Работает из коробки; Хорошо параллелится."


def test_items_after_colon_start_lowercase() -> None:
    md = (
        "## kNN\n\n### Главное\n\nМетод соседей работает так, как говорит пословица о соседях:\n\n"
        "1. Посчитать расстояние до всех объектов.\n2. Выбрать `k` ближайших.\n3. SQL не трогать.\n"
    )
    assert section_gist(md) == (
        "Метод соседей работает так, как говорит пословица о соседях: посчитать расстояние "
        "до всех объектов; выбрать `k` ближайших; SQL не трогать."
    )


def test_unfinished_sentence_before_formula_takes_next_paragraph() -> None:
    # rf «Декорреляция деревьев»: «…с корреляцией ρ равен», дальше формула.
    md = (
        "## Декорреляция\n\n### Главное\n\nРазброс среднего B деревьев с корреляцией ρ равен\n\n"
        "```\nρσ² + (1 − ρ)σ²/B\n```\n\nЧем меньше корреляция деревьев, тем сильнее усреднение снижает разброс.\n"
    )
    assert section_gist(md) == "Чем меньше корреляция деревьев, тем сильнее усреднение снижает разброс."


def test_colon_then_code_takes_next_paragraph() -> None:
    md = (
        "## Осмотр\n\n### Главное\n\nПять вызовов:\n\n```python\ndf.head()\n```\n\n"
        "Осмотр начинают с размера и типов столбцов, потом смотрят пропуски и выбросы.\n"
    )
    assert section_gist(md) == (
        "Осмотр начинают с размера и типов столбцов, потом смотрят пропуски и выбросы."
    )


def test_list_items_stop_at_limit() -> None:
    items = "".join(f"- пункт номер {n} с пояснением;\n" for n in range(40))
    md = f"## Много\n\n### Главное\n\nВот список:\n\n{items}"
    gist = section_gist(md)
    assert gist is not None and gist.startswith("Вот список: пункт номер 0")
    assert len(gist) <= GIST_CHARS


def test_clip_keeps_whole_sentences_under_limit() -> None:
    text = ("Первое предложение про узлы. " * 20).strip()
    out = clip_sentences(text, 120)
    assert len(out) <= 120
    assert out.endswith("узлы.")


def test_long_single_sentence_clipped_by_word_with_ellipsis() -> None:
    out = clip_sentences(("слово " * 100).strip(), 50)
    assert out.endswith("…")
    assert len(out) <= 51
    assert "  " not in out
    assert out[:-1] == out[:-1].rstrip()


def test_clip_does_not_break_bold_or_code() -> None:
    text = "Начало длинное. " + "**жирный термин который длинный** и `код внутри строки` " * 10
    out = clip_sentences(text.strip(), 60)
    assert out.count("**") % 2 == 0
    assert out.count("`") % 2 == 0


def test_clip_word_cut_does_not_split_bold() -> None:
    out = clip_sentences("**очень длинный жирный термин без точки " + "ещё " * 30 + "конец**", 40)
    assert out.count("**") % 2 == 0
    assert out.endswith("…")


def test_decimal_and_abbreviation_not_sentence_end() -> None:
    text = "Максимум 0,5 при равных долях, т. е. классы поровну. Хвост длинного второго предложения."
    assert clip_sentences(text, 60) == "Максимум 0,5 при равных долях, т. е. классы поровну."


def test_short_text_unchanged() -> None:
    assert clip_sentences("Коротко.", 320) == "Коротко."


def test_only_code_or_missing_gives_none() -> None:
    assert section_gist("## Код\n\n```python\nx = 1\n```\n") is None
    assert section_gist("") is None
    assert section_gist("## Пусто\n") is None


def test_skips_tables_quotes_and_headings() -> None:
    md = "## Т\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> цитата\n\n#### Мелкий\n\nНормальный абзац раздела, достаточно длинный для сути без добавки."
    assert section_gist(md) == "Нормальный абзац раздела, достаточно длинный для сути без добавки."


def test_answer_gist_first_paragraph() -> None:
    answer = "Джини считается без логарифма, поэтому чуть быстрее.\n\n```python\nx\n```\n\nЕщё."
    assert answer_gist(answer) == "Джини считается без логарифма, поэтому чуть быстрее."


def test_answer_gist_skips_leading_list_and_empty() -> None:
    assert answer_gist("- пункт\n- пункт\n\nВывод: пункты важны для понимания темы.") == (
        "Вывод: пункты важны для понимания темы."
    )
    assert answer_gist("") is None
    assert answer_gist("```python\nx\n```") is None


def _real_sections() -> list[tuple[str, str, str]]:
    found = []
    for seed_path in [*sorted(DATA.glob("seed_topic[0-9][0-9].json")), DATA / "demo" / "seed_topic01.json"]:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        theory_dir = seed_path.parent / "theory" / seed_path.stem.removeprefix("seed_")
        for node in seed.get("nodes", []):
            path = theory_dir / f"{node['id']}.md"
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
            for section in node.get("sections", []):
                body = section_text(text, section["title"])
                if body is not None and "### Главное" in body:
                    found.append((f"{node['id']}/{section['id']}", body, section["title"]))
    return found


REAL = _real_sections()


def test_real_sections_found() -> None:
    assert len(REAL) >= 100


@pytest.mark.parametrize(("key", "body", "title"), REAL, ids=[key for key, _, _ in REAL])
def test_every_real_section_has_short_gist(key: str, body: str, title: str) -> None:
    gist = section_gist(body)
    assert gist is not None, key
    assert len(gist) <= GIST_CHARS + 1, (key, len(gist))
    assert not gist.rstrip().endswith(":"), (key, gist)
    assert gist.count("**") % 2 == 0, (key, gist)
    assert gist.count("`") % 2 == 0, (key, gist)
    assert len(gist) >= 40, (key, gist)
