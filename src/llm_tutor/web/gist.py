"""Суть раздела конспекта: коротко и только главное, без модели (срез 106).

Конспект ученика собирается сам (страница «Конспект»), а суть каждого
пройденного раздела берётся из конспекта курса: первый абзац подраздела
``### Главное`` (у курсов из админки его нет — первый абзац раздела). Абзац
режется по целым предложениям до ``GIST_CHARS`` знаков, чтобы конспект не
превращался в копию учебника.

Вводная фраза с двоеточием («…работает в три шага:») сама по себе пустая:
за ней список — его пункты дописываются в строку; за ней код или таблица —
берётся следующий абзац. Слишком короткий абзац дополняется следующим.

Тексты здесь — Markdown: жирные термины и инлайн-код сохраняются (их рендерит
``web.markdown.render``), а резка не рвёт ``**…**`` и `` `…` `` посередине.
Модуль чистый: ни БД, ни сети.
"""

from __future__ import annotations

import re

# Предел сути раздела (и итога ответа тьютора), знаков.
GIST_CHARS = 320
# Абзац короче — не суть, а подводка: к нему добавляется следующий.
SHORT_PARAGRAPH = 60

_MAIN = re.compile(r"^###\s+Главное\s*$")
_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE = re.compile(r"^\s*\|")
_QUOTE = re.compile(r"^\s*>")
# Конец предложения: знак, затем пробел; граница решается в ``_boundaries``.
_END = re.compile(r"[.!?…](?=\s)")
_STARTS_SENTENCE = re.compile(r"[A-ZА-ЯЁ0-9«\"'`*(\[]")
# Абзац закончен: последний знак — конец предложения (с закрывающими скобками,
# кавычками и разметкой после него).
_FINISHED = re.compile(r"[.!?…][)»\"'*`]*$")


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Разбор на блоки: ``para``, ``list``, ``code``, ``table``, ``quote``, ``heading``.

    Пустая строка или смена вида строки закрывает блок. Строка с отступом
    внутри списка — продолжение пункта, а не новый абзац.
    """
    blocks: list[tuple[str, list[str]]] = []
    kind: str | None = None
    current: list[str] = []
    fence: str | None = None

    def close() -> None:
        nonlocal kind, current
        if kind is not None and current:
            blocks.append((kind, current))
        kind, current = None, []

    for line in lines:
        if fence is not None:
            current.append(line)
            if line.strip().startswith(fence):
                fence = None
                close()
            continue
        opened = _FENCE.match(line)
        if opened:
            close()
            fence = opened.group(1)[:3]
            kind, current = "code", [line]
            continue
        if not line.strip():
            close()
            continue
        if _HEADING.match(line):
            close()
            blocks.append(("heading", [line]))
            continue
        if _LIST.match(line):
            line_kind = "list"
        elif kind == "list" and line.startswith((" ", "\t")):
            line_kind = "list"
        elif _TABLE.match(line):
            line_kind = "table"
        elif _QUOTE.match(line):
            line_kind = "quote"
        else:
            line_kind = "para"
        if line_kind != kind:
            close()
            kind = line_kind
        current.append(line)
    close()
    return blocks


def _join(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def _items(lines: list[str]) -> list[str]:
    """Пункты списка одной строкой каждый (продолжения пункта приклеены)."""
    items: list[str] = []
    for line in lines:
        marker = _LIST.match(line)
        if marker and not line.startswith(("  ", "\t")):
            items.append(line[marker.end():].strip())
        elif items:
            items[-1] = f"{items[-1]} {line.strip()}"
    return [item for item in items if item]


def _balanced(text: str) -> bool:
    return text.count("**") % 2 == 0 and text.replace("**", "").count("`") % 2 == 0


def _boundaries(text: str) -> list[int]:
    """Концы предложений (индекс после знака), не ломающие Markdown и сокращения."""
    ends: list[int] = []
    for match in _END.finditer(text):
        end = match.end()
        head = text[:end]
        if not _balanced(head):
            continue
        rest = text[end:].lstrip()
        if not rest:
            continue
        if not _STARTS_SENTENCE.match(rest):
            continue
        # «т. Е», «см. Рис» — сокращение из одной-двух букв перед точкой.
        word = re.search(r"(\w+)[.!?…]$", head)
        if match.group() == "." and word is not None and len(word.group(1)) <= 2 and word.group(1).isalpha():
            continue
        ends.append(end)
    return ends


def _first_sentence(text: str) -> str:
    ends = _boundaries(text)
    return text[: ends[0]].strip() if ends else text.strip()


def _strip_unmatched(text: str) -> str:
    """Убирает последний непарный ``**`` или `` ` ``, чтобы обрезка не ломала разметку."""
    while text.count("**") % 2:
        cut = text.rfind("**")
        text = text[:cut] + text[cut + 2:]
    plain = text.replace("**", "\0")
    while plain.count("`") % 2:
        cut = plain.rfind("`")
        plain = plain[:cut] + plain[cut + 1:]
    return plain.replace("\0", "**")


