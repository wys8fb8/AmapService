@echo off
REM 启动 cron 守护进程(拉数据落库 + MQTT 发布)。配置见 config\config.yaml。
cd /d "%~dp0"
amap-service.exe run -c config\config.yaml
pause
