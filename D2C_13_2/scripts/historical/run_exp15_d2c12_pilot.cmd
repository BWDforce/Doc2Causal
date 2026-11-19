@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
set "ALLOW_EXISTING="
for %%A in (%*) do if /I "%%A"=="--resume" set "ALLOW_EXISTING=1"
if exist "nova_exp\exp15_d2c13_2_pilot" if not defined ALLOW_EXISTING (
  echo D2C_13_2: nova_exp\exp15_d2c13_2_pilot already exists.
  echo Use --resume to continue checkpoints or rename the directory for a clean pilot.
  exit /b 1
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
echo D2C_13_2 entity-first pilot: nova_exp\exp15_d2c13_2_pilot
"%PYTHON_EXE%" -m d2c13_2.experiment --out-root nova_exp\exp15_d2c13_2_pilot --corpus both --runs 3 --temperature 0.3 --max-parallel 6 --verification-policy balanced --progress --progress-interval 2 --progress-heartbeat 10 %*
exit /b %ERRORLEVEL%