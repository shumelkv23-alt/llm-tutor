"""Общие фикстуры pytest для LLM-тьютора."""

import httpx
import pytest

from llm_tutor.config import Settings, get_settings
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.web.app import create_app
from web_fakes import RecordingMailer, SilentLLM


@pytest.fixture(autouse=True)
def _default_settings_without_env(monkeypatch):
    """Код, который зовёт ``get_settings()`` без явных настроек, получает ключ.

    На машине разработчика ключ даёт ``.env``, а в CI и в чистой копии его
    нет — и такие тесты падали бы только там. Настоящий ключ не трогаем.
    """
    get_settings.cache_clear()
    try:
        get_settings()
    except Exception:  # noqa: BLE001 — нет ключа ни в окружении, ни в .env
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def conn():
    """Соединение с мигрированной БД в памяти (`:memory:`)."""
    connection = get_conn(":memory:")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings():
    """Настройки с фиктивными секретами и без чтения `.env`."""
    return Settings(
        _env_file=None,
        openrouter_api_key="test-key",
    )


@pytest.fixture
def web_settings(tmp_path):
    """Настройки веба: все файлы — во временном каталоге, Telegram не нужен."""
    return Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        accounts_db_path=str(tmp_path / "accounts.sqlite3"),
        users_dir=str(tmp_path / "users"),
        materials_db_path=str(tmp_path / "materials.sqlite3"),
        # Каталог созданных курсов — тоже во временной папке: иначе реестр
        # читал бы реальный data/courses и тесты зависели бы от машины.
        courses_dir=str(tmp_path / "courses"),
    )


@pytest.fixture
def web_llm():
    """LLM веб-приложения; тест подменяет его, если модель нужна."""
    return SilentLLM()


@pytest.fixture
def web_mailer():
    """Почта веб-приложения: письма копятся в памяти, наружу не уходят."""
    return RecordingMailer()


@pytest.fixture
def courses_home(tmp_path, monkeypatch):
    """Каталог созданных курсов во временной папке: реестр не смотрит в data/.

    Настройка реестра — глобальная (модуль ``courses``), поэтому тесты,
    которым нужны созданные курсы без приложения, ставят её так же.
    """
    from llm_tutor.web import courses

    root = tmp_path / "courses"
    monkeypatch.setattr(courses, "_COURSES_DIR", root)
    monkeypatch.setattr(courses, "_GENERATED", ())
    courses.course_data.cache_clear()
    yield root
    courses.course_data.cache_clear()


@pytest.fixture
def visible_builtins(monkeypatch):
    """Встроенные курсы снова видны — для тестов движка ученика.

    По умолчанию ``mlcourse`` и ИСИТ скрыты из каталога и админки, а их занятия
    остаются на месте. Тесты, которые идут по этим занятиям, показывают курсы
    явно; скрытие проверяют тесты реестра и каталога.
    """
    from dataclasses import replace

    from llm_tutor.web import courses

    shown = tuple(replace(course, visible=True) for course in courses.COURSES)
    monkeypatch.setattr(courses, "COURSES", shown)
    monkeypatch.setattr(courses, "_BY_ID", {course.id: course for course in shown})
    return shown


@pytest.fixture
def make_course(courses_home):
    """Создаёт курс в tmp-каталоге и кладёт в него настоящий модуль фикстуры.

    Возвращает фабрику: ``make_course("python-basics", status="published")``.
    """
    import shutil

    from course_fixtures import LESSONS_DIR
    from llm_tutor.web import courses

    def build(course_id: str = "python-basics", *, status: str = "draft", title: str = "Основы Python"):
        courses.create_course(title=title, course_id=course_id)
        course = courses.get_course(course_id)
        assert course is not None and course.root is not None
        seed = LESSONS_DIR / "seed_topic01.json"
        shutil.copy(seed, course.root / seed.name)
        topic = seed.name.removeprefix("seed_").removesuffix(".json")
        shutil.copytree(LESSONS_DIR / "theory" / topic, course.root / "theory" / topic)
        if status != "draft":
            courses.update_meta(course_id, status=status)
        courses.invalidate_course(course_id)
        return courses.get_course(course_id)

    return build


@pytest.fixture
def web_app(web_settings, web_llm, web_mailer):
    app = create_app(web_settings, client=web_llm, mailer=web_mailer)
    yield app
    app.state.accounts.close()


@pytest.fixture
async def web(web_app):
    """HTTP-клиент к приложению в памяти процесса (без сети и uvicorn)."""
    transport = httpx.ASGITransport(app=web_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def student(web):
    """Клиент вошедшего ученика (регистрация через API)."""
    response = await web.post(
        "/api/auth/register",
        json={"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"},
    )
    assert response.status_code == 201
    return web
