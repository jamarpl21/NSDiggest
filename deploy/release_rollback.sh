#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (or via sudo)." >&2
  exit 1
fi

APP_USER="${APP_USER:-nsdiggest}"
APP_ROOT="${APP_ROOT:-/opt/nsdiggest}"
RELEASES_DIR="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"
ENV_FILE="${ENV_FILE:-/etc/nsdiggest/nsdiggest.env}"

if [[ ! -d "${RELEASES_DIR}" ]]; then
  echo "Releases directory not found: ${RELEASES_DIR}" >&2
  exit 1
fi

if [[ ! -L "${CURRENT_LINK}" ]]; then
  echo "Current symlink not found: ${CURRENT_LINK}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

target_release_path=""

if [[ "${#}" -ge 1 ]]; then
  target_release_path="${RELEASES_DIR}/${1}"
  if [[ ! -d "${target_release_path}" ]]; then
    echo "Target release not found: ${target_release_path}" >&2
    exit 1
  fi
else
  current_target="$(readlink -f "${CURRENT_LINK}")"
  mapfile -t sorted_releases < <(ls -1dt "${RELEASES_DIR}"/* 2>/dev/null || true)
  for candidate in "${sorted_releases[@]}"; do
    if [[ "$(readlink -f "${candidate}")" != "${current_target}" ]]; then
      target_release_path="${candidate}"
      break
    fi
  done

  if [[ -z "${target_release_path}" ]]; then
    echo "No previous release found for rollback." >&2
    exit 1
  fi
fi

target_release_id="$(basename "${target_release_path}")"
echo ">>> Rolling back to ${target_release_id}"
ln -sfn "${target_release_path}" "${CURRENT_LINK}"

echo ">>> Reloading systemd and ensuring timer is active"
install -o root -g root -m 0644 "${CURRENT_LINK}/deploy/nsdiggest.service" /etc/systemd/system/nsdiggest.service
install -o root -g root -m 0644 "${CURRENT_LINK}/deploy/nsdiggest.timer" /etc/systemd/system/nsdiggest.timer
systemctl daemon-reload
systemctl enable nsdiggest.timer >/dev/null
systemctl start nsdiggest.timer

echo ">>> Fast startup check after rollback (no inbox processing)"
sudo -u "${APP_USER}" bash -c "
  set -euo pipefail
  cd '${CURRENT_LINK}'
  '${CURRENT_LINK}/.venv/bin/python' -m src.main --help >/dev/null
"

echo ">>> Rollback complete: ${target_release_id}"
