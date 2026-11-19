@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if not defined DEEPSEEK_API_KEY (
  echo D2C_13_2: DEEPSEEK_API_KEY is not set.
  exit /b 1
)
set "TARGET=nova_exp\exp17\water\run_05"
if not exist "%TARGET%" (
  echo D2C_13_2: original run_05 directory does not exist.
  exit /b 1
)
echo D2C_13_2: removing original %TARGET%
rmdir /s /q "%TARGET%"
if exist "%TARGET%" (
  echo D2C_13_2: failed to remove original run_05 directory.
  exit /b 1
)
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
echo D2C_13_2: rerunning water run_05 into %TARGET%
"%PYTHON_EXE%" -m d2c13_2.cli lang7-run ^
  --input-dir "data\Test-text\input\goldgen_gen5\water" ^
  --out-dir "%TARGET%" ^
  --config "D2C_13_2\config.deepseek-official.json" ^
  --temperature 0.3 ^
  --verification-policy balanced
exit /b %ERRORLEVEL%
