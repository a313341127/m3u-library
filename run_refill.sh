#!/usr/bin/env bash
set +e
cd /c/Users/win11/WorkBuddy/2026-08-20-15-02-10/m3u-library
PY=/c/Users/win11/.workbuddy/binaries/python/versions/3.13.12/python.exe
LOG=/c/Users/win11/WorkBuddy/2026-08-20-15-02-10/m3u-library/batch_all.log
echo "REFILL_START $(date)" >> "$LOG"

"$PY" -u main.py collect -n cc0cd --pages 2 --sources "速播,极速,火狐,西瓜,优酷,百度,豆瓣" --no-generate >> batch2.log 2>&1
echo "BATCH2_DONE $(date)" >> "$LOG"

"$PY" -u main.py collect -n cc0cd --pages 2 --sources "暴风,星球,量子,茅台" --no-generate >> batch3.log 2>&1
echo "BATCH3_DONE $(date)" >> "$LOG"

"$PY" -u main.py collect -n cc0cd --pages 2 --sources "360,旺旺,如意,率率" --no-generate >> batch4.log 2>&1
echo "BATCH4_DONE $(date)" >> "$LOG"

"$PY" -u main.py generate --txt --best >> batch_gen.log 2>&1
echo "GEN_EXIT_$?" >> "$LOG"
echo "ALL_DONE $(date)" >> "$LOG"
