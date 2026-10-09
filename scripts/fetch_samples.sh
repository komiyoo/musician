#!/usr/bin/env bash
# 下载采样包到 ./samples （不提交到 git）。Download the free sample packs into ./samples.
# Usage: scripts/fetch_samples.sh [all|drums|strings|piano]
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${CTM_SAMPLES_DIR:-samples}"
mkdir -p "$DEST"
what="${1:-all}"

if [[ $what == all || $what == drums ]]; then
  # Sam's Sonor — CC-BY-SA-4.0, ~60 MB
  [[ -d "$DEST/SamsSonor" ]] || git clone --depth 1 https://github.com/sfzinstruments/SamsSonor.git "$DEST/SamsSonor"
fi

if [[ $what == all || $what == strings ]]; then
  # VSCO-2 Community Edition — CC0. Only the sustain-vibrato string folders are needed.
  if [[ ! -d "$DEST/VSCO-2-CE" ]]; then
    git clone --depth 1 --filter=blob:none --sparse https://github.com/sgossner/VSCO-2-CE.git "$DEST/VSCO-2-CE"
    git -C "$DEST/VSCO-2-CE" sparse-checkout set \
      "Strings/Violin Section/susVib" "Strings/Viola Section/susvib" "Strings/Cello Section/susvib"
  fi
fi

if [[ $what == all || $what == piano ]]; then
  # Salamander Grand Piano V3 (SFZ + FLAC) — CC-BY-3.0, ~1 GB unpacked
  URL="https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/SalamanderGrandPiano-SFZ+FLAC-V3+20200602.tar.gz"
  if ! ls -d "$DEST"/SalamanderGrandPiano* >/dev/null 2>&1; then
    curl -L "$URL" -o "$DEST/salamander.tar.gz"
    tar -xzf "$DEST/salamander.tar.gz" -C "$DEST" && rm "$DEST/salamander.tar.gz"
  fi
fi
echo "samples ready in $DEST"
ls "$DEST"
