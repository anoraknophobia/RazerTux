#!/usr/bin/env bash
# RazerTux installer – not an official Razer product; not affiliated with Razer Inc.
#
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove RazerTux (your settings in ~/.config/razertux stay)
#   ./install.sh --help
#
# Run it as your normal user from the unpacked source folder; it asks for your password
# (sudo) for the system-wide steps.
#
# Arch Linux and derivatives (CachyOS, EndeavourOS, Manjaro …): builds and installs the
# pacman package from packaging/PKGBUILD.
# Other distributions (Debian, Ubuntu, Linux Mint, Fedora, openSUSE …): installs the
# dependencies with the distribution's package manager, RazerTux itself into /opt/razertux
# and the same system files the Arch package has. Files outside /opt/razertux are listed in
# /opt/razertux/installed-files, which --uninstall uses.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SRC/packaging"
OPT=/opt/razertux
MANIFEST="$OPT/installed-files"
COMMANDS=(razertux razertuxd razertux-gui razertux-record)
WORK=""  # temporary folder of the generic install (removed on exit)

cleanup() { if [ -n "$WORK" ]; then sudo rm -rf -- "$WORK"; fi; }
trap cleanup EXIT

say()  { printf '\033[1;32m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m:: warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m:: error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# -- system files (generic install) ---------------------------------------------------------
# source file in packaging/ | destination | mode. Keep in sync with package() in PKGBUILD
# (tests/test_packaging.py checks that).
generic_files() {
  cat <<EOF
70-razertux.rules|/etc/udev/rules.d/70-razertux.rules|644
razertuxd.service|/etc/systemd/user/razertuxd.service|644
io.github.razertux.service|/usr/local/share/dbus-1/services/io.github.razertux.service|644
razertux-record-helper|/usr/lib/razertux/razertux-record-helper|755
io.github.razertux.record.policy|/usr/share/polkit-1/actions/io.github.razertux.record.policy|644
razertux.desktop|/usr/local/share/applications/razertux.desktop|644
razertux-tray.desktop|/etc/xdg/autostart/razertux-tray.desktop|644
razertux.svg|/usr/local/share/icons/hicolor/scalable/apps/razertux.svg|644
EOF
}

# The packaging files name /usr/bin/...; the generic install runs everything from /opt.
adapt() {
  case "$1" in
    razertux-record-helper)
      printf '#!/bin/sh\n# Root-owned wrapper for pkexec: isolated mode (-I) keeps cwd and PYTHON* variables off sys.path.\nexec %s/bin/python3 -I -m razertux.recorder.helper "$@"\n' "$OPT" ;;
    *.service)
      sed "s#/usr/bin/razertuxd#$OPT/bin/razertuxd#" "$PKG/$1" ;;
    *.desktop)
      sed "s#^Exec=razertux-gui#Exec=$OPT/bin/razertux-gui#" "$PKG/$1" ;;
    *)
      cat "$PKG/$1" ;;
  esac
}

# -- distribution ---------------------------------------------------------------------------
distro_family() {
  local ids=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    ids="$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")"
  fi
  case " $ids " in
    *" arch "*|*" archlinux "*) echo arch ;;
    *" debian "*|*" ubuntu "*) echo apt ;;
    *" fedora "*|*" rhel "*|*" centos "*) echo dnf ;;
    *suse*) echo zypper ;;
    *) echo other ;;
  esac
}

# One package at a time, so a name missing on this release does not stop the others;
# what is missing comes from PyPI later (Python modules) or is reported.
install_each() {
  local family="$1"; shift
  local p
  for p in "$@"; do
    case "$family" in
      apt)    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$p" >/dev/null 2>&1 ;;
      dnf)    sudo dnf install -y -q "$p" >/dev/null 2>&1 ;;
      zypper) sudo zypper --non-interactive --quiet install "$p" >/dev/null 2>&1 ;;
    esac && echo "   ok   $p" || echo "   --   $p (not available, skipped)"
  done
}

