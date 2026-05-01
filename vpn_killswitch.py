#!/usr/bin/env python3
"""
VPN Kill Switch — iptables-based traffic blocker.
Must be run as root (or via pkexec/sudo).

Commands:
  strict-on   — block ALL outgoing traffic except loopback, LAN, DNS, and VPN iface
  strict-off  — restore previous rules or reset to defaults
  force-off   — unconditionally reset to ACCEPT defaults (emergency unlock)
  status      — show detected interfaces
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

IPTABLES = None
IP6TABLES = None
IPTABLES_SAVE = None
IP6TABLES_SAVE = None
IPTABLES_RESTORE = None
IP6TABLES_RESTORE = None
IP_CMD = None
WG_CMD = None

# Root-owned backup directory to avoid symlink attacks under HOME
BACKUP_DIR = Path('/var/tmp') / f'vpn-checker-{os.getuid()}'
BACKUP_FILE = BACKUP_DIR / 'iptables-backup.v4'
BACKUP_FILE6 = BACKUP_DIR / 'iptables-backup.v6'

# Legacy soft mode chain name (cleaned up by force-off)
_LEGACY_SOFT_CHAIN = 'VPNCHECKER_SOFT'


# ---------------------------------------------------------------------------
# Tool resolution (absolute paths only for root safety)
# ---------------------------------------------------------------------------
def _resolve_tool(name):
    """Find absolute path to a system binary — hardcoded first, then PATH."""
    for prefix in ('/usr/sbin', '/sbin', '/usr/bin', '/bin'):
        p = os.path.join(prefix, name)
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def _init_tools():
    global IPTABLES, IP6TABLES
    global IPTABLES_SAVE, IP6TABLES_SAVE
    global IPTABLES_RESTORE, IP6TABLES_RESTORE
    global IP_CMD, WG_CMD

    IPTABLES = _resolve_tool('iptables')
    IP6TABLES = _resolve_tool('ip6tables')
    IPTABLES_SAVE = _resolve_tool('iptables-save')
    IP6TABLES_SAVE = _resolve_tool('ip6tables-save')
    IPTABLES_RESTORE = _resolve_tool('iptables-restore')
    IP6TABLES_RESTORE = _resolve_tool('ip6tables-restore')
    IP_CMD = _resolve_tool('ip')
    WG_CMD = _resolve_tool('wg')

    if IPTABLES is None:
        print("ERROR: iptables not found")
        sys.exit(1)

    if IP6TABLES is None:
        print("WARNING: ip6tables not found. IPv6 traffic will NOT be blocked.")
        print("         Install ip6tables or disable IPv6 to prevent leaks.")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
class _FakeResult:
    stdout = ''
    stderr = ''
    returncode = 1


def _run(cmd_list, check=False):
    """Run a command list safely (no shell)."""
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True)
        if check and result.returncode != 0 and result.stderr.strip():
            print(f'WARN: {" ".join(cmd_list)}\n  {result.stderr.strip()}')
        return result
    except Exception as exc:
        fake = _FakeResult()
        fake.stderr = str(exc)
        return fake


def _ipt(args, ipv6=False, critical=False):
    """Run iptables or ip6tables with given args."""
    tool = IP6TABLES if ipv6 else IPTABLES
    if tool is None:
        return _FakeResult()
    result = _run([tool] + args)
    if critical and result.returncode != 0:
        print(f"FATAL: {' '.join([tool] + args)}")
        print(f"  stderr: {result.stderr.strip()}")
        sys.exit(1)
    return result


def _save(ipv6=False):
    tool = IP6TABLES_SAVE if ipv6 else IPTABLES_SAVE
    if tool is None:
        return None
    result = _run([tool])
    if result.returncode != 0:
        return None
    return result.stdout


def _restore(text, ipv6=False):
    tool = IP6TABLES_RESTORE if ipv6 else IPTABLES_RESTORE
    if tool is None:
        return False
    result = subprocess.run([tool], input=text, text=True, capture_output=True)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def detect_vpn_iface():
    if IP_CMD is None:
        return None
    result = _run([IP_CMD, '-j', 'route', 'get', '1.1.1.1'])
    if result.returncode == 0 and result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            if isinstance(data, list) and data:
                iface = data[0].get('dev', '')
                if iface.startswith(('tun', 'wg', 'ppp', 'singbox')):
                    return iface
        except Exception:
            pass
    result = _run([IP_CMD, 'route', 'get', '1.1.1.1'])
    for line in result.stdout.strip().splitlines():
        if 'dev' in line:
            parts = line.split()
            try:
                iface = parts[parts.index('dev') + 1]
                if iface.startswith(('tun', 'wg', 'ppp', 'singbox')):
                    return iface
            except (ValueError, IndexError):
                pass
    return None


def get_lan_iface():
    if IP_CMD is None:
        return None
    result = _run([IP_CMD, 'route', 'show', 'default'])
    for line in result.stdout.strip().splitlines():
        if line.startswith('default'):
            parts = line.split()
            try:
                return parts[parts.index('dev') + 1]
            except (ValueError, IndexError):
                pass
    return None


def get_vpn_server_ip():
    """Try to find VPN server IP for WireGuard."""
    if WG_CMD is None:
        return None
    result = _run([WG_CMD, 'show', 'endpoints'])
    if result.returncode == 0 and result.stdout.strip():
        first = result.stdout.strip().splitlines()[0]
        parts = first.split()
        if len(parts) >= 3:
            return parts[2].rsplit(':', 1)[0]
    return None


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------
def _ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for p in [BACKUP_DIR] + list(BACKUP_DIR.parents):
        if p.is_symlink():
            print(f"ERROR: Backup path contains symlink: {p}")
            sys.exit(1)


def backup_rules():
    _ensure_backup_dir()
    if BACKUP_FILE.exists() or BACKUP_FILE6.exists():
        print("ERROR: Backup already exists. Run strict-off first, or use force-off to reset.")
        print(f"       Backups: {BACKUP_FILE}, {BACKUP_FILE6}")
        sys.exit(1)

    v4 = _save(ipv6=False)
    v6 = _save(ipv6=True)
    if v4 is None:
        print("ERROR: Failed to save IPv4 rules")
        sys.exit(1)
    if v6 is None:
        print("ERROR: Failed to save IPv6 rules")
        sys.exit(1)
    BACKUP_FILE.write_text(v4, encoding='utf-8')
    BACKUP_FILE6.write_text(v6, encoding='utf-8')
    print(f'IPv4 rules backed up to {BACKUP_FILE}')
    print(f'IPv6 rules backed up to {BACKUP_FILE6}')
    return True


def restore_rules():
    ok = True
    had_v4 = BACKUP_FILE.exists()
    had_v6 = BACKUP_FILE6.exists()
    v4_restored = False
    v6_restored = False

    if had_v4:
        text = BACKUP_FILE.read_text(encoding='utf-8')
        if _restore(text, ipv6=False):
            print('IPv4 rules restored')
            v4_restored = True
        else:
            print('ERROR: failed to restore IPv4 rules')
            ok = False

    if had_v6:
        text = BACKUP_FILE6.read_text(encoding='utf-8')
        if _restore(text, ipv6=True):
            print('IPv6 rules restored')
            v6_restored = True
        else:
            print('ERROR: failed to restore IPv6 rules')
            ok = False

    # Only delete backups after all present backups are restored
    if had_v4 and had_v6:
        if v4_restored and v6_restored:
            BACKUP_FILE.unlink()
            BACKUP_FILE6.unlink()
    elif had_v4 and v4_restored:
        BACKUP_FILE.unlink()
    elif had_v6 and v6_restored:
        BACKUP_FILE6.unlink()

    return ok


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------
def _apply_common(ipv6=False):
    """Rules common to IPv4 and IPv6."""
    _ipt(['-F'], ipv6=ipv6, critical=True)
    _ipt(['-X'], ipv6=ipv6, critical=True)

    _ipt(['-P', 'INPUT', 'DROP'], ipv6=ipv6, critical=True)
    _ipt(['-P', 'FORWARD', 'DROP'], ipv6=ipv6, critical=True)
    _ipt(['-P', 'OUTPUT', 'DROP'], ipv6=ipv6, critical=True)

    _ipt(['-A', 'INPUT', '-i', 'lo', '-j', 'ACCEPT'], ipv6=ipv6, critical=True)
    _ipt(['-A', 'OUTPUT', '-o', 'lo', '-j', 'ACCEPT'], ipv6=ipv6, critical=True)

    _ipt(['-A', 'INPUT', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'], ipv6=ipv6)
    _ipt(['-A', 'OUTPUT', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'], ipv6=ipv6)

    if ipv6:
        for net in ['fc00::/7', 'fe80::/10']:
            _ipt(['-A', 'OUTPUT', '-d', net, '-j', 'ACCEPT'], ipv6=True)
            _ipt(['-A', 'INPUT', '-s', net, '-j', 'ACCEPT'], ipv6=True)
    else:
        for net in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']:
            _ipt(['-A', 'OUTPUT', '-d', net, '-j', 'ACCEPT'], ipv6=ipv6)
            _ipt(['-A', 'INPUT', '-s', net, '-j', 'ACCEPT'], ipv6=ipv6)

    if not ipv6:
        _ipt(['-A', 'OUTPUT', '-p', 'udp', '--dport', '67:68', '--sport', '67:68', '-j', 'ACCEPT'], ipv6=ipv6)
        _ipt(['-A', 'INPUT', '-p', 'udp', '--dport', '67:68', '--sport', '67:68', '-j', 'ACCEPT'], ipv6=ipv6)

    # DNS — allowed globally so the VPN client can resolve its server before tunnel is up.
    _ipt(['-A', 'OUTPUT', '-p', 'udp', '--dport', '53', '-j', 'ACCEPT'], ipv6=ipv6)
    _ipt(['-A', 'OUTPUT', '-p', 'tcp', '--dport', '53', '-j', 'ACCEPT'], ipv6=ipv6)

    _ipt(['-A', 'OUTPUT', '-p', 'udp', '--dport', '123', '-j', 'ACCEPT'], ipv6=ipv6)

    if ipv6:
        for icmpv6_type in ('neighbor-solicitation', 'neighbor-advertisement',
                            'router-solicitation', 'router-advertisement'):
            _ipt(['-A', 'INPUT', '-p', 'icmpv6', '--icmpv6-type', icmpv6_type, '-j', 'ACCEPT'], ipv6=True)
            _ipt(['-A', 'OUTPUT', '-p', 'icmpv6', '--icmpv6-type', icmpv6_type, '-j', 'ACCEPT'], ipv6=True)


def strict_on():
    # IPv6 is mandatory for strict mode — otherwise we have a leak path
    if IP6TABLES is None:
        print("ERROR: ip6tables not found. Strict mode requires ip6tables.")
        print("       Install: sudo apt install ip6tables")
        sys.exit(1)

    vpn_iface = detect_vpn_iface()
    lan_iface = get_lan_iface()
    vpn_server = get_vpn_server_ip()

    print(f'LAN interface: {lan_iface or "unknown"}')
    print(f'VPN interface: {vpn_iface or "NOT DETECTED"}')
    if vpn_server:
        print(f'VPN server IP: {vpn_server}')

    if not vpn_iface and not vpn_server:
        print()
        print('⚠️  WARNING: VPN interface not detected!')
        print('    Kill switch will block ALL internet traffic.')
        print('    Ensure VPN is connected, or you have an emergency way to')
        print(f'    run: sudo {sys.argv[0]} force-off')
        print()

    backup_rules()

    _apply_common(ipv6=False)

    _ipt(['-A', 'OUTPUT', '-d', '224.0.0.0/4', '-j', 'ACCEPT'])
    _ipt(['-A', 'INPUT', '-d', '224.0.0.0/4', '-j', 'ACCEPT'])

    if vpn_server:
        _ipt(['-A', 'OUTPUT', '-d', vpn_server, '-j', 'ACCEPT'])

    if vpn_iface:
        _ipt(['-A', 'OUTPUT', '-o', vpn_iface, '-j', 'ACCEPT'])
        _ipt(['-A', 'INPUT', '-i', vpn_iface, '-j', 'ACCEPT'])

    _apply_common(ipv6=True)
    if vpn_iface:
        _ipt(['-A', 'OUTPUT', '-o', vpn_iface, '-j', 'ACCEPT'], ipv6=True)
        _ipt(['-A', 'INPUT', '-i', vpn_iface, '-j', 'ACCEPT'], ipv6=True)

    print()
    print('🔒 Strict kill switch ENABLED')
    print('   Allowed: loopback, LAN, DNS, NTP, VPN interface, VPN server IP')
    print('   Blocked: everything else')
    print('   NOTE: DNS is allowed globally (needed to resolve VPN server before tunnel)')


def strict_off():
    if restore_rules():
        print()
        print('🔓 Strict kill switch DISABLED — previous rules restored')
    else:
        force_off()


def force_off():
    """Emergency reset — unconditionally ACCEPT all."""
    for ipv6 in (False, True):
        if ipv6 and IP6TABLES is None:
            continue
        _ipt(['-P', 'INPUT', 'ACCEPT'], ipv6=ipv6, critical=True)
        _ipt(['-P', 'FORWARD', 'ACCEPT'], ipv6=ipv6, critical=True)
        _ipt(['-P', 'OUTPUT', 'ACCEPT'], ipv6=ipv6, critical=True)
        _ipt(['-F'], ipv6=ipv6, critical=True)
        _ipt(['-X'], ipv6=ipv6, critical=True)

    # Cleanup legacy soft-mode chain if present from older versions
    for ipv6 in (False, True):
        if ipv6 and IP6TABLES is None:
            continue
        while True:
            result = _ipt(['-D', 'OUTPUT', '-j', _LEGACY_SOFT_CHAIN], ipv6=ipv6)
            if result.returncode != 0:
                break
        _ipt(['-F', _LEGACY_SOFT_CHAIN], ipv6=ipv6)
        _ipt(['-X', _LEGACY_SOFT_CHAIN], ipv6=ipv6)

    for f in (BACKUP_FILE, BACKUP_FILE6):
        if f.exists():
            f.unlink()
    print()
    print('🔓 EMERGENCY UNLOCK — all firewall rules cleared, defaults set to ACCEPT')


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def show_status():
    vpn = detect_vpn_iface()
    lan = get_lan_iface()
    print(f'Default LAN interface: {lan or "unknown"}')
    print(f'VPN interface detected: {vpn or "none"}')

    for name, tool in (('IPv4', IPTABLES), ('IPv6', IP6TABLES)):
        if tool is None:
            continue
        result = _run([tool, '-L', '-n'])
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if lines:
                print(f'\n{name} policies:')
                for line in lines[:3]:
                    if line.startswith('Chain'):
                        print(f'  {line}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if os.geteuid() != 0:
        print('This script must be run as root (use pkexec or sudo)')
        sys.exit(1)

    _init_tools()

    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <strict-on|strict-off|force-off|status>')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'strict-on':
        strict_on()
    elif cmd == 'strict-off':
        strict_off()
    elif cmd == 'force-off':
        force_off()
    elif cmd == 'status':
        show_status()
    else:
        print(f'Unknown command: {cmd}')
        sys.exit(1)


if __name__ == '__main__':
    main()
