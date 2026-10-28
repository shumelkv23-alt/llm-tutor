"""Рёбра графа модуля — производная от порядка занятий.

Внутри модуля занятия идут цепочкой: каждое следующее мягко опирается на
предыдущее. Цепочку строит генерация (``steps.assemble``), а редактор модуля
пересобирает её при каждом сохранении (``edit.write_module``): добавленное,
убранное или переставленное занятие не оставляет рёбер на несуществующие узлы
и не расходится с графом курса.

Внутримодульные рёбра любого вида — производные: цепочка заменяет их целиком,
поэтому перестановка занятия не может оставить ребро прежнего направления и
замкнуть цикл. Сохраняются только входы из предыдущих модулей: их задаёт
автор и порядком занятий этого модуля они не определяются.
"""

from collections.abc import Collection, Sequence

from llm_tutor.schemas import Concept, Edge

CHAIN_WEIGHT = 0.3
"""Вес мягкой связи: подсказка планировщику, а не блокировка маршрута."""


def chain_edges(lesson_ids: Sequence[str]) -> list[Edge]:
    """Линейная цепочка мягких ``requires`` по порядку занятий."""
    return [
        Edge(from_id=previous, to_id=current, type="requires", hard=False, weight=CHAIN_WEIGHT)
        for previous, current in zip(lesson_ids, lesson_ids[1:])
    ]


def rebuild_edges(
    nodes: Sequence[Concept],
    edges: Sequence[Edge],
    *,
    removed: Collection[str] = (),
) -> list[Edge]:
    """Рёбра модуля после правки его занятий.

    ``removed`` — id занятий, которые в модуле были, а теперь убраны: без них
    ребро от убранного занятия к оставшемуся не отличить от входа из другого
    модуля (``_check_topics`` разрешает входящие рёбра).

    - ребро, ведущее в занятие, которого в модуле больше нет, или выходящее из
      убранного занятия, убирается: иначе курс не соберётся (битая ссылка);
    - все рёбра внутри модуля заменяются цепочкой по порядку ``nodes``:
      цепочка — единственное, что задаёт порядок занятий, и она всегда ациклична;
    - входы из других модулей остаются как есть (дубли схлопываются): это
      пререквизиты из пройденного материала, порядком занятий они не задаются.

    Цепочка дописывается в конец: порядок рёбер в seed не важен, а так правка
    файла руками видна как «входы сначала, производное потом».
    """
    own = {node.id for node in nodes}
    gone = set(removed) - own
    seen: set[tuple[str, str, str]] = set()
    kept: list[Edge] = []
    for edge in edges:
        if edge.to_id not in own or edge.from_id in gone:
            continue  # цель убрана из модуля или ребро выходит из убранного
        if edge.from_id in own:
            continue  # внутримодульное ребро пересоберём цепочкой
        key = (edge.from_id, edge.to_id, edge.type)
        if key in seen:
            continue
        seen.add(key)
        kept.append(edge)
    for edge in chain_edges([node.id for node in nodes]):
        key = (edge.from_id, edge.to_id, edge.type)
        if key in seen:
            continue
        seen.add(key)
        kept.append(edge)
    return kept