install_dependencies() {
  local family="$1"
  say "Installing dependencies ($family)"
  case "$family" in
    apt)
      sudo apt-get update -q >/dev/null
      install_each apt python3 python3-venv python3-pip python3-evdev python3-pyudev \
        python3-dbus-next python3-pyside6.qtwidgets python3-pyside6.qtsvg python3-pyside6.qtnetwork \
        polkitd pkexec policykit-1 libxcb-cursor0 libegl1 libxkbcommon-x11-0 ;;
    dnf)
      install_each dnf python3 python3-pip python3-evdev python3-pyudev python3-dbus-next \
        python3-pyside6 polkit xcb-util-cursor ;;
    zypper)
      install_each zypper python3 python3-pip python3-evdev python3-pyudev python3-dbus-next \
        python3-pyside6 polkit libxcb-cursor0 ;;
    other)
      warn "Unknown distribution: please install Python >= 3.11, python3-venv, polkit and (if"
      warn "available as packages) python-evdev, pyudev, dbus-next and PySide6 yourself." ;;
  esac
}

check_python() {
  command -v python3 >/dev/null || die "python3 not found – please install Python 3.11 or newer"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || die "Python $(python3 -c 'import platform; print(platform.python_version())') is too old; RazerTux needs 3.11 or newer"
  python3 -c 'import venv, ensurepip' 2>/dev/null \
    || die "python3 cannot create virtual environments (install python3-venv / python3-pip)"
}

warn_openrazer() {
  if lsmod 2>/dev/null | grep -q '^razermouse' || command -v openrazer-daemon >/dev/null; then
    warn "OpenRazer is installed. It accesses the same devices and interferes with RazerTux;"
    warn "please uninstall it (or at least unload the razermouse driver)."
  fi
}

# -- generic install -----------------------------------------------------------------------
install_generic() {
  local family="$1" src dest mode cmd
  install_dependencies "$family"
  check_python
  say "Installing RazerTux into $OPT"
  if [ ! -x "$OPT/bin/python3" ]; then
    # system site packages: use the distribution's evdev/PySide6 where they exist
    sudo python3 -m venv --system-site-packages "$OPT"
  fi
  WORK="$(mktemp -d)"
  # build from a clean copy: pip must not write build/ or *.egg-info into the source folder
  mkdir "$WORK/src"
  cp -r "$SRC/razertux" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$WORK/src/"
  find "$WORK/src" -name __pycache__ -prune -exec rm -rf {} +
  sudo "$OPT/bin/python3" -m pip install --quiet --disable-pip-version-check --no-cache-dir \
    --upgrade "$WORK/src" || die "pip could not install RazerTux (see the messages above)"
  sudo "$OPT/bin/python3" -c 'import evdev, pyudev, dbus_next, PySide6.QtWidgets, razertux' \
    || die "RazerTux was installed but cannot be imported (see the messages above)"

  say "Installing system files"
  sudo install -d "$OPT/share"
  sudo install -m644 "$SRC/README.md" "$SRC/LICENSE" "$PKG/OPENRAZER-NOTICE" "$OPT/share/"
  : | sudo tee "$MANIFEST" >/dev/null
  while IFS='|' read -r src dest mode; do
    adapt "$src" > "$WORK/file"
    sudo install -D -m"$mode" "$WORK/file" "$dest"
    echo "$dest" | sudo tee -a "$MANIFEST" >/dev/null
  done < <(generic_files)
  for cmd in "${COMMANDS[@]}"; do
    sudo ln -sfn "$OPT/bin/$cmd" "/usr/local/bin/$cmd"
    echo "/usr/local/bin/$cmd" | sudo tee -a "$MANIFEST" >/dev/null
  done
  if command -v udevadm >/dev/null; then
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger --action=change --subsystem-match=hidraw --subsystem-match=input \
      --subsystem-match=misc || true
  fi
}

