"""API админки: действия над аккаунтами (срез 66).

Защита стоит на роутере, а не в каждой функции: новый маршрут не может её
забыть. Изменяющие запросы идут через общий ``api_guard`` (только JSON и свой
``Origin``).
"""

from fastapi import APIRouter, Depends

from llm_tutor.web.security import admin_user

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])
