# Резервная копия аккаунтов, БД учеников, курсов и материалов.
# Запускать из Планировщика задач; каталог backups/ не попадает в git.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
uv run python -m llm_tutor.web.cli backup --with-courses
