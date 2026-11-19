@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "D2C_DIR=%~dp0.."
set "PYTHONPATH=%D2C_DIR%;%PYTHONPATH%"
if not defined PYTHON set "PYTHON=python"
set "OUT_ROOT=nova_exp\exp20_d2c13_2_aliasguard_clean"

if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2 exp20: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
if exist "%OUT_ROOT%" (
  echo %* | findstr /I /C:"--resume" >nul
  if errorlevel 1 (
    echo D2C_13_2 exp20: %OUT_ROOT% already exists.
    echo Use a new --out-root or pass --resume only when intentionally resuming.
    exit /b 1
  )
)
"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo D2C_13_2 exp20: Python not found: %PYTHON%
  exit /b 1
)

echo D2C_13_2 exp20 clean run: %OUT_ROOT%
echo Nine fresh runs per corpus; alias guard and balanced verification enabled.
"%PYTHON%" -m d2c13_2.experiment ^
  --out-root "%OUT_ROOT%" ^
  --corpus both ^
  --runs 9 ^
  --temperature 0.3 ^
  --max-parallel 18 ^
  --verification-policy balanced ^
  --no-content-cache ^
  --progress ^
  --progress-interval 5 ^
  --progress-heartbeat 30 ^
  --config "D2C_13_2\config.zihang-deepseek-v4.1-flash-1017.json" ^
  %*
exit /b %ERRORLEVEL%
