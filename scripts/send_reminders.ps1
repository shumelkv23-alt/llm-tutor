# Ежедневная рассылка напоминаний о повторении.
# Запускать из Планировщика задач Windows; рабочий каталог — корень проекта.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
uv run python -m llm_tutor.web.cli send-review-reminders
