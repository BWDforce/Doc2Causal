@echo off
setlocal
cd /d "%~dp0.."

if "%DEEPSEEK_API_KEY%"=="" (
  echo DEEPSEEK_API_KEY is not set
  exit /b 1
)

set "D2C11_API_MAX_CONCURRENCY=0"
"python" "D2C_13_2\tools\resume_missing_verdicts.py" --exp-root "nova_exp\exp18" --config "D2C_13_2\config.deepseek-official.run05-retry.json" --verification-policy balanced --run-workers 1 --run-path "win95pts/run_05" --summary-json "nova_exp\exp18\win95pts\run_05\verifier_resume_summary.json" --status-json "nova_exp\exp18\win95pts\run_05\verifier_status.json"
if errorlevel 1 exit /b %ERRORLEVEL%

"python" "D2C_13_2\tools\aggregate_child_summaries.py" --exp-root "nova_exp\exp18"
if errorlevel 1 exit /b %ERRORLEVEL%

"python" "D2C_13_2\tools\replay_report.py" --replay-root "nova_exp\exp18" --source-exp-root "nova_exp\exp17" --out "nova_exp\exp18\comparison_vs_exp17.json"
exit /b %ERRORLEVEL%