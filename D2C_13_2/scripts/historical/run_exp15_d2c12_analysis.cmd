@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if defined CONDA_PREFIX (
  set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
) else (
  set "PYTHON_EXE=python"
)
if not exist "nova_exp\exp15_d2c13_2_pilot\experiment_summary.json" (
  echo D2C_13_2: exp15_d2c13_2_pilot is not complete yet.
  exit /b 1
)
"%PYTHON_EXE%" D2C_13_2\tools\diagnose_raw_recall.py --exp-root nova_exp\exp15_d2c13_2_pilot
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" D2C_13_2\tools\exp15_analysis.py --exp-root exp15_d2c13_2_pilot
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" D2C_13_2\tools\exp15_report.py --summary nova_exp\exp15_d2c13_2_pilot\analysis_summary.json --out nova_exp\exp15_d2c13_2_pilot\comparison_d2c9.md
exit /b %ERRORLEVEL%
