"""Граф концептов курса поверх NetworkX (Срез 4.2).

Граф пререквизитов — DAG: ребро ``from_id -> to_id`` означает «``from_id``
требуется для ``to_id``». В граф входят только рёбра типа ``requires``:
``part_of``/``leads_to`` зарезервированы в схеме, но пререквизитами не
являются — иначе связь другого типа между той же парой вытеснила бы
``requires`` (у ``DiGraph`` на пару вершин одно ребро), а цикл из ``part_of``
блокировал бы весь граф.

``hard`` различает жёсткий пререквизит (блокирует узел) и мягкий (штраф к
приоритету, не блокировка).

Узел знает свой модуль (срез 24): межмодульные рёбра ведут только назад,
порядок обхода — модуль за модулем.
"""

import sqlite3
from collections.abc import Sequence

import networkx as nx

from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge

_REQUIRES = "requires"


class CourseGraphError(ValueError):
    """Некорректный граф: цикл, дубль или ссылка на несуществующий концепт."""


class CourseGraph:
    """DAG концептов курса: пререквизиты, зависимости, топологический порядок."""

    def __init__(self, concepts: Sequence[Concept], edges: Sequence[Edge]) -> None:
        self._concepts: dict[str, Concept] = {}
        for concept in concepts:
            if concept.id in self._concepts:
                raise CourseGraphError(f"Дубль концепта в графе: {concept.id}")
            self._concepts[concept.id] = concept

        self._graph = nx.DiGraph()
        self._graph.add_nodes_from(self._concepts)

        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            key = (edge.from_id, edge.to_id, edge.type)
            if key in seen:
                raise CourseGraphError(
                    f"Дубль ребра в графе: {edge.from_id} -> {edge.to_id} ({edge.type})"
                )
            seen.add(key)
            self._check_endpoints(edge)
            if edge.type == _REQUIRES:
                self._graph.add_edge(
                    edge.from_id, edge.to_id, hard=edge.hard, weight=edge.weight
                )
        self._check_topic_direction()
        self.validate_dag()
        # Граф неизменяем: порядок считаем один раз (маршрут зовёт его каждый ход).
        self._topo = self._module_topo_order()

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
        """Падает с ``CourseGraphError``, если в пререквизитах есть цикл."""
        try:
            cycle = nx.find_cycle(self._graph)
        except nx.NetworkXNoCycle:
            return
        edges = " -> ".join(f"{src}->{dst}" for src, dst, *_ in cycle)
        raise CourseGraphError(f"В графе курса цикл: {edges}")

    def _check_topic_direction(self) -> None:
        """Пререквизит не может лежать в модуле позже зависимой темы (срез 24).

        На этом держится топопорядок модуль за модулем: ребро вперёд сделало бы
        склейку порядков модулей неверной.
        """
        for src, dst in self._graph.edges:
            src_topic = self._concepts[src].topic_id
            dst_topic = self._concepts[dst].topic_id
            if src_topic > dst_topic:
                raise CourseGraphError(
                    f"Ребро {src} -> {dst} ведёт вперёд: из модуля {src_topic} "
                    f"в модуль {dst_topic}"
                )

    def _module_topo_order(self) -> list[str]:
        """Модуль за модулем, внутри модуля — прежний ``nx.topological_sort``.

        Подграф строится заново, а не через ``subgraph``: вид подграфа при малой
        доле узлов перебирает их в порядке множества (хеш строк, меняется от
        запуска к запуску). Узлы и рёбра добавляются в порядке исходного графа,
        поэтому порядок topic01 тот же, что до появления модулей.
        """
        order: list[str] = []
        for topic_id in self.topic_ids:
            members = [n for n in self._graph if self._concepts[n].topic_id == topic_id]
            inside = set(members)
            module = nx.DiGraph()
            module.add_nodes_from(members)
            module.add_edges_from(
                (src, dst) for src, dst in self._graph.edges if src in inside and dst in inside
            )
            order.extend(nx.topological_sort(module))
        return order

    # --- интроспекция ---

    @property
    def node_ids(self) -> list[str]:
        """Все id концептов."""
        return list(self._concepts)

    def has_node(self, node_id: str) -> bool:
        """Есть ли такой узел в графе."""
        return node_id in self._concepts

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
            if not hard_only or data["hard"]
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
        return sorted(self._graph.successors(node_id))

    def edge_weight(self, from_id: str, to_id: str) -> float:
        """Вес ребра-пререквизита (0.0, если рёбра нет)."""
        data = self._graph.get_edge_data(from_id, to_id)
        return float(data["weight"]) if data else 0.0

    @property
    def topic_ids(self) -> list[int]:
        """Номера модулей, у которых есть темы, по возрастанию."""
        return sorted({concept.topic_id for concept in self._concepts.values()})

    def topic_of(self, node_id: str) -> int:
        """Модуль курса, к которому относится тема."""
        return self.concept(node_id).topic_id

    def topic_nodes(self, topic_id: int) -> list[str]:
        """Темы модуля в топологическом порядке."""
        return [node for node in self._topo if self._concepts[node].topic_id == topic_id]

    def topo_order(self) -> list[str]:
        """Топологический порядок: модуль за модулем, внутри — по пререквизитам."""
        return list(self._topo)

    def ancestors(self, node_id: str) -> set[str]:
        """Все (транзитивные) пререквизиты узла."""
        return nx.ancestors(self._graph, node_id)

    def descendants(self, node_id: str) -> set[str]:
        """Все (транзитивные) узлы, зависящие от данного."""
        return nx.descendants(self._graph, node_id)

    def distance(self, from_id: str, to_id: str) -> int | None:
        """Длина кратчайшего пути ``from_id -> to_id`` (``None``, если пути нет)."""
        self.concept(from_id)
        self.concept(to_id)
        try:
            return nx.shortest_path_length(self._graph, from_id, to_id)
        except nx.NetworkXNoPath:
            return None
