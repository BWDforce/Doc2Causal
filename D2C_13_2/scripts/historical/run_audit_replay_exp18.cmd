@echo off
setlocal
cd /d "%~dp0.."
"python" "D2C_13_2\tools\run_canonical_audit.py" --exp-root "nova_exp\exp18" --out-dir "nova_exp\audit_d2c13_2_exp18"
exit /b %ERRORLEVEL%
