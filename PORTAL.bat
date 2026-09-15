@echo off
title Portal NOTAMs Colombia
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "APP=%~dp0"
set "OTRO=%~dp0..\..\Servidor AWS\portal-notams"
set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%"
set "DIAGVID=sin comprobar"
set "DIAGDB=sin comprobar"
set "DIAGCOD=sin comprobar"

echo.
echo  ===========================================================
echo    PORTAL AERONAUTICO NOTAMs COLOMBIA
echo  ===========================================================
echo.

if not exist "%APP%index.html" (
    echo  [X] No encuentro index.html en esta carpeta.
    echo      Este .bat debe estar en la carpeta del proyecto.
    goto :fin
)

rem ---------------------------------------------------------------
rem  0. El fondo del portal es un video servido desde static\
rem ---------------------------------------------------------------
if not exist "%APP%static\hero-nubes.mp4" (
    echo  [!] Falta static\hero-nubes.mp4
    echo      El portal va a funcionar, pero sin el video de fondo.
    echo.
)

rem ---------------------------------------------------------------
rem  0b. Esta carpeta es el checkout de GitHub: no trae la base de
rem      datos ni el indice de reglamentos (van en .gitignore). Si
rem      ya existen en la carpeta "Servidor AWS\portal-notams" de
rem      este mismo equipo, se copian aqui la primera vez.
rem ---------------------------------------------------------------
if not exist "%APP%sistema_notams.db" (
    if exist "%OTRO%\sistema_notams.db" (
        echo  [i] Primera vez en esta carpeta: copiando los datos ya
        echo      descargados desde portal-notams...
        copy /y "%OTRO%\sistema_notams.db" "%APP%sistema_notams.db" >nul
        if exist "%OTRO%\sistema_rac.db" copy /y "%OTRO%\sistema_rac.db" "%APP%sistema_rac.db" >nul
        if exist "%OTRO%\rac_pdf" xcopy /y /i /e /q "%OTRO%\rac_pdf" "%APP%rac_pdf\" >nul
        echo        Datos copiados.
        echo.
    ) else (
        echo  [!] No hay base de datos de NOTAMs todavia en esta carpeta.
        echo      El portal va a abrir vacio hasta que corras extractor.py
        echo      o copies aqui sistema_notams.db y sistema_rac.db.
        echo.
    )
)

rem ---------------------------------------------------------------
rem  0c. La clave de Gemini va por .env, que no se sube a git.
rem ---------------------------------------------------------------
if not exist "%APP%.env" (
    if exist "%APP%.env.example" (
        copy /y "%APP%.env.example" "%APP%.env" >nul
        echo  [!] Se creo .env a partir de .env.example.
        echo      Edita GEMINI_API_KEY con tu clave para que funcione
        echo      el resumen con IA de los NOTAMs y de los reglamentos.
        echo.
    )
)

rem ---------------------------------------------------------------
rem  1. Si ya hay algo escuchando en el puerto
rem ---------------------------------------------------------------
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 goto :arrancar

where curl >nul 2>&1
if errorlevel 1 (
    echo  [i] Ya hay algo en el puerto %PORT%. Abriendo el navegador...
    start "" "%URL%"
    goto :fin
)

curl -sf -o nul --max-time 3 "%URL%/health" >nul 2>&1
if errorlevel 1 (
    echo  [X] El puerto %PORT% esta ocupado por otro programa.
    echo      Cierra lo que lo este usando y vuelve a abrir este archivo.
    goto :fin
)

curl -s --max-time 3 "%URL%/health" 2>nul | findstr "codigo_actualizado.:false" >nul 2>&1
if not errorlevel 1 (
    echo  [i] El portal corriendo cargo una version anterior del codigo.
    echo      Reiniciandolo para que tome los archivos nuevos...
    call :detener
    timeout /t 2 /nobreak >nul
    goto :arrancar
)

echo  [i] El portal ya estaba corriendo. Abriendo el navegador...
start "" "%URL%"
goto :fin

:arrancar
rem ---------------------------------------------------------------
rem  2. Python
rem ---------------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo  [X] No se encontro Python 3.
    echo.
    echo      Instalalo desde https://www.python.org/downloads/
    echo      IMPORTANTE: marca la casilla "Add python.exe to PATH".
    goto :fin
)