def clip_sentences(text: str, limit: int) -> str:
    """Целые предложения, пока влезают в ``limit``; иначе — по слову с «…»."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    fitting = [end for end in _boundaries(text) if end <= limit]
    if fitting:
        return text[: fitting[-1]].rstrip()
    cut = text.rfind(" ", 0, limit)
    head = text[:cut] if cut > 0 else text[: limit - 1]
    head = _strip_unmatched(head).rstrip(" ,;:—-")
    return f"{head}…"


def _gist_from(blocks: list[tuple[str, list[str]]], limit: int, *, intro_rules: bool) -> str | None:
    paragraphs = [index for index, (kind, _) in enumerate(blocks) if kind == "para"]
    if not paragraphs:
        return None
    first = paragraphs[0]
    text = _join(blocks[first][1])
    following = blocks[first + 1] if first + 1 < len(blocks) else None
    if (
        intro_rules
        and following is not None
        and following[0] == "list"
        and (text.endswith(":") or len(text) < SHORT_PARAGRAPH)
    ):
        # Вводная к списку («…три шага:», «**Достоинства.**») — пункты в строку.
        return _with_items(text, _items(following[1]), limit)
    if intro_rules and (text.endswith(":") or not _FINISHED.search(text)):
        # Вводная к коду или формуле («…равен», «…вызовов:») сама по себе пустая.
        later = [index for index in paragraphs if index > first]
        if not later:
            return None
        first = later[0]
        text = _join(blocks[first][1])
    if intro_rules and len(text) < SHORT_PARAGRAPH:
        later = [index for index in paragraphs if index > first]
        if later:
            text = f"{text} {_join(blocks[later[0]][1])}"
    return clip_sentences(text, limit) or None


def _with_items(intro: str, items: list[str], limit: int) -> str | None:
    """«Вводная: пункт; пункт; пункт.» — пункты по первому предложению, пока влезают."""
    parts: list[str] = []
    for item in items:
        short = _first_sentence(item).rstrip(" .;,:")
        if not short:
            continue
        if intro.endswith(":") and short[0].isupper() and not short[1:2].isupper():
            # После двоеточия — строчная («шага: посчитать…»); аббревиатуру не трогаем.
            short = short[0].lower() + short[1:]
        candidate = f"{intro} {'; '.join([*parts, short])}."
        if len(candidate) > limit:
            break
        parts.append(short)
    if not parts:
        return clip_sentences(intro, limit) or None
    return f"{intro} {'; '.join(parts)}."


def section_gist(section_md: str, *, limit: int = GIST_CHARS) -> str | None:
    """Суть раздела: текст раздела, как его отдаёт ``course.seed.section_text``.

    ``None`` — в разделе нет обычного абзаца (только код, списки, таблицы).
    """
    lines = section_md.splitlines()
    if lines and lines[0].startswith("## "):
        lines = lines[1:]
    start = next((index for index, line in enumerate(lines) if _MAIN.match(line)), None)
    if start is not None:
        region = lines[start + 1:]
        stop = next((index for index, line in enumerate(region) if _HEADING.match(line)), None)
        main = region if stop is None else region[:stop]
        gist = _gist_from(_blocks(main), limit, intro_rules=True)
        if gist is not None:
            return gist
    return _gist_from(_blocks(lines), limit, intro_rules=True)


def answer_gist(markdown: str, *, limit: int = GIST_CHARS) -> str | None:
    """Первый обычный абзац ответа тьютора, обрезанный по предложениям."""
    return _gist_from(_blocks(markdown.splitlines()), limit, intro_rules=False)
