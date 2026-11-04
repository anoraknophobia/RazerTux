#!/usr/bin/env bash
# Publish the current release to the AUR (package "razertux").
#
#   tools/aur_publish.sh            update the AUR clone, build-test, commit and push
#   tools/aur_publish.sh --dry-run  everything except the push
#
# Run it after the GitHub release vX.Y.Z exists. The AUR clone lives in $AUR_DIR
# (default ../razertux-aur) and is created on first use; pushing needs your SSH key in
# your AUR account.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUR_DIR="${AUR_DIR:-$ROOT/../razertux-aur}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

ver="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/razertux/__init__.py")"
[ -n "$ver" ] || { echo "version not found" >&2; exit 1; }
curl -fsIL -o /dev/null "https://github.com/anoraknophobia/RazerTux/archive/refs/tags/v$ver.tar.gz" \
  || { echo "GitHub release v$ver not found - release first" >&2; exit 1; }

if [ ! -d "$AUR_DIR/.git" ]; then
  git clone ssh://aur@aur.archlinux.org/razertux.git "$AUR_DIR"
fi
git -C "$AUR_DIR" pull -q --ff-only 2>/dev/null || true

cp "$ROOT/packaging/aur/PKGBUILD" "$ROOT/packaging/razertux.install" "$AUR_DIR/"
cd "$AUR_DIR"
sed -i "s/^pkgver=.*/pkgver=$ver/; s/^pkgrel=.*/pkgrel=1/" PKGBUILD
updpkgsums
makepkg --printsrcinfo > .SRCINFO
makepkg -f --cleanbuild >/dev/null  # build test
rm -rf src pkg ./*.pkg.tar.zst ./*.tar.gz

git add PKGBUILD .SRCINFO razertux.install
if git diff --cached --quiet; then
  echo "AUR already at $ver"; exit 0
fi
git commit -q -m "Update to $ver"
if [ "$DRY" = 1 ]; then
  echo "dry run: committed $ver in $AUR_DIR, not pushed"
else
  git push -q origin HEAD:master
  echo "AUR updated to $ver: https://aur.archlinux.org/packages/razertux"
fi
