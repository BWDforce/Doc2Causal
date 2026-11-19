@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if not defined PYTHON set "PYTHON=python"
if not defined CONTROLLED_REPORT set "CONTROLLED_REPORT=nova_exp\exp20_d2c13_2_aliasguard_clean\acceptance\e4\e4_report.json"
if not defined CSB_ROOT set "CSB_ROOT=CSB_exp\results\csb_d2c13_2_aliasguard_latest"
if not defined PARITY_OUT set "PARITY_OUT=nova_exp\exp_sparql_parity_d2c13_2"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo SPARQL parity: Python not found: %PYTHON%
  exit /b 1
)
if not exist "%CONTROLLED_REPORT%" (
  echo SPARQL parity: controlled report not found: %CONTROLLED_REPORT%
  echo Set CONTROLLED_REPORT to the frozen e4_report.json path.
  exit /b 1
)
if not exist "%CSB_ROOT%" (
  echo SPARQL parity: CSB root not found: %CSB_ROOT%
  echo Set CSB_ROOT to the prepared CSB result directory.
  exit /b 1
)

"%PYTHON%" "D2C_13_2\tools\compare_sparql_parity.py" ^
  --controlled-report "%CONTROLLED_REPORT%" ^
  --csb-root "%CSB_ROOT%" ^
  --output-dir "%PARITY_OUT%"
exit /b %ERRORLEVEL%
