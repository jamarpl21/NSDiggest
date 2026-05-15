#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (or via sudo)." >&2
  exit 1
fi

if [[ "${#}" -ne 2 ]]; then
  echo "Usage: $0 <release_id> <artifact_tar_gz_path>" >&2
  exit 1
fi

APP_USER="${APP_USER:-nsdiggest}"
APP_GROUP="${APP_GROUP:-nsdiggest}"
APP_ROOT="${APP_ROOT:-/opt/nsdiggest}"
RELEASES_DIR="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"
ENV_FILE="${ENV_FILE:-/etc/nsdiggest/nsdiggest.env}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

RELEASE_ID="${1}"
ARTIFACT_PATH="${2}"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"

if [[ ! -f "${ARTIFACT_PATH}" ]]; then
  echo "Artifact not found: ${ARTIFACT_PATH}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ -e "${RELEASE_DIR}" ]]; then
  echo "Release already exists: ${RELEASE_DIR}" >&2
  exit 1
fi

echo ">>> Preparing directories"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${APP_ROOT}" "${RELEASES_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 /var/lib/nsdiggest /var/log/nsdiggest

echo ">>> Extracting artifact to ${RELEASE_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${RELEASE_DIR}"
tar -xzf "${ARTIFACT_PATH}" -C "${RELEASE_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${RELEASE_DIR}"

echo ">>> Building virtualenv"
sudo -u "${APP_USER}" python3 -m venv "${RELEASE_DIR}/.venv"
sudo -u "${APP_USER}" "${RELEASE_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${RELEASE_DIR}/.venv/bin/pip" install -r "${RELEASE_DIR}/requirements.txt"

echo ">>> Installing systemd units from release"
install -o root -g root -m 0644 "${RELEASE_DIR}/deploy/nsdiggest.service" /etc/systemd/system/nsdiggest.service
install -o root -g root -m 0644 "${RELEASE_DIR}/deploy/nsdiggest.timer" /etc/systemd/system/nsdiggest.timer
systemctl daemon-reload
systemctl enable nsdiggest.timer >/dev/null

echo ">>> Switching current release"
ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"

echo ">>> Fast startup check (no inbox processing)"
sudo -u "${APP_USER}" bash -c "
  set -euo pipefail
  cd '${CURRENT_LINK}'
  '${CURRENT_LINK}/.venv/bin/python' -m src.main --help >/dev/null
"

echo ">>> Timer status after deploy"
if systemctl is-active --quiet nsdiggest.timer; then
  echo "nsdiggest.timer is already active (no restart performed)."
else
  echo "nsdiggest.timer is inactive. Start manually when ready:"
  echo "  sudo systemctl start nsdiggest.timer"
fi

echo ">>> Cleaning up old releases (keep ${KEEP_RELEASES})"
mapfile -t release_paths < <(ls -1dt "${RELEASES_DIR}"/* 2>/dev/null || true)
if (( ${#release_paths[@]} > KEEP_RELEASES )); then
  for old_release in "${release_paths[@]:KEEP_RELEASES}"; do
    if [[ "$(readlink -f "${CURRENT_LINK}")" == "$(readlink -f "${old_release}")" ]]; then
      continue
    fi
    rm -rf "${old_release}"
  done
fi

echo ">>> Deploy complete: ${RELEASE_ID}"
