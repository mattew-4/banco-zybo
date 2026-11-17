@echo off
title Banco Zybo Z7-10 (emulatore)
cd /d "%~dp0"
echo Banco Zybo Z7-10 emulato
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

rem Tutto il resto e' in un unico blocco ( ... ): cmd lo legge per intero prima di eseguirlo,
rem cosi' l'aggiornamento automatico puo' sostituire anche questo file mentre e' in uso.
(
    %PY% --version >nul 2>nul
    if errorlevel 1 (
        echo Python non trovato. Installalo da https://www.python.org/downloads/
        echo ^(durante l'installazione spunta "Add python.exe to PATH"^), poi riprova.
        pause
        exit /b 1
    )

    rem scarica da GitHub l'ultima versione del banco, se ce n'e' una nuova
    %PY% -I update.py

    rem la prima volta scarica compilatore ed emulatore ^(circa 400 MB^); poi non fa niente
    %PY% -I setup.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
    echo.
    echo Si apre nel browser su http://127.0.0.1:8765/
    echo Chiudi questa finestra per spegnere l'emulatore.
    echo.
    %PY% -I server\app.py
    if errorlevel 1 pause
    exit /b
)
