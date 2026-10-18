@echo off
rem Genera dist\Revoz.exe (un solo archivo, no necesita Python instalado).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python -m pip install -q -e ".[dev]" pyinstaller || goto :error

rem Los plugins pueden usar estos modulos de la libreria estandar aunque Revoz no los use.
rem ponytail: lista fija; si un plugin necesita otro modulo, agregarlo aca con --hidden-import.
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name Revoz ^
    --paths src ^
    --hidden-import wave --hidden-import csv --hidden-import gzip --hidden-import zlib ^
    --hidden-import subprocess --hidden-import sqlite3 --hidden-import base64 ^
    --hidden-import xml.etree.ElementTree --hidden-import concurrent.futures --hidden-import ctypes ^
    src\revoz\__main__.py || goto :error

echo.
echo Listo: dist\Revoz.exe
goto :eof

:error
echo.
echo Fallo la compilacion.
exit /b 1
