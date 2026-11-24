"""Пересборка рёбер модуля по порядку занятий.

Порядок занятий — источник истины для внутримодульного графа: генерация и
редактор модуля строят цепочку из него (``course.build.edges``). Тесты держат
два правила: цепочка идёт по порядку и рёбра убранных занятий не остаются.
"""

import pytest

from llm_tutor.course.build.edges import CHAIN_WEIGHT, chain_edges, rebuild_edges
from llm_tutor.course.seed import Seed, SeedError, build_course
from llm_tutor.schemas import Concept, Edge, SurveyBlock, SurveyConfig, Topic


def _nodes(*ids: str) -> list[Concept]:
    return [Concept(id=node_id, name=node_id) for node_id in ids]


def _pairs(edges: list[Edge]) -> list[tuple[str, str]]:
    return [(edge.from_id, edge.to_id) for edge in edges]


def test_chain_links_consecutive_lessons() -> None:
    edges = chain_edges(["a", "b", "c"])

    assert _pairs(edges) == [("a", "b"), ("b", "c")]
    assert all(edge.type == "requires" and not edge.hard for edge in edges)
    assert all(edge.weight == CHAIN_WEIGHT for edge in edges)


def test_rebuild_reorders_the_chain() -> None:
    stale = [Edge(from_id="a", to_id="b", hard=False, weight=0.3)]

    rebuilt = rebuild_edges(_nodes("b", "a"), stale)

    assert _pairs(rebuilt) == [("b", "a")]
    assert rebuilt[0].hard is False and rebuilt[0].weight == CHAIN_WEIGHT


def test_rebuild_drops_edges_of_removed_lesson() -> None:
    stale = [
        Edge(from_id="a", to_id="b", hard=False, weight=0.3),
        Edge(from_id="b", to_id="c", hard=False, weight=0.3),
    ]

    rebuilt = rebuild_edges(_nodes("a", "c"), stale, removed={"b"})

    # b больше нет в модуле: ни ребро в него, ни ребро из него не остаются.
    assert _pairs(rebuilt) == [("a", "c")]


def test_rebuild_keeps_incoming_edges_and_replaces_intra_module_ones() -> None:
    edges = [
        # Вход из предыдущего модуля: не наш узел, но цель — наша.
        Edge(from_id="t00_intro", to_id="a", hard=True),
        # Внутримодульные рёбра любого вида — производные: их заменит цепочка.
        Edge(from_id="a", to_id="b", hard=True),
        Edge(from_id="a", to_id="b", type="leads_to"),
    ]

    rebuilt = rebuild_edges(_nodes("a", "b"), edges)

    assert _pairs(rebuilt) == [("t00_intro", "a"), ("a", "b")]
    assert [(edge.hard, edge.type) for edge in rebuilt] == [
        (True, "requires"),
        (False, "requires"),
    ]


def test_rebuild_deduplicates_edges() -> None:
    edges = [
        Edge(from_id="t00_intro", to_id="a"),
        Edge(from_id="t00_intro", to_id="a"),
    ]

    rebuilt = rebuild_edges(_nodes("a"), edges)

    assert _pairs(rebuilt) == [("t00_intro", "a")]


def test_cycle_is_reported_as_seed_error() -> None:
    """Цикл в графе — понятная ошибка курса, а не 500 из админки."""
    nodes = _nodes("a", "b")
    seed = Seed(
        course="Курс",
        topic=Topic(
            number=1,
            title="Модуль",
            intro="",
            survey=SurveyConfig(
                level_key="t01_level",
                level_question="Что знаете?",
                level_options=["Ничего", "Немного", "Уверенно"],
                blocks=[
                    SurveyBlock(
                        key="t01_block1",
                        title="Блок",
                        question="",
                        example="",
                        concepts=("a", "b"),
                    )
                ],
            ),
        ),
        nodes=nodes,
        edges=[
            Edge(from_id="a", to_id="b"),
            Edge(from_id="b", to_id="a"),
        ],
    )

    with pytest.raises(SeedError, match="цикл"):
        build_course([seed])
