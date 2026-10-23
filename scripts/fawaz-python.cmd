@echo off
setlocal
for %%I in ("%~dp0..") do set "OM_ROOT=%%~fI"
set "PATH=%OM_ROOT%\.venv\Scripts;%OM_ROOT%\remotion-composer\node_modules\@remotion\compositor-win32-x64-msvc;%PATH%"
cd /d "%OM_ROOT%"
"%OM_ROOT%\.venv\Scripts\python.exe" %*
exit /b %errorlevel%
