@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
if exist "nova_exp\exp19_d2c13_2_clean\water\run_05" (
  echo D2C_13_2: water\run_05 already exists.
  exit /b 1
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
"%PYTHON_EXE%" -m d2c13_2.experiment ^
  --out-root "nova_exp\exp19_d2c13_2_clean" ^
  --corpus water ^
  --run-ids run_05 ^
  --max-parallel 1 ^
  --temperature 0.3 ^
  --verification-policy balanced ^
  --config "D2C_13_2\config.zihang-deepseek-v4.1-flash-1017.json" ^
  --no-content-cache ^
  --progress ^
  --quiet-summary ^
  --summary-name "experiment_summary_water_run05.json"
exit /b %ERRORLEVEL%