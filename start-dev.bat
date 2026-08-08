@echo off
start "marketplace-backend" cmd /k "cd /d %~dp0backend && python run.py"
start "marketplace-frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
