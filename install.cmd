@echo off
rem Instala tudo o que os agentes precisam. Pode ser executado de novo sem problemas.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
