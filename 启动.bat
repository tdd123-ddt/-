@echo off
setlocal
cd /d "%~dp0"

rem -- Use local Python (absolute path, PATH-independent), deps pre-installed --
set "PYTHON=C:\Users\33689\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found at:
    echo   %PYTHON%
    echo Please install Python 3.10+ and check "Add python.exe to PATH".
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

"%PYTHON%" -c "import streamlit, rapidocr_onnxruntime, anthropic, pydantic, requests" >NUL 2>NUL
if errorlevel 1 (
    echo [INFO] First run: installing dependencies, this may take a few minutes...
    "%PYTHON%" -m pip install --no-cache-dir -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies. Check your network and retry.
        pause
        exit /b 1
    )
)

rem -- Skip Streamlit first-run email prompt --
set "STREAMLIT_DIR=%USERPROFILE%\.streamlit"
if not exist "%STREAMLIT_DIR%\credentials.toml" (
    mkdir "%STREAMLIT_DIR%" 2>NUL
    echo [general]> "%STREAMLIT_DIR%\credentials.toml"
    echo email = "">> "%STREAMLIT_DIR%\credentials.toml"
)

echo [INFO] Starting web app. Open http://localhost:8501 if the browser does not open automatically.
"%PYTHON%" -m streamlit run app.py
pause
