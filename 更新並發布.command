#!/bin/zsh
# Optional terminal fallback; normal use is 更新並發布.app.
cd "${0:A:h}"
/bin/zsh scripts/launch_publisher.sh "$@"
TASK_RESULT=$?
print '\n完成。按 Enter 關閉。'
read -r
exit "$TASK_RESULT"
