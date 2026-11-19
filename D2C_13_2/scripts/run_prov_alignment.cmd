@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
if not defined PYTHON set "PYTHON=python"
if not defined CONTROLLED_ROOT set "CONTROLLED_ROOT=nova_exp\exp20_d2c13_2_aliasguard_clean"
if not defined CSB_ROOT set "CSB_ROOT=CSB_exp\results\csb_d2c13_2_aliasguard_latest"
if not defined PROV_OUT set "PROV_OUT=nova_exp\exp_prov_alignment_d2c13_2"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo PROV alignment: Python not found: %PYTHON%
  exit /b 1
)
if not exist "%CONTROLLED_ROOT%" (
  echo PROV alignment: controlled artifact root not found: %CONTROLLED_ROOT%
  echo Set CONTROLLED_ROOT to the prepared controlled artifact directory.
  exit /b 1
)
if not exist "%CSB_ROOT%" (
  echo PROV alignment: CSB artifact root not found: %CSB_ROOT%
  echo Set CSB_ROOT to the prepared CSB artifact directory.
  exit /b 1
)

"%PYTHON%" "D2C_13_2\tools\check_prov_alignment.py" ^
  --artifact-root "%CONTROLLED_ROOT%" ^
  --artifact-root "%CSB_ROOT%" ^
  --output-dir "%PROV_OUT%"
exit /b %ERRORLEVEL%
