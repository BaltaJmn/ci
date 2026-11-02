#!/bin/zsh
# tren-mac.sh tick | ya | seco <clave>: the weekly release train on this Mac (PUBLICAR.md, section 7). launchd runs `tick`
# every 30 min and on Monday at 7:30 (com.baltajmn.tren.plist, next to this file); `ya` starts the week's version now
# (release-train); `seco` is one app's dry Monday, by hand. It runs from its own clean clone of ci (~/.tren/ci, brought to origin/main first), never from a working copy;
# tren.py keeps every app in its own clean clone in ~/.tren. Log: ~/Library/Logs/tren.log.
set -eu
export PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
T=$HOME/.tren
[[ -d $T/ci ]] || git clone -q https://github.com/BaltaJmn/ci.git $T/ci
git -C $T/ci fetch -q origin && git -C $T/ci reset -q --hard origin/main
secret() { security find-generic-password -a "$USER" -s "dev.baltajmn.$1" -w; }
export NTFY_TOPIC=$(secret ntfy-topic) NTFY_TOPIC_APROBAR=$(secret ntfy-topic-aprobar)
# One train at a time: a tick that finds another one running leaves (the next one comes in 30 min); seco waits for it.
# caffeinate: the Mac does not sleep in the middle of a build.
case ${1:-} in
  tick) exec lockf -t 0 $T/tren.lock caffeinate -i /usr/bin/python3 $T/ci/tienda/tren.py tick ;;
  ya) exec lockf -t 7200 $T/tren.lock caffeinate -i /usr/bin/python3 $T/ci/tienda/tren.py ya ;;
  seco) exec lockf -t 7200 $T/tren.lock caffeinate -i env SECO=true /usr/bin/python3 $T/ci/tienda/tren.py seco ${2:?clave} ;;
  *) sed -n '2,5p' $0; exit 1 ;;
esac
