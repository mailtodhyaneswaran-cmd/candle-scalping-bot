@echo off
cd /d "%~dp0"
echo [%date% %time%] US session starting >> log_us.txt
python strategy.py --session us >> log_us.txt 2>&1
echo [%date% %time%] US session finished >> log_us.txt