set "VENVPY=%APP%.venv\Scripts\python.exe"

rem ---------------------------------------------------------------
rem  3. Entorno virtual y dependencias (solo la primera vez)
rem ---------------------------------------------------------------
if not exist "%VENVPY%" (
    echo  [1/3] Primera ejecucion: preparando el entorno...
    echo        Esto tarda 1-2 minutos, solo pasa una vez.
    pushd "%APP%"
    %PY% -m venv .venv
    popd
    if not exist "%VENVPY%" (
        echo  [X] No se pudo crear el entorno virtual.
        goto :fin
    )
    "%VENVPY%" -m pip install --quiet --upgrade pip
    "%VENVPY%" -m pip install --quiet -r "%APP%requirements.txt"
    if errorlevel 1 (
        echo  [X] Fallo la instalacion de dependencias.
        goto :fin
    )
    echo        Entorno listo.
) else (
    echo  [1/3] Entorno ya preparado.
)

rem ---------------------------------------------------------------
rem  4. Arrancar la API en una ventana aparte
rem ---------------------------------------------------------------
echo  [2/3] Arrancando el servidor...
pushd "%APP%"
start "PortalNOTAMsServidor" /min cmd /c ""%VENVPY%" -m uvicorn api_notams:app --host 127.0.0.1 --port %PORT%"
popd

rem ---------------------------------------------------------------
rem  5. Esperar a que responda y abrir el navegador
rem ---------------------------------------------------------------
echo  [3/3] Esperando respuesta del portal...
where curl >nul 2>&1
if errorlevel 1 (
    echo        Sin curl: esperando 8 segundos.
    timeout /t 8 /nobreak >nul
    goto :listo
)
set /a N=0
:esperar
set /a N+=1
curl -sf -o nul --max-time 2 "%URL%/health" >nul 2>&1
if not errorlevel 1 goto :comprobar
if %N% GEQ 40 goto :sinrespuesta
timeout /t 1 /nobreak >nul
goto :esperar

:sinrespuesta
echo.
echo  [X] El servidor no respondio despues de 40 segundos.
echo      Revisa la ventana "PortalNOTAMsServidor" para ver el error.
goto :fin

:comprobar
set "DIAGVID=no"
set "DIAGDB=sin datos"
set "DIAGCOD=al dia"
curl -sf -o nul --max-time 3 "%URL%/static/hero-nubes.mp4" >nul 2>&1
if not errorlevel 1 set "DIAGVID=si"
curl -s --max-time 3 "%URL%/health" 2>nul | findstr "total_notams" >nul 2>&1
if not errorlevel 1 set "DIAGDB=cargados"
curl -s --max-time 3 "%URL%/health" 2>nul | findstr "codigo_actualizado.:false" >nul 2>&1
if not errorlevel 1 set "DIAGCOD=DESFASADO - reinicia"

:listo
start "" "%URL%"
echo.
echo  ===========================================================
echo    PORTAL ABIERTO EN  %URL%
echo  ===========================================================
echo.
echo    Video de fondo:      %DIAGVID%
echo    NOTAMs en la base:   %DIAGDB%
echo    Codigo cargado:      %DIAGCOD%
echo.
echo    Estado del sistema:  %URL%/health
echo    Documentacion API:   %URL%/docs
echo.
echo    Deja esta ventana abierta mientras uses el portal.
echo.
echo  -----------------------------------------------------------
echo    Pulsa una tecla para CERRAR el portal y detener todo.
echo  -----------------------------------------------------------
pause >nul

echo.
echo  Deteniendo el servidor...
call :detener
echo  Portal detenido.
timeout /t 2 /nobreak >nul
exit /b 0

rem ---------------------------------------------------------------
rem  Subrutina: matar lo que ocupe el puerto y la ventana del servidor
rem ---------------------------------------------------------------
:detener
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
taskkill /F /FI "WINDOWTITLE eq PortalNOTAMsServidor*" /T >nul 2>&1
exit /b 0

:fin
echo.
pause
endlocal
