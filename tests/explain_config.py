"""Эталон на три занятия для чистых тестов логики «Объясни новичку».

Логика (покрытие, отчёт, расписание) от числа занятий в эталоне не зависит, а в
курсе эталон лежит по занятиям: этот — собранный из тестовых модулей, чтобы тесты
проверяли переходы между занятиями отчёта без привязки к фикстурам seed.
"""

from llm_tutor.schemas import ExplainConfig

CONFIG = ExplainConfig.model_validate_json(
    '''
{
    "ideas": [
        {
            "id": "series_vs_frame",
            "lesson": "l1_frames",
            "text": "Series — один столбец с индексом, DataFrame — таблица из таких столбцов с общим индексом",
            "required": true,
            "expect": "ученик различает Series и DataFrame и говорит про общий индекс"
        },
        {
            "id": "loc_vs_iloc",
            "lesson": "l1_select",
            "text": "loc выбирает по меткам индекса, iloc — по номерам позиций",
            "required": true,
            "expect": "ученик называет разницу меток и позиций и приводит пример"
        },
        {
            "id": "boolean_mask",
            "lesson": "l1_select",
            "text": "булева маска — Series из True/False той же длины; df[mask] оставляет строки, где True",
            "required": false,
            "expect": "ученик объясняет, что маска — это условие по столбцу"
        },
        {
            "id": "split_apply_combine",
            "lesson": "l1_group",
            "text": "groupby делит строки на группы, к каждой применяет функцию и склеивает результат",
            "required": true,
            "expect": "ученик называет три шага и пример агрегата по группе"
        },
        {
            "id": "pivot_table",
            "lesson": "l1_group",
            "text": "pivot_table раскладывает агрегат по двум признакам: строки — один, столбцы — другой",
            "required": false,
            "expect": "ученик говорит, что pivot_table — группировка по двум признакам сразу"
        }
    ],
    "pitfalls": [
        {
            "id": "groupby_returns_df",
            "lesson": "l1_group",
            "belief": "А, то есть df.groupby('x') сразу возвращает новую таблицу?",
            "fix": "возвращает объект GroupBy; таблица появляется после агрегата: .mean(), .agg(...)"
        },
        {
            "id": "iloc_by_label",
            "lesson": "l1_select",
            "belief": "Получается, iloc[5] — это строка с индексом 5?",
            "fix": "iloc берёт пятую позицию по порядку; строку с меткой 5 даёт loc[5]"
        }
    ]
}
    '''
)
