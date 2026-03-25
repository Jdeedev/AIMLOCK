@echo off
echo ============================================
echo   AIM LOCK - Setup e Inicializacao
echo ============================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado.
    echo Instale em: https://python.org/downloads
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Criando ambiente virtual...
    python -m venv .venv
)

echo Instalando dependencias...
.venv\Scripts\pip install -r requirements.txt --quiet --upgrade

echo.
echo ============================================
echo   Iniciando Aim Lock...
echo ============================================
echo.
.venv\Scripts\python aim_lock.py
pause
