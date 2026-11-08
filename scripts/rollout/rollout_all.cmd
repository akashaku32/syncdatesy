@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollout_all.ps1" %*
