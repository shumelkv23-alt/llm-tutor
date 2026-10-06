"""Метрики согласия грейдера с разметкой (Срез 7.2).

Считаем по КРИТЕРИЯМ, а не по «правильности ответа»: рубрика — это набор
независимых бинарных решений, и качество грейдера видно именно на них.
Основная метрика — kappa Коэна: согласие с поправкой на случайность (при
перекосе в «всё засчитано» обычная точность обманчиво высока).
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Agreement:
    """Согласие предсказаний с разметкой по бинарным критериям."""

    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def total(self) -> int:
        """Сколько всего решений по критериям."""
        return self.true_positive + self.false_positive + self.false_negative + self.true_negative

    @property
    def accuracy(self) -> float:
        """Доля совпавших решений (0.0 на пустой выборке)."""
        if self.total == 0:
            return 0.0
        return (self.true_positive + self.true_negative) / self.total

    @property
    def precision(self) -> float:
        """Доля верных среди засчитанных критериев."""
        predicted_positive = self.true_positive + self.false_positive
        return self.true_positive / predicted_positive if predicted_positive else 0.0

    @property
    def recall(self) -> float:
        """Доля засчитанных среди действительно выполненных."""
        actual_positive = self.true_positive + self.false_negative
        return self.true_positive / actual_positive if actual_positive else 0.0

    @property
    def kappa(self) -> float:
        """Kappa Коэна: согласие за вычетом случайного.

        Пустая выборка или полное совпадение маргиналов (ожидание 1.0) — 0.0:
        метрика не определена, а не «идеальна».
        """
        if self.total == 0:
            return 0.0
        observed = self.accuracy
        predicted_positive = (self.true_positive + self.false_positive) / self.total
        actual_positive = (self.true_positive + self.false_negative) / self.total
        expected = (
            predicted_positive * actual_positive
            + (1 - predicted_positive) * (1 - actual_positive)
        )
        if expected >= 1.0:
            return 0.0
        return (observed - expected) / (1.0 - expected)


def agreement(pairs: Iterable[tuple[bool, bool]]) -> Agreement:
    """Считает согласие по парам ``(предсказание, разметка)``."""
    true_positive = false_positive = false_negative = true_negative = 0
    for predicted, expected in pairs:
        if predicted and expected:
            true_positive += 1
        elif predicted and not expected:
            false_positive += 1
        elif expected:
            false_negative += 1
        else:
            true_negative += 1
    return Agreement(true_positive, false_positive, false_negative, true_negative)
