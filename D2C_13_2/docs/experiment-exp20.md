# exp20: D2C_13_2 alias-guard clean run

## Recommendation

Use `n=9` runs per corpus for the paper-facing full experiment.

## Run from CMD

```bat
cd /d "<repo-root>"
set "DEEPSEEK_API_KEY=YOUR_KEY"
D2C_13_2\run_exp20_d2c13_2_aliasguard_clean.cmd
```

The wrapper uses fresh extraction, 9 runs per corpus, 18 parallel runs,
temperature 0.3, balanced verification, and the zihang DeepSeek config.

Output:

```text
nova_exp\exp20_d2c13_2_aliasguard_clean
```

## Recover a failed run after Lang7 extraction completed

If only the final artifact compilation failed, do not redo extraction. Use:

```bat
cd /d "<repo-root>"
set "DEEPSEEK_API_KEY=YOUR_KEY"
D2C_13_2\run_exp20_win95_run06_recover.cmd
```

This reuses the completed `win95pts/run_06/lang7` directory and reruns only
D2C compilation plus verification.

The 2026-11-13 failure was fixed in
`d2c13_2/canonical_arbitration.py`: unresolved/self-loop edges now receive
`canonical_support_tier=none` so support-tier sorting cannot compare `None`
with strings.
