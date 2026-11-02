@echo off
title ElderCare MedTrack - Senior Medicine Companion
echo =====================================================================
echo    ELDERCARE MEDTRACK - SENIOR MEDICINE TRACKER ^& DOORSTEP REFILL
echo =====================================================================
echo.
echo Initializing database and starting server...
python database.py
start http://127.0.0.1:5000
python app.py
pause
