@echo off
REM 启动 HTTP API 进程(需 config 中 api.enabled: true)。Swagger: http://<host>:<port>/docs
cd /d "%~dp0"
amap-service.exe serve -c config\config.yaml
pause
