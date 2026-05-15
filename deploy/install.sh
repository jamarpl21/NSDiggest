#!/usr/bin/env bash
# Install NSDiggest on a Linux server (run as root on target host).
# Run as root: bash install.sh
set -euo pipefail

APP_USER=nsdiggest
APP_DIR=/opt/nsdiggest
DATA_DIR=/var/lib/nsdiggest
LOG_DIR=/var/log/nsdiggest
ENV_DIR=/etc/nsdiggest

echo ">>> Installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  echo ">>> Creating user ${APP_USER}"
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo ">>> Creating directories"
install -d -o "${APP_USER}" -g "${APP_USER}" -m 0750 "${APP_DIR}" "${DATA_DIR}" "${LOG_DIR}"
install -d -o root -g "${APP_USER}" -m 0750 "${ENV_DIR}"

echo ">>> Copying app sources"
# Expect the repo to be unpacked alongside this script (or pull via git).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rsync -a --delete \
  --exclude='.env' --exclude='.git' --exclude='data' --exclude='logs' --exclude='.venv' \
  "${SCRIPT_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo ">>> Building virtualenv"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if [[ ! -f "${ENV_DIR}/nsdiggest.env" ]]; then
  echo ">>> Seeding /etc/nsdiggest/nsdiggest.env (EDIT before enabling)"
  install -o root -g "${APP_USER}" -m 0640 "${APP_DIR}/.env.example" "${ENV_DIR}/nsdiggest.env"
  # Force production-safe defaults
  sed -i 's|^DATA_DIR=.*|DATA_DIR=/var/lib/nsdiggest|' "${ENV_DIR}/nsdiggest.env"
  echo "!!! Edit ${ENV_DIR}/nsdiggest.env and fill ANTHROPIC_API_KEY before enabling the timer."
fi

echo ">>> Installing systemd units"
install -o root -g root -m 0644 "${APP_DIR}/deploy/nsdiggest.service" /etc/systemd/system/nsdiggest.service
install -o root -g root -m 0644 "${APP_DIR}/deploy/nsdiggest.timer"   /etc/systemd/system/nsdiggest.timer
systemctl daemon-reload
systemctl enable nsdiggest.timer

echo
echo "Install complete."
echo "Next steps:"
echo "  1. Edit ${ENV_DIR}/nsdiggest.env (fill ANTHROPIC_API_KEY, verify Gmail app password)."
echo "  2. Smoke-test:   sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python -m src.main --dry-run"
echo "  3. Start timer:  systemctl start nsdiggest.timer"
echo "  4. One-shot run: systemctl start nsdiggest.service"
echo "  5. Inspect:      journalctl -u nsdiggest.service -e   /   tail -f ${LOG_DIR}/run.log"
