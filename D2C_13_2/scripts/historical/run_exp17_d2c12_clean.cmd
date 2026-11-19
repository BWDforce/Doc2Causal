@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
if exist "nova_exp\exp17" (
  echo D2C_13_2: nova_exp\exp17 already exists.
  echo Remove or rename it before starting a clean exp17 run.
  exit /b 1
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
echo D2C_13_2 clean main run: nova_exp\exp17
"%PYTHON_EXE%" -m d2c13_2.experiment ^
  --out-root nova_exp\exp17 ^
  --corpus both ^
  --runs 9 ^
  --temperature 0.3 ^
  --max-parallel 18 ^
  --verification-policy balanced ^
  --config "D2C_13_2\config.deepseek-official.json"
exit /b %ERRORLEVEL%
