#!/bin/bash
# 毎日午前7時に collect.py を自動実行するcronジョブを登録する

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
CRON_CMD="0 7 * * * cd \"$SCRIPT_DIR\" && \"$PYTHON\" collect.py >> \"$SCRIPT_DIR/data/collect.log\" 2>&1"

# 既存のcrontabを取得し、重複しないように追記
(crontab -l 2>/dev/null | grep -v "collect.py"; echo "$CRON_CMD") | crontab -

echo "cronジョブを登録しました:"
echo "  $CRON_CMD"
echo ""
echo "確認: crontab -l"