uninstall_generic() {
  [ -f "$MANIFEST" ] || die "No RazerTux installation found in $OPT"
  local f
  while read -r f; do
    case "$f" in
      /etc/*|/usr/local/*|/usr/lib/razertux/*|/usr/share/polkit-1/actions/io.github.razertux.*)
        sudo rm -f -- "$f" ;;
      *) warn "skipping unexpected manifest entry: $f" ;;
    esac
  done < "$MANIFEST"
  sudo rmdir /usr/lib/razertux 2>/dev/null || true
  sudo rm -rf -- "$OPT"
  if command -v udevadm >/dev/null; then sudo udevadm control --reload-rules || true; fi
}

# -- Arch ---------------------------------------------------------------------------------
install_arch() {
  command -v makepkg >/dev/null || die "makepkg not found (install base-devel)"
  say "Building and installing the pacman package"
  (cd "$PKG" && makepkg -sif)  # no --needed: a rebuilt package of the same version is installed too
}

# -- user service ---------------------------------------------------------------------------
# RAZERTUX_NO_SERVICE=1 skips the user service (containers and tests without a session).
start_service() {
  [ -z "${RAZERTUX_NO_SERVICE:-}" ] || return 0
  command -v systemctl >/dev/null \
    || die "systemd is required: the service runs as a systemd user service (start razertuxd yourself otherwise)"
  say "Starting the RazerTux service for $(id -un)"
  systemctl --user daemon-reload
  systemctl --user disable --now naga-configd.service >/dev/null 2>&1 || true
  systemctl --user enable razertuxd.service
  systemctl --user restart razertuxd.service \
    || warn "the service did not start yet – log out and back in, then check: systemctl --user status razertuxd"
  if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && command -v razertux-gui >/dev/null; then
    pkill -u "$(id -u)" -f 'razertux-gui --tray' >/dev/null 2>&1 || true  # an older version
    sleep 1
    nohup razertux-gui --tray >/dev/null 2>&1 &
  fi
}

stop_service() {
  [ -z "${RAZERTUX_NO_SERVICE:-}" ] || return 0
  systemctl --user disable --now razertuxd.service >/dev/null 2>&1 || true
  pkill -u "$(id -u)" -f 'razertux-gui' >/dev/null 2>&1 || true
}

offer_restore() {
  command -v razertux >/dev/null || return 0
  [ -f "$HOME/.config/razertux/mouse-backup.json" ] || return 0
  local answer
  read -r -p ":: Restore the mouse's original settings (from the backup) before removing? [Y/n] " answer || answer=n
  case "$answer" in
    n|N|no|No) ;;
    *) razertux restore || warn "restore failed – the mouse keeps its current settings" ;;
  esac
}

# -- main -----------------------------------------------------------------------------------
main() {
  local action=install
  case "${1:-}" in
    ""|--install) ;;
    --uninstall) action=uninstall ;;
    --print-files) generic_files | cut -d'|' -f2; printf '/usr/local/bin/%s\n' "${COMMANDS[@]}"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  [ "$(id -u)" -ne 0 ] || die "please run this as your normal user, not as root (it uses sudo itself)"
  local family
  family="$(distro_family)"

  if [ "$action" = uninstall ]; then
    offer_restore
    stop_service
    if [ "$family" = arch ] && pacman -Q razertux >/dev/null 2>&1; then
      sudo pacman -Rns razertux
    else
      uninstall_generic
    fi
    [ -n "${RAZERTUX_NO_SERVICE:-}" ] || systemctl --user daemon-reload || true
    say "RazerTux is removed. Your profiles and the mouse backup are still in ~/.config/razertux"
    say "(delete that folder to remove them as well)."
    exit 0
  fi

  [ -f "$SRC/pyproject.toml" ] && [ -d "$PKG" ] || die "run this script from the RazerTux source folder"
  if [ -z "${RAZERTUX_NO_SERVICE:-}" ] && ! command -v systemctl >/dev/null; then
    die "systemd is required: the service runs as a systemd user service"
  fi
  warn_openrazer
  if [ "$family" = arch ]; then
    install_arch
  else
    warn "The installer has not been tested on this distribution yet (work in progress)."
    warn "Please report problems or success: https://github.com/anoraknophobia/RazerTux/issues"
    install_generic "$family"
  fi
  start_service
  say "Done. Start RazerTux from the application menu or with: razertux-gui"
  say "The tray icon starts automatically from your next login on."
  say "If the mouse is not found: unplug it and plug it in again (or log out and back in)."
}

main "$@"
