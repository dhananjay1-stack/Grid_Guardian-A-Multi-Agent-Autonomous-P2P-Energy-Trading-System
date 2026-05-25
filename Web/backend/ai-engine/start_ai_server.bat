@echo off
REM Grid-Guardian AI Decision Engine Startup Script (Windows)
REM This script starts the Python AI inference server

echo ====================================================
echo Grid-Guardian AI Decision Engine
echo ====================================================

REM Configuration
set AI_SERVER_HOST=127.0.0.1
set AI_SERVER_PORT=5050
set AI_SERVER_DEBUG=false
set POLICY_PACK_PATH=%~dp0..\..\..\..\Agentic_AI\edge\policy_pack

echo.
echo Configuration:
echo   Host: %AI_SERVER_HOST%
echo   Port: %AI_SERVER_PORT%
echo   Policy Pack: %POLICY_PACK_PATH%
echo.

REM Check if Python is available
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.8+ and add it to PATH
    pause
    exit /b 1
)

REM Check if policy pack exists
if not exist "%POLICY_PACK_PATH%\cql_policy.torchscript" (
    if not exist "%POLICY_PACK_PATH%\cql_policy.onnx" (
        echo ERROR: No model file found in policy pack
        echo Expected: %POLICY_PACK_PATH%\cql_policy.torchscript
        echo       or: %POLICY_PACK_PATH%\cql_policy.onnx
        pause
        exit /b 1
    )
)

echo Policy pack found. Starting AI server...
echo.

REM Start the AI inference server
cd /d %~dp0
python ai_inference_server.py

pause
