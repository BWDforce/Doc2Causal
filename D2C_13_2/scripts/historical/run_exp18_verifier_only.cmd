@echo off
setlocal
cd /d "%~dp0.."

if /I "%~1"=="--dry-run" (
  "python" "D2C_13_2\tools\run_experiment_parallel_processes.py" --exp-root "nova_exp\exp18" --config "D2C_13_2\config.deepseek-official.json" --verification-policy balanced --max-parallel 18 --source-exp-root "nova_exp\exp17" --comparison-out "nova_exp\exp18\comparison_vs_exp17.json" --dry-run
  exit /b %ERRORLEVEL%
)

if "%DEEPSEEK_API_KEY%"=="" (
  echo DEEPSEEK_API_KEY is not set
  exit /b 1
)

set "D2C11_API_MAX_CONCURRENCY=0"
"python" "D2C_13_2\tools\run_experiment_parallel_processes.py" --exp-root "nova_exp\exp18" --config "D2C_13_2\config.deepseek-official.json" --verification-policy balanced --max-parallel 18 --source-exp-root "nova_exp\exp17" --comparison-out "nova_exp\exp18\comparison_vs_exp17.json"
exit /b %ERRORLEVEL%