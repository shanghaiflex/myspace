#!/bin/sh
# Отдаёт лог launchd-агента самому launchd: sh deploy/claim-log.sh logs/week-review.log [...]
# ~/movies на регистронезависимом диске — это ~/Movies, папка под TCC. Свой StandardOutPath xpcproxy
# открывает, только если в xattr com.apple.macl файла записан он сам — то есть если файл создал launchd.
# Лог, который первым создал кто-то другой (ssh: `sh scripts/x.sh >> logs/x.log`), несёт macl того, кто
# создал (или никакого), xpcproxy получает `deny file-read-data`, и агент падает до старта скрипта с
# `last exit code = 78: EX_CONFIG`, не написав в лог ни строки. Так итоги недели молчали с 20.09 по 18.10.2026.
# Скрипт узнаёт «подпись» launchd по пробному файлу, который создаёт одноразовый агент, и пересоздаёт руками
# launchd каждый лог без этой подписи, дописывая в него прежнее содержимое. Лог с подписью не трогает.
set -e
U=$(id -u); AG="$HOME/Library/LaunchAgents"; LABEL=cc.bodywithoutorgans.claimlog

ids() {  # UUID-ы из com.apple.macl: записи по 18 байт, 2 байта заголовка и 16 байт UUID
  xattr -px com.apple.macl "$1" 2>/dev/null | tr -d ' \n' | fold -w36 | grep -v '^0*$' | cut -c5-36 || true
}

by_launchd() {  # создать файл $1 руками launchd: одноразовый агент с /usr/bin/true пишет в него stdout
  cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/true</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$1</string><key>StandardErrorPath</key><string>$1</string>
</dict></plist>
PL
  launchctl bootout "gui/$U/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$U" "$AG/$LABEL.plist"
  i=0; while [ ! -f "$1" ] && [ $i -lt 50 ]; do /bin/sleep 0.1; i=$((i+1)); done
  launchctl bootout "gui/$U/$LABEL" 2>/dev/null || true
  rm -f "$AG/$LABEL.plist"
  [ -f "$1" ]
}

for LOG in "$@"; do
  case "$LOG" in /*) ;; *) LOG="$(pwd)/$LOG" ;; esac
  DIR=$(dirname "$LOG"); mkdir -p "$DIR"
  PROBE="$DIR/.launchd-probe.$$"
  by_launchd "$PROBE" || { echo "FAILED: launchd could not create $PROBE" >&2; exit 1; }
  REF=$(ids "$PROBE"); rm -f "$PROBE"
  if [ -f "$LOG" ] && [ -n "$REF" ] && ids "$LOG" | grep -qxF "$REF"; then continue; fi
  OLD=""; if [ -f "$LOG" ]; then OLD="$LOG.claim.$$"; mv "$LOG" "$OLD"; fi
  by_launchd "$LOG" || { [ -n "$OLD" ] && mv "$OLD" "$LOG"; echo "FAILED to claim $LOG for launchd" >&2; exit 1; }
  if [ -n "$OLD" ]; then cat "$OLD" >> "$LOG"; rm -f "$OLD"; fi
  echo "claimed for launchd: $LOG"
done
