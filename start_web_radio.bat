@echo off
cd /d %%~dp0
echo Starting SDR-BoomBox web radio server...
python web_radio_server.py
pause
