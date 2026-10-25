@echo off
REM Builds SwitchOverlay.exe (a single file, no Python needed to run it).
REM Needs Python on THIS computer only. Put this file next to switch_overlay.py and double-click it.
cd /d "%~dp0"
python -m pip install --upgrade pip pillow pyinstaller
if errorlevel 1 goto fail
python -m PyInstaller --onefile --noconsole --clean --name SwitchOverlay switch_overlay.py
if errorlevel 1 goto fail
copy /y dist\SwitchOverlay.exe SwitchOverlay.exe >nul
echo.
echo Done. SwitchOverlay.exe is in this folder - copy just that file to her laptop.
pause
exit /b 0
:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
