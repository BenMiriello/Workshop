#!/bin/bash
# bird-mic test (durable copy). Usage: bash bt.sh [seconds]
# Captures from the yard mic on the Pi, prints a static-vs-signal verdict using
# SPECTRAL FLATNESS (the metric that actually distinguishes noise from sound),
# and publishes mp3s over the tailnet so the operator can listen.
#
# Verdict scale (bird band 2-8 kHz, min flatness across frames):
#   <0.20  = real structured sound (birds/voice)   -> SIGNAL
#   <0.30  = faint structure, look closer
#   else   = pinned ~0.4-0.5 = STATIC (no acoustic signal reaching the ADC)
# Calibrated baselines on this rig: pure tone 0.004 | white noise 0.85 |
#   dead static (both Pi cards) full-band ~0.74-0.83 pinned, never dips.
set +e
D=${1:-15}
PI=pi@raspberrypi
PIFQDN=pi@raspberrypi.tail38e62e.ts.net
LISTEN=/tmp/yard_listen
TAILIP=100.104.196.85
mkdir -p "$LISTEN"
# ensure the listen server is up (tailnet-only)
ss -tlpn 2>/dev/null | grep -q "$TAILIP:8765" || ( cd "$LISTEN" && nohup python3 -m http.server 8765 --bind "$TAILIP" >/dev/null 2>&1 & )

# capture (stop streamer for exclusive access; restart after)
ssh -o ConnectTimeout=12 "$PI" "sudo systemctl stop birdstream-lean 2>/dev/null; sleep 1; ffmpeg -nostdin -hide_banner -loglevel error -f alsa -channels 1 -sample_rate 48000 -i plughw:1,0 -t $D -y /tmp/bt.wav </dev/null; sudo systemctl start birdstream-lean" >/dev/null 2>&1
scp -o ConnectTimeout=15 "$PIFQDN:/tmp/bt.wav" /tmp/bt.wav >/dev/null 2>&1
[ -s /tmp/bt.wav ] || { echo "CAPTURE FAILED (Pi reachable? card on plughw:1,0?)"; exit 1; }

ffmpeg -nostdin -hide_banner -loglevel error -y -i /tmp/bt.wav -c:a libmp3lame -q:a 5 "$LISTEN/bt.mp3" >/dev/null 2>&1
ffmpeg -nostdin -hide_banner -loglevel error -y -i /tmp/bt.wav -af "volume=20dB" -c:a libmp3lame -q:a 5 "$LISTEN/bt_boosted.mp3" >/dev/null 2>&1

fb(){ ffmpeg -nostdin -hide_banner -i /tmp/bt.wav -af "${1}aspectralstats,ametadata=print" -f null /dev/null 2>/tmp/_m.txt
  grep -oE "flatness=[0-9.]+" /tmp/_m.txt | sed 's/flatness=//' | sort -n | awk '{v[n++]=$1;s+=$1} END{if(n)printf "%.3f/%.3f", s/n, v[0]}'; }
LV=$(ffmpeg -nostdin -hide_banner -i /tmp/bt.wav -af volumedetect -f null /dev/null 2>&1 | grep -oE "max_volume: [-0-9.]+" | grep -oE -- "-?[0-9.]+")
FULL=$(fb ""); BIRD=$(fb "highpass=f=2000,lowpass=f=8000,"); BMIN=${BIRD#*/}
V=$(awk -v b="$BMIN" 'BEGIN{ if(b<0.20)print "*** SIGNAL (structured!) ***"; else if(b<0.30)print "~ faint structure, look closer"; else print "STATIC (no signal)"}')
echo "${D}s  maxdB=$LV  flat full(mean/min)=$FULL  bird2-8k(mean/min)=$BIRD"
echo "VERDICT: $V"
echo "listen: http://$TAILIP:8765/bt.mp3   (+20dB: /bt_boosted.mp3)"
