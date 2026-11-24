@echo off
chcp 65001 >nul
setlocal EnableExtensions
title LLM-тьютор

rem Запускатор llm-tutor: проверяет окружение, ставит зависимости и держит сервер.
rem Двойной клик - запуск. "start.bat --setup" - только подготовка, без сервера.
rem "start.bat --no-open" - запуск без автоматического открытия браузера.

cd /d "%~dp0"
set "PYTHONUTF8=1"
if /i "%~1"=="--no-open" set "NO_OPEN=1"

echo ==========================================================
echo   LLM-тьютор - локальный сервер
echo ==========================================================
echo.

call :find_uv
if errorlevel 1 goto fail

call :ensure_env
if errorlevel 1 goto fail

echo [1/2] Проверяю зависимости...
uv sync --python 3.12
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить зависимости.
    goto fail
)

if /i "%~1"=="--setup" (
    echo.
    echo [Готово] Всё установлено. Дальше запускайте start.bat без параметров.
    goto done
)

call :read_port
call :port_busy
if defined BUSY (
    echo.
    echo [i] Порт %PORT% уже занят - похоже, сервер уже запущен.
    echo     Адрес: http://127.0.0.1:%PORT%/
    if not defined NO_OPEN start "" "http://127.0.0.1:%PORT%/"
    goto done
)

echo.
echo [2/2] Запускаю сервер: http://127.0.0.1:%PORT%/
echo       Остановка - Ctrl+C или закрыть это окно.
echo.

rem Открыть браузер, когда порт начнёт отвечать, но не позже чем через 60 секунд.
if not defined NO_OPEN start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%PORT%);$c.Close();Start-Process 'http://127.0.0.1:%PORT%/';break}catch{Start-Sleep -Milliseconds 500}}"

uv run python -m llm_tutor.web
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
    echo [ОШИБКА] Сервер завершился с кодом %CODE%.
) else (
    echo Сервер остановлен.
)

goto done


:fail
echo.
echo Исправьте ошибку выше и запустите start.bat снова.
echo.
pause
exit /b 1


:done
echo.
echo Окно можно закрыть.
pause
exit /b 0


rem ---------------------------------------------------------------
rem Поиск uv; при отсутствии - предложение поставить официальным установщиком.
:find_uv
where uv >nul 2>nul
if not errorlevel 1 exit /b 0

for %%P in ("%USERPROFILE%\.local\bin" "%LOCALAPPDATA%\Microsoft\WinGet\Links" "%LOCALAPPDATA%\Programs\uv") do (
    if exist "%%~P\uv.exe" set "PATH=%%~P;%PATH%"
)
where uv >nul 2>nul
if not errorlevel 1 exit /b 0

echo [i] Не найден uv - менеджер пакетов и Python, без него проект не запустить.
echo.
choice /c YN /n /m "Установить uv сейчас? [Y/N] "
if errorlevel 2 exit /b 1

echo.
echo Устанавливаю uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ОШИБКА] uv всё ещё не найден. Установите вручную:
    echo   winget install --id=astral-sh.uv -e
    echo и запустите start.bat заново.
    exit /b 1
)
exit /b 0


rem ---------------------------------------------------------------
rem .env: создать из шаблона и проверить, что ключ модели заполнен.
:ensure_env
if exist ".env" goto env_key

if not exist ".env.example" (
    echo [ОШИБКА] Нет ни .env, ни .env.example - проверьте папку проекта.
    exit /b 1
)
copy /y ".env.example" ".env" >nul
echo [i] Создан .env из .env.example.
echo.

:env_key
set "KEY="
for /f "usebackq tokens=2 delims==" %%A in (`findstr /b /c:"OPENROUTER_API_KEY=" ".env"`) do set "KEY=%%A"
if not defined KEY (
    echo [ОШИБКА] В .env пустой OPENROUTER_API_KEY.
    echo Откройте .env в блокноте и впишите ключ с https://openrouter.ai/keys
    exit /b 1
)
exit /b 0


rem ---------------------------------------------------------------
rem Порт из .env (WEB_PORT), по умолчанию 8000.
:read_port
set "PORT=8000"
for /f "usebackq tokens=2 delims==" %%A in (`findstr /b /c:"WEB_PORT=" ".env"`) do set "PORT=%%A"
if not defined PORT set "PORT=8000"
exit /b 0


rem ---------------------------------------------------------------
rem Проверка, слушает ли кто-то уже этот порт.
:port_busy
set "BUSY="
for /f "delims=" %%A in ('netstat -ano ^| findstr /c:":%PORT%" ^| findstr /c:"LISTENING"') do set "BUSY=1"
exit /b 0
