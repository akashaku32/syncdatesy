@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollout_migration_function.ps1" %*
