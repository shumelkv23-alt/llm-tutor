"""Граф концептов курса поверх NetworkX (Срез 4.2).

Граф пререквизитов — DAG: строка ``from_id`` требуется для ``to_id``.
Учитываются только рёбра типа ``requires``; ``hard`` различает жёсткий
пререквизит (блокирует узел) и мягкий (штраф к приоритету, не блокировка).
"""

import sqlite3
from collections.abc import Sequence

import networkx as nx

from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge

_REQUIRES = "requires"


class CourseGraphError(ValueError):
    """Некорректный граф курса: цикл или ребро на несуществующий концепт."""


class CourseGraph:
    """DAG концептов курса: пререквизиты, зависимости, топологический порядок."""

    def __init__(self, concepts: Sequence[Concept], edges: Sequence[Edge]) -> None:
        self._concepts: dict[str, Concept] = {c.id: c for c in concepts}
        self._graph = nx.DiGraph()
        self._graph.add_nodes_from(self._concepts)

        for edge in edges:
            self._check_endpoints(edge)
            self._graph.add_edge(
                edge.from_id,
                edge.to_id,
                type=edge.type,
                hard=edge.hard,
                weight=edge.weight,
            )
        self.validate_dag()

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "CourseGraph":
        """Строит граф из БД (concepts + edges)."""
        return cls(repos.get_concepts(conn), repos.get_edges(conn))

    def _check_endpoints(self, edge: Edge) -> None:
        for node_id in (edge.from_id, edge.to_id):
            if node_id not in self._concepts:
                raise CourseGraphError(
                    f"Ребро ссылается на неизвестный концепт: {node_id} "
                    f"({edge.from_id} -> {edge.to_id})"
                )

    def validate_dag(self) -> None:
        """Падает с ``CourseGraphError``, если в графе есть цикл."""
        try:
            cycle = nx.find_cycle(self._graph)
        except nx.NetworkXNoCycle:
            return
        edges = " -> ".join(f"{src}->{dst}" for src, dst, *_ in cycle)
        raise CourseGraphError(f"В графе курса цикл: {edges}")

    # --- интроспекция ---

    @property
    def node_ids(self) -> list[str]:
        """Все id концептов."""
        return list(self._concepts)

    def concept(self, node_id: str) -> Concept:
        """Концепт по id (``KeyError``, если его нет в графе)."""
        try:
            return self._concepts[node_id]
        except KeyError:
            raise KeyError(f"Концепт {node_id!r} отсутствует в графе курса") from None

    def difficulty(self, node_id: str) -> float:
        """Сложность узла (0..1) — прокси ожидаемой стоимости прохода."""
        return self.concept(node_id).difficulty

    def _in_edges(self, node_id: str, *, hard_only: bool = False) -> list[tuple[str, dict]]:
        return [
            (src, data)
            for src, _, data in self._graph.in_edges(node_id, data=True)
            if data["type"] == _REQUIRES and (not hard_only or data["hard"])
        ]

    def prerequisites(self, node_id: str) -> list[str]:
        """Прямые пререквизиты (жёсткие и мягкие), отсортированные по id."""
        return sorted(src for src, _ in self._in_edges(node_id))

    def hard_prerequisites(self, node_id: str) -> list[str]:
        """Прямые жёсткие пререквизиты — без них узел заблокирован."""
        return sorted(src for src, _ in self._in_edges(node_id, hard_only=True))

    def soft_prerequisites(self, node_id: str) -> list[str]:
        """Прямые мягкие пререквизиты — штраф, но не блокировка."""
        hard = set(self.hard_prerequisites(node_id))
        return [src for src in self.prerequisites(node_id) if src not in hard]

    def dependents(self, node_id: str) -> list[str]:
        """Прямые зависимые узлы (те, для кого ``node_id`` — пререквизит)."""
        return sorted(
            dst
            for _, dst, data in self._graph.out_edges(node_id, data=True)
            if data["type"] == _REQUIRES
        )

    def edge_weight(self, from_id: str, to_id: str) -> float:
        """Вес ребра ``requires`` (0.0, если рёбра нет)."""
        data = self._graph.get_edge_data(from_id, to_id)
        if not data or data.get("type") != _REQUIRES:
            return 0.0
        return float(data["weight"])

    def topo_order(self) -> list[str]:
        """Топологический порядок обхода (узел идёт после всех пререквизитов)."""
        return list(nx.topological_sort(self._graph))

    def ancestors(self, node_id: str) -> set[str]:
        """Все (транзитивные) пререквизиты узла."""
        return nx.ancestors(self._graph, node_id)

    def descendants(self, node_id: str) -> set[str]:
        """Все (транзитивные) узлы, зависящие от данного."""
        return nx.descendants(self._graph, node_id)
