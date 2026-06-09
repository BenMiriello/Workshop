#!/usr/bin/env bash
# Fetch the bird-audio test set into this directory.
#
# RUN THIS FROM A MACHINE WITH OUTBOUND NETWORK. The workshop box blocks
# outbound shell traffic (egress-guard), so it cannot pull these itself — run it
# on your laptop, or copy the files over after. All sources are CC-licensed; see
# manifest.json / README.md for per-clip license + attribution.
set -euo pipefail
cd "$(dirname "$0")"

UA="Mozilla/5.0 (workshop-bird-test)"
get() { # url  outfile
  if [[ -s "$2" ]]; then echo "  have   $2"; return; fi
  echo "  fetch  $2"
  curl -fSL --retry 3 --retry-delay 2 -A "$UA" "$1" -o "$2"
}

echo "Single birds:"
get "https://static.inaturalist.org/sounds/850281.wav?1700857672"  single_oak-titmouse.wav
get "https://static.inaturalist.org/sounds/341257.wav?1640749143"  single_nuttalls-woodpecker.wav

echo "Repeated-call birds (last-chirp / dense cases):"
get "https://static.inaturalist.org/sounds/235159.m4a?1622086128"  repeated_california-towhee.m4a
get "https://static.inaturalist.org/sounds/393288.m4a?1649964999"  repeated_dark-eyed-junco.m4a

echo "Multi-species soundscapes (simultaneous-bird + stress):"
get "https://raw.githubusercontent.com/birdnet-team/BirdNET-Analyzer/main/birdnet_analyzer/example/soundscape.wav"  group_birdnet-sapsucker-woods.wav
get "https://upload.wikimedia.org/wikipedia/commons/9/9a/Dawn_Chorus_2020-05-06_0500.mp3"  group_dawn-chorus-temperate.mp3
get "https://upload.wikimedia.org/wikipedia/commons/e/eb/RainforestDawnChorus_Selaliparai_DM.ogg"  group_rainforest-dawn-chorus.ogg

echo "Done. $(ls -1 *.wav *.mp3 *.m4a *.ogg 2>/dev/null | wc -l) files present."
