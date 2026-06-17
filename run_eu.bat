@echo off
cd /d "%~dp0"
echo [%date% %time%] EU session starting >> log_eu.txt
python strategy.py --session eu >> log_eu.txt 2>&1
echo [%date% %time%] EU session finished >> log_eu.txt
