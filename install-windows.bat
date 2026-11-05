@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo Super Rami - installing...
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Opening the download page - install it, tick "Add python.exe to PATH", then run this file again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist "%USERPROFILE%\.claude\skills" mkdir "%USERPROFILE%\.claude\skills"
if not exist "%USERPROFILE%\super-rami-data" mkdir "%USERPROFILE%\super-rami-data"
if exist "%USERPROFILE%\.claude\skills\super-rami" rmdir /s /q "%USERPROFILE%\.claude\skills\super-rami"
xcopy /e /i /q "skills\super-rami" "%USERPROFILE%\.claude\skills\super-rami" >nul
echo.
echo Done! In Claude Code say: super rami, pick stocks for me
echo Or double-click start-windows.bat for the menu.
python -c "import ib_async" >nul 2>nul
if not errorlevel 1 goto end
set /p a=Install the extra library for the SPX bot? [y/n]: 
if /i "%a%"=="y" python -m pip install --user ib_async
:end
pause
