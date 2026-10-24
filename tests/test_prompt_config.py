"""Промпты «Объясни новичку» из TOML (срез 85)."""

import shutil
from pathlib import Path

import pytest

from llm_tutor.llm.prompt_config import PromptConfigError, load_explain
from llm_tutor.web.app import create_app
from web_fakes import SilentLLM

SHIPPED = Path(__file__).resolve().parent.parent / "data" / "prompts"


def _copy(tmp_path: Path) -> Path:
    folder = tmp_path / "prompts"
    shutil.copytree(SHIPPED, folder)
    return folder


def _edit(folder: Path, old: str, new: str) -> None:
    path = folder / "explain.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_shipped_config_loads() -> None:
    prompts = load_explain(SHIPPED)
    limits = prompts.limits
    assert limits.paste_min_chars == 600
    assert limits.paste_overlap == 0.5
    assert limits.wrap_up_share == 0.8
    assert limits.max_student_turns == 30
    assert limits.pitfall_every == 3
    assert limits.pitfall_max == 3
    assert limits.novice_max_chars == 600
    assert limits.stale_hours == 24
    assert limits.tail_turns == 6
    assert len(prompts.novice.fallback_questions) >= 3
    assert "{title}" in prompts.novice.opening


def test_missing_key_is_error(tmp_path) -> None:
    folder = _copy(tmp_path)
    text = (folder / "explain.toml").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("strict =")]
    (folder / "explain.toml").write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(PromptConfigError, match="strict"):
        load_explain(folder)


def test_unknown_placeholder_is_error(tmp_path) -> None:
    folder = _copy(tmp_path)
    _edit(folder, "ask = \"", "ask = \"{answer} ")
    with pytest.raises(PromptConfigError, match="answer") as error:
        load_explain(folder)
    assert "novice.directives.ask" in str(error.value)


def test_bad_limit_is_error(tmp_path) -> None:
    folder = _copy(tmp_path)
    _edit(folder, "paste_overlap = 0.5", "paste_overlap = 1.5")
    with pytest.raises(PromptConfigError, match="paste_overlap"):
        load_explain(folder)


def test_broken_toml_is_error(tmp_path) -> None:
    folder = _copy(tmp_path)
    (folder / "explain.toml").write_text("[novice\nsystem = ", encoding="utf-8")
    with pytest.raises(PromptConfigError, match="explain.toml"):
        load_explain(folder)


def test_missing_file_is_error(tmp_path) -> None:
    with pytest.raises(PromptConfigError, match="explain.toml"):
        load_explain(tmp_path)


def test_prompts_dir_overrides(tmp_path, web_settings) -> None:
    folder = _copy(tmp_path)
    _edit(folder, "opening = \"", "opening = \"Тест-приветствие. ")
    app = create_app(web_settings.model_copy(update={"prompts_dir": str(folder)}), client=SilentLLM())
    try:
        assert app.state.explain_prompts.novice.opening.startswith("Тест-приветствие.")
    finally:
        app.state.accounts.close()


def test_app_refuses_bad_config(tmp_path, web_settings) -> None:
    with pytest.raises(PromptConfigError):
        create_app(web_settings.model_copy(update={"prompts_dir": str(tmp_path)}), client=SilentLLM())


def test_default_settings() -> None:
    from llm_tutor.config import Settings

    settings = Settings(_env_file=None, openrouter_api_key="k")
    assert settings.prompts_dir == "data/prompts"
    assert settings.explain_evidence_weight == 0.3
    assert settings.explain_evaluator_model
