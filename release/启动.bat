@echo off
chcp 65001 >NUL
setlocal
cd /d "%~dp0"

rem ============================================================
rem  理赔后医护关怀 —— 一键启动（绿色版，无需安装任何东西）
rem  内置 Python 解释器 + 全部依赖，双击即用
rem ============================================================

rem -- 内置 Python（相对路径，PATH 无关）--
set "PYTHON=%~dp0runtime\python.exe"

if not exist "%PYTHON%" (
    echo [错误] 未找到内置运行时 runtime\python.exe
    echo 请确认本文件夹完整解压，未删除 runtime 子目录。
    pause
    exit /b 1
)

rem -- 检查依赖是否可用 --
"%PYTHON%" -c "import streamlit, rapidocr_onnxruntime, anthropic, pydantic, requests, openpyxl" >NUL 2>NUL
if errorlevel 1 (
    echo [提示] 首次运行：正在安装依赖（需联网，约 2-5 分钟）...
    "%PYTHON%" -m pip install --no-cache-dir -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

rem -- 跳过 Streamlit 首次邮箱询问 --
set "STREAMLIT_DIR=%USERPROFILE%\.streamlit"
if not exist "%STREAMLIT_DIR%\credentials.toml" (
    mkdir "%STREAMLIT_DIR%" 2>NUL
    echo [general]> "%STREAMLIT_DIR%\credentials.toml"
    echo email = "">> "%STREAMLIT_DIR%\credentials.toml"
)

echo [信息] 正在启动网页应用，请稍候...
echo        浏览器将自动打开 http://localhost:8501
echo        若未自动打开，请手动在浏览器输入该地址。
"%PYTHON%" -m streamlit run app.py

pause
