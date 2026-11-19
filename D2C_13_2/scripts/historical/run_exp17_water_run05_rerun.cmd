@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
if exist "nova_exp\exp17_rerun\water\run_05" (
  echo D2C_13_2: nova_exp\exp17_rerun\water\run_05 already exists.
  echo Remove or rename it before rerunning water run_05.
  exit /b 1
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
echo D2C_13_2 water run_05 rerun: nova_exp\exp17_rerun\water\run_05
"%PYTHON_EXE%" -m d2c13_2.cli lang7-run ^
  --input-dir "data\Test-text\input\goldgen_gen5\water" ^
  --out-dir "nova_exp\exp17_rerun\water\run_05" ^
  --config "D2C_13_2\config.deepseek-official.json" ^
  --temperature 0.3 ^
  --verification-policy balanced
exit /b %ERRORLEVEL%
