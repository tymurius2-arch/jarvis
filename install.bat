@echo off
chcp 65001 >nul
title Установка Джарвиса
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.12 с python.org и поставьте галочку "Add python.exe to PATH".
  start https://www.python.org/downloads/release/python-3129/
  pause
  exit /b 1
)
echo Создаю окружение...
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo Что-то пошло не так при установке. Сделайте скриншот этого окна.
  pause
  exit /b 1
)
echo.
echo Готово! Запускайте start.bat
pause
