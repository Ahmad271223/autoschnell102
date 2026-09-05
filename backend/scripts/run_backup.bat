@echo off
rem Huelle fuer den Windows-Taskplaner: fuehrt das Backup aus und schreibt
rem saemtliche Ausgaben (auch Fehler) in task_run.log neben den Backups.
if not exist "C:\AutoSchnell-Backups" mkdir "C:\AutoSchnell-Backups"
echo ==== %date% %time% ==== >> "C:\AutoSchnell-Backups\task_run.log"
"C:\Python314\python.exe" -X utf8 "%~dp0backup_mongo.py" >> "C:\AutoSchnell-Backups\task_run.log" 2>&1
echo Exitcode: %errorlevel% >> "C:\AutoSchnell-Backups\task_run.log"
