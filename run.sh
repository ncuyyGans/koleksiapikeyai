#!/usr/bin/env bash
# Supervisor for the Telegram bot inside GitHub Actions.
#
# Responsibilities:
#   1. Run the bot, restart it if it crashes.
#   2. Periodically commit+push the encrypted DB so data survives rotation.
#   3. When the bot exits with the "rotate" code (time limit reached), commit
#      the final state and re-dispatch the workflow so the bot runs 24/7.
#
# Exit code 20 from bot.py == "rotate me" (time budget spent).

set -u

cd "$(dirname "$0")"

ROTATE_CODE=20
RUN_BUDGET="${BOT_MAX_RUNTIME_SEC:-18000}"   # 5h, safely under the 6h job limit
started_at=$(date +%s)

echo "==> Installing dependencies"
python3 -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
python3 -m pip install --quiet -r requirements.txt || {
  echo "==> pip install failed; retrying with --user"
  python3 -m pip install --quiet --user -r requirements.txt
}

echo "==> Configuring git"
git config user.name "bot-supervisor"
git config user.email "bot-supervisor@users.noreply.github.com"
git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" >/dev/null 2>&1 || true

commit_db() {
  # Commit the encrypted DB if it exists and changed since the last commit.
  if [ -f data.db.enc ]; then
    if ! git diff --quiet -- data.db.enc 2>/dev/null; then
      git add data.db.enc
      git commit -m "chore: auto-backup encrypted db [skip ci]" >/dev/null 2>&1 && {
        git push -f origin HEAD:"${GITHUB_REF_NAME:-main}" >/dev/null 2>&1 \
          && echo "==> pushed db backup" \
          || echo "!!  db push failed (non-fatal)"
      }
    fi
  fi
}

# Background auto-backup loop every 5 minutes.
(
  while true; do
    sleep 300
    commit_db
  done
) &
backup_pid=$!

trap 'kill $backup_pid 2>/dev/null; commit_db' EXIT INT TERM

echo "==> Starting bot (budget ${RUN_BUDGET}s)"
exit_code=1
while true; do
  python3 bot.py
  exit_code=$?
  elapsed=$(( $(date +%s) - started_at ))
  echo "==> bot.py exited code=${exit_code} after ${elapsed}s"

  if [ "${exit_code}" -eq "${ROTATE_CODE}" ]; then
    echo "==> rotation requested"
    break
  fi

  # Crash or clean exit: if we still have budget, restart; otherwise rotate.
  if [ "${elapsed}" -ge "${RUN_BUDGET}" ]; then
    echo "==> budget spent, rotating"
    break
  fi

  # Guard against a tight crash loop: wait longer if the run was very short.
  if [ "${elapsed}" -lt 120 ]; then
    echo "!!  bot died too fast; sleeping 60s before retry"
    sleep 60
  else
    sleep 5
  fi
done

kill "$backup_pid" 2>/dev/null || true
echo "==> Final backup"
commit_db

# Re-dispatch this workflow so the bot keeps running.
if [ -n "${REDISPATCH:-1}" ]; then
  echo "==> Re-dispatching workflow"
  gh workflow run "${GITHUB_WORKFLOW}" \
    --ref "${GITHUB_REF_NAME:-main}" \
    || echo "!!  could not re-dispatch; the schedule cron will pick it up"
fi

echo "==> Supervisor done (exit ${exit_code})"
exit 0
