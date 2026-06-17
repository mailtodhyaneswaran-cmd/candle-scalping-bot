@echo off
cd /d "%~dp0"
python strategy.py >> run_log.txt 2>&1
