@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
if exist "nova_exp\exp19_d2c13_2_clean" (
  echo %* | findstr /I /C:"--resume" >nul
  if errorlevel 1 (
    echo D2C_13_2: nova_exp\exp19_d2c13_2_clean already exists.
    echo Remove or rename it before starting a clean run, or pass --resume.
    exit /b 1
  )
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
echo D2C_13_2 arbitration clean main run: nova_exp\exp19_d2c13_2_clean
"%PYTHON_EXE%" -m d2c13_2.experiment ^
  --out-root nova_exp\exp19_d2c13_2_clean ^
  --corpus both ^
  --runs 9 ^
  --temperature 0.3 ^
  --max-parallel 18 ^
  --verification-policy balanced ^
  --config "D2C_13_2\config.zihang-deepseek-v4.1-flash-1017.json" ^
  %*
exit /b %ERRORLEVEL%