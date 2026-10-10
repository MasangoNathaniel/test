@echo off
REM Launch FreeMask. Drag a clip onto this file, or: freemask.bat clip.mp4 [options]
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python.exe" -m freemask %*
if errorlevel 1 pause
