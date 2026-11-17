#!/usr/bin/env bash
# Operator-run, read-only host triage for the Contabo incident.
# Usage on edge-1: sudo bash /tmp/triage-edge-host.sh [SINCE]
# stdout contains private operational metadata: save it outside git.
# Does not install, restart, change permissions, read private keys, or read tokens.
# Guest-provided evidence cannot rule out a provider/host compromise.
set -u
export LC_ALL=C TZ=UTC
since=${1:-"2026-11-01 00:00:00 UTC"}
if (( EUID != 0 )); then
    printf 'Run this collector yourself with sudo on edge-1.\n' >&2
    exit 1
fi
if [[ $(hostname -s) != k8s-edge1 ]]; then
    printf 'Refusing: expected hostname k8s-edge1.\n' >&2
    exit 1
fi
section() { printf '\n=== %s ===\n' "$1"; }
run() { "$@"; local rc=$?; (( rc == 0 )) || printf '[check exited %s]\n' "$rc"; return 0; }

section 'Time, boot and login accounting (UTC)'
run date --iso-8601=seconds
run uptime
run uname -a
run last -Faiwx -n 100
run lastb -Faiw -n 30
run journalctl --list-boots --utc --no-pager

section "Authentication journal summary since $since"
# Report counts and successful/administrative events, not a flood of failed
# passwords. Suppress sudo command bodies because they may contain credentials.
journalctl --since "$since" --utc --no-pager -o json \
    -t sshd -t sshd-session -t sudo -t login -t su -t useradd -t usermod \
    -t userdel -t passwd -t chpasswd -t groupadd -t groupmod |
python3 -c '
import collections, datetime, json, re, sys
counts=collections.Counter(); failures=collections.Counter(); events=[]
first=last=None
for line in sys.stdin:
    try: item=json.loads(line)
    except ValueError: continue
    msg=item.get("MESSAGE", "")
    if not isinstance(msg,str): continue
    ts=int(item["__REALTIME_TIMESTAMP"])/1e6
    first=ts if first is None else min(first,ts); last=ts if last is None else max(last,ts)
    ident=item.get("SYSLOG_IDENTIFIER", "?")
    counts[ident]+=1
    if re.search(r"Failed |Invalid user|authentication failure",msg,re.I):
        addr=re.search(r"(?:from |rhost=)([0-9a-fA-F:.]+)",msg)
        failures[addr.group(1) if addr else "unknown"]+=1
    if re.search(r"Accepted |session opened|session closed|COMMAND=|new user|user added|password changed|password change|FAILED SU|authentication failure",msg,re.I) and not (ident.startswith("sshd") and "Accepted " not in msg and "session " not in msg):
        msg=re.sub(r"COMMAND=.*", "COMMAND=<redacted>", msg)
        # Escape control characters and preserve a bounded event sample.
        events.append((ts,ident,msg))
        if len(events)>500: events.pop(0)
def stamp(ts):
    return datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).isoformat() if ts is not None else "none"
print("Matching journal entries:",dict(counts))
print("First:",stamp(first),"Last:",stamp(last))
print("Authentication failure entries by source (may count a failed attempt twice):",failures.most_common(30))
print("Last up to 500 successful/session/administrative events:")
for ts,ident,msg in events: print(stamp(ts),ident,json.dumps(msg))
if not counts: print("NO AUTHENTICATION RECORDS: missing evidence, not a clean result")
'
printf 'Authentication pipeline statuses: %s\n' "${PIPESTATUS[*]}"

section 'Accounts and password lock status (no hashes)'
run awk -F: '$3 == 0 || ($3 >= 1000 && $3 < 65534) {print $1 ":" $3 ":" $6 ":" $7}' /etc/passwd
for account in root ubuntu tarjei; do run passwd -S "$account"; done

section 'SSH effective settings for root and operator'
for account in root tarjei; do
    printf 'user=%s\n' "$account"
    /usr/sbin/sshd -T -C "user=$account,host=k8s-edge1,addr=91.230.99.25" |
        awk '$1 ~ /^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|authenticationmethods|authorizedkeysfile|authorizedkeyscommand|allowusers|allowgroups|denyusers|denygroups|allowagentforwarding|permitemptypasswords)$/ {print}'
done
section 'Authorized-key fingerprints and timestamps (no key bodies)'
while IFS= read -r -d '' path; do
    run stat -c '%y | %z | %a | %U:%G | %n' "$path"
    run ssh-keygen -lf "$path"
done < <(find /root /home -xdev -type f -name authorized_keys -print0)

section 'Processes (names only), sockets, routes, WireGuard peer handshakes'
run ps -eo user,pid,ppid,lstart,comm --sort=pid
run ss -lntup
run ss -tnp state established
run ip -brief address
run ip route show table all
run ip -6 route show table all
run wg show all latest-handshakes
run wg show all endpoints
run wg show all allowed-ips
run nft list ruleset

section 'Services, timers and persistence-file metadata'
run systemctl list-units --type=service --all --no-pager
run systemctl list-unit-files --state=enabled --no-pager
run systemctl list-timers --all --no-pager
run find /etc/systemd/system /etc/cron.d /etc/cron.daily /etc/cron.hourly \
    /etc/cron.weekly /etc/cron.monthly /var/spool/cron /etc/ssh /etc/sudoers.d \
    -xdev -type f -printf '%TY-%Tm-%TdT%TH:%TM:%TS | %CY-%Cm-%CdT%CH:%CM:%CS | %m | %u:%g | %p\n'
run stat -c '%y | %z | %a | %U:%G | %n' /etc/passwd /etc/shadow /etc/group \
    /etc/sudoers /etc/crontab /etc/rc.local /etc/ld.so.preload
section 'Files recently modified OR with recent inode changes in persistence paths'
run find /etc /usr/local/bin /usr/local/sbin /root/.ssh /home/tarjei/.ssh \
    -xdev -type f \( -newermt "$since" -o -newerct "$since" \) \
    -printf '%TY-%Tm-%TdT%TH:%TM:%TS | %CY-%Cm-%CdT%CH:%CM:%CS | %m | %u:%g | %p\n'

section 'Package verification (local package database; not independent attestation)'
run dpkg --verify openssh-server openssh-client sudo systemd coreutils procps iproute2
section 'Recent package history'
run tail -n 120 /var/log/apt/history.log
section 'Collector finished'
run date --iso-8601=seconds
