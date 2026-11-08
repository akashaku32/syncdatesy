#!/usr/bin/env bash
# Install unixODBC + optional NetSuite SuiteAnalytics Connect ODBC driver (Linux).
# Build with INSTALL_NETSUITE_ODBC=1 and vendor bundle under NETSUITE_ODBC_VENDOR_DIR.
set -euo pipefail

INSTALL_NETSUITE_ODBC="${INSTALL_NETSUITE_ODBC:-0}"
if [[ "${INSTALL_NETSUITE_ODBC}" != "1" ]]; then
  echo "[netsuite-odbc] INSTALL_NETSUITE_ODBC=0 — skipping unixODBC + NetSuite driver."
  exit 0
fi

VENDOR_DIR="${NETSUITE_ODBC_VENDOR_DIR:-/build/vendor/netsuite-odbc}"
INSTALL_ROOT="${NETSUITE_ODBC_INSTALL_ROOT:-/opt/netsuite/odbcclient}"
MARKER_FILE="/etc/netsuite-odbc-installed"

echo "[netsuite-odbc] Installing unixODBC system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  unixodbc \
  unixodbc-dev \
  odbcinst \
  libodbc2 \
  ca-certificates \
  tar \
  gzip
rm -rf /var/lib/apt/lists/*

if [[ ! -d "${VENDOR_DIR}" ]]; then
  echo "[netsuite-odbc] ERROR: INSTALL_NETSUITE_ODBC=1 but vendor dir missing: ${VENDOR_DIR}" >&2
  exit 1
fi

WORK="${VENDOR_DIR}/.extract-work"
rm -rf "${WORK}"
mkdir -p "${WORK}"

shopt -s nullglob
archives=("${VENDOR_DIR}"/*.tgz "${VENDOR_DIR}"/*.tar.gz "${VENDOR_DIR}"/*.zip)
if [[ ${#archives[@]} -gt 0 ]]; then
  for archive in "${archives[@]}"; do
    echo "[netsuite-odbc] Extracting ${archive}..."
    case "${archive}" in
      *.zip) apt-get install -y --no-install-recommends unzip && unzip -q -o "${archive}" -d "${WORK}" ;;
      *) tar -xf "${archive}" -C "${WORK}" ;;
    esac
  done
fi

# Copy loose files/dirs from vendor (e.g. pre-extracted tree from CI).
if [[ -z "$(find "${WORK}" -mindepth 1 -print -quit 2>/dev/null || true)" ]]; then
  cp -a "${VENDOR_DIR}/." "${WORK}/" 2>/dev/null || true
fi

INSTALL_SH="$(find "${WORK}" "${VENDOR_DIR}" -name 'install.sh' -type f 2>/dev/null | head -n 1 || true)"
if [[ -z "${INSTALL_SH}" ]]; then
  echo "[netsuite-odbc] ERROR: No install.sh found under ${VENDOR_DIR}. Upload NetSuite Linux ODBC bundle." >&2
  exit 1
fi

INSTALL_DIR="$(dirname "${INSTALL_SH}")"
chmod +x "${INSTALL_SH}"
echo "[netsuite-odbc] Running ${INSTALL_SH} ..."
if ! (
  cd "${INSTALL_DIR}"
  if [[ ! -x "./install.sh" ]]; then
    exit 1
  fi
  ./install.sh -y || ./install.sh || bash ./install.sh
); then
  echo "[netsuite-odbc] ERROR: NetSuite install.sh failed in ${INSTALL_DIR}" >&2
  exit 1
fi

# Normalize install root if installer used a nested path.
if [[ ! -d "${INSTALL_ROOT}" ]]; then
  FOUND="$(find /opt/netsuite -maxdepth 3 -type d -name 'odbcclient' 2>/dev/null | head -n 1 || true)"
  if [[ -n "${FOUND}" ]]; then
    INSTALL_ROOT="${FOUND}"
  fi
fi

if [[ ! -d "${INSTALL_ROOT}" ]]; then
  echo "[netsuite-odbc] ERROR: Expected install root ${INSTALL_ROOT} not found after install.sh." >&2
  exit 1
fi

chmod -R a+rX "${INSTALL_ROOT}" || true

TRUSTSTORE="${INSTALL_ROOT}/cert/ca4.cer"
if [[ ! -f "${TRUSTSTORE}" ]]; then
  TRUSTSTORE="$(find "${INSTALL_ROOT}/cert" -name '*.cer' 2>/dev/null | head -n 1 || true)"
fi

LIB64="${INSTALL_ROOT}/lib64"
if [[ ! -d "${LIB64}" ]]; then
  LIB64="${INSTALL_ROOT}/lib"
fi

ODBC_CONF=""
if [[ -d "${INSTALL_ROOT}/odconf" ]]; then
  ODBC_CONF="${INSTALL_ROOT}/odconf"
fi

DRIVER_NAME="${NETSUITE_ODBC_DRIVER:-{NetSuite ODBC Drivers}}"
if command -v odbcinst >/dev/null 2>&1; then
  REGISTERED="$(odbcinst -q -d 2>/dev/null | head -n 1 || true)"
  if [[ -n "${REGISTERED}" ]]; then
    DRIVER_NAME="${REGISTERED}"
  fi
fi

cat > /etc/profile.d/netsuite-odbc.sh <<EOF
export LD_LIBRARY_PATH="${LIB64}:\${LD_LIBRARY_PATH:-}"
export NETSUITE_ODBC_DRIVER="${DRIVER_NAME}"
export NETSUITE_ODBC_TRUSTSTORE="${TRUSTSTORE:-/opt/netsuite/odbcclient/cert/ca4.cer}"
export NETSUITE_ODBC_INSTALLED=1
EOF
chmod 644 /etc/profile.d/netsuite-odbc.sh

{
  echo "install_root=${INSTALL_ROOT}"
  echo "driver_name=${DRIVER_NAME}"
  echo "truststore=${TRUSTSTORE}"
  echo "lib64=${LIB64}"
  echo "odbc_conf=${ODBC_CONF}"
} > "${MARKER_FILE}"

rm -rf "${WORK}"
echo "[netsuite-odbc] Install complete. Driver: ${DRIVER_NAME}"
