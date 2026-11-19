@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "D2C_DIR=%~dp0.."
set "PYTHONPATH=%D2C_DIR%;%PYTHONPATH%"
if not defined PYTHON set "PYTHON=python"
if not defined DOCS_DIR set "DOCS_DIR=data\Test-text\input\goldgen_gen5\win95pts"

if not defined DEEPSEEK_API_KEY (
  echo exp20 recovery: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo exp20 recovery: Python not found: %PYTHON%
  exit /b 1
)
if not exist "%DOCS_DIR%" (
  echo exp20 recovery: document directory not found: %DOCS_DIR%
  echo Set DOCS_DIR to the local win95pts input directory.
  exit /b 1
)

echo exp20 recovery: reusing completed Lang7 extraction for win95pts/run_06
"%PYTHON%" -m d2c13_2 lang7-enhance ^
  --lang7-run-dir "nova_exp\exp20_d2c13_2_aliasguard_clean\win95pts\run_06\lang7" ^
  --out-dir "nova_exp\exp20_d2c13_2_aliasguard_clean\win95pts\run_06" ^
  --docs-dir "%DOCS_DIR%" ^
  --corpus win95pts ^
  --config "D2C_13_2\config.zihang-deepseek-v4.1-flash-1017.json" ^
  --chain-mode safe ^
  --verify ^
  --verification-policy balanced
exit /b %ERRORLEVEL%
