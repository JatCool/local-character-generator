@echo off
rem Local Character Generator launcher (cmd / PowerShell).
rem Python: %CHARGEN_PYTHON% if set, else ComfyUI's embedded Python, else python on PATH.
setlocal
set "PY=%CHARGEN_PYTHON%"
if "%PY%"=="" if exist "C:\AI\ComfyUI\python_embeded\python.exe" set "PY=C:\AI\ComfyUI\python_embeded\python.exe"
if "%PY%"=="" set "PY=python"
"%PY%" -s "%~dp0character_generator.py" %*
exit /b %ERRORLEVEL%
