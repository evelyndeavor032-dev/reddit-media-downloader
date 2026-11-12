@echo off
cd /d "%~dp0"
echo Reddit Media Downloader 3.6
py -c "import PyQt5, requests, yt_dlp, selenium" 2>nul
if errorlevel 1 (
  echo Installing required Python packages...
  py -m pip install --upgrade pip
  py -m pip install -r requirements.txt
)
py app.py
if errorlevel 1 (
  echo.
  echo The application closed with an error. Check the message above.
  pause
)
