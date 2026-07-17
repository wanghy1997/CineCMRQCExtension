#!/bin/zsh

set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
VERSION="0.2.2"
PACKAGE_NAME="CineCMRQCExtension-${VERSION}"
DIST_DIR="${PROJECT_ROOT}/dist"
STAGING_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/cine-cmr-qc-package-XXXXXX")
PACKAGE_ROOT="${STAGING_ROOT}/${PACKAGE_NAME}"
ZIP_PATH="${DIST_DIR}/${PACKAGE_NAME}.zip"

cleanup() {
  rm -rf "${STAGING_ROOT}"
}
trap cleanup EXIT

mkdir -p "${PACKAGE_ROOT}/CineCMRQC" "${PACKAGE_ROOT}/docs" "${DIST_DIR}"

cp "${PROJECT_ROOT}/CMakeLists.txt" "${PACKAGE_ROOT}/CMakeLists.txt"
cp "${PROJECT_ROOT}/CineCMRQC/CMakeLists.txt" "${PACKAGE_ROOT}/CineCMRQC/CMakeLists.txt"
cp "${PROJECT_ROOT}/CineCMRQC/CineCMRQC.py" "${PACKAGE_ROOT}/CineCMRQC/CineCMRQC.py"
cp "${PROJECT_ROOT}/docs/PORTABLE_INSTALL_zh-CN.md" "${PACKAGE_ROOT}/docs/INSTALL_zh-CN.md"
cp "${PROJECT_ROOT}/packaging/RELEASE.json" "${PACKAGE_ROOT}/RELEASE.json"
cp "${PROJECT_ROOT}/packaging/NOTICE.txt" "${PACKAGE_ROOT}/NOTICE.txt"

if [[ -f "${ZIP_PATH}" ]]; then
  unlink "${ZIP_PATH}"
fi

(
  cd "${STAGING_ROOT}"
  /usr/bin/zip -X -q -r "${ZIP_PATH}" "${PACKAGE_NAME}"
)

(
  cd "${DIST_DIR}"
  shasum -a 256 "${PACKAGE_NAME}.zip" > "${PACKAGE_NAME}.zip.sha256"
)

print "Package: ${ZIP_PATH}"
print "SHA-256: $(awk '{print $1}' "${ZIP_PATH}.sha256")"
