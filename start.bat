@echo off
title finv - Local dev server

echo ============================================
echo   finv - Inventory Management System
echo   Local development server
echo ============================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [..] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERR] Failed to create venv. Is Python installed?
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

echo [..] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERR] Failed to install dependencies.
    pause
    exit /b 1
)

echo [..] Running migrations...
python manage.py migrate --noinput
if errorlevel 1 (
    echo [ERR] Migration failed.
    pause
    exit /b 1
)

echo [..] Collecting static files...
python manage.py collectstatic --noinput --clear
if errorlevel 1 (
    echo [ERR] Static files collection failed.
    pause
    exit /b 1
)

echo [..] Checking superuser...
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', 'admin123')" >nul 2>&1

echo.
echo ============================================
echo   Server:  http://127.0.0.1:8000
echo   Admin:   http://127.0.0.1:8000/admin/
echo   Login:   admin
echo   Pass:    admin123
echo   Stop:    Ctrl+C
echo ============================================
echo.

python manage.py runserver 127.0.0.1:8000

echo.
echo [INFO] Server stopped.
pause
