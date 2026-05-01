#!/usr/bin/env python3
"""
VPN Checker — system tray indicator showing VPN status with country flag.
Works with any VPN (WireGuard, OpenVPN, sing-box, etc.) on any Linux desktop.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

# Try AppIndicator implementations
INDICATOR = None
for indicator_name in ('AppIndicator3', 'AyatanaAppIndicator3'):
    try:
        gi.require_version(indicator_name, '0.1')
        INDICATOR = getattr(__import__('gi.repository', fromlist=[indicator_name]), indicator_name)
        break
    except (ValueError, ImportError):
        continue

if INDICATOR is None:
    print(
        "ERROR: AppIndicator3 not found.\n"
        "Install: sudo apt install gir1.2-appindicator3-0.1 gir1.2-ayatanaappindicator3-0.1"
    )
    sys.exit(1)

# Optional: notifications via gi
HAVE_NOTIFY = False
try:
    gi.require_version('Notify', '0.7')
    from gi.repository import Notify
    HAVE_NOTIFY = True
except (ValueError, ImportError):
    pass

# Config paths
CONFIG_DIR = Path.home() / '.config' / 'vpn-checker'
CONFIG_FILE = CONFIG_DIR / 'config.json'
CACHE_DIR = Path.home() / '.cache' / 'vpn-checker'
LOG_FILE = CACHE_DIR / 'vpn-checker.log'

# Check intervals
IFACE_CHECK_INTERVAL = 1    # seconds — fast local check
IP_CHECK_INTERVAL = 60      # seconds — slow API check

DEFAULT_CONFIG = {
    'expected_country': '',
    'expected_iface_prefix': '',
    'home_country': '',
    'strict_mode': False,
    'notifications': True,
}

# Fallback IP APIs
IP_APIS = [
    'https://ipapi.co/json/',
    'https://ipinfo.io/json',
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class _Logger:
    _lock = threading.Lock()
    MAX_BYTES = 500 * 1024

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self):
        if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
            backup = self.path.with_suffix('.log.old')
            try:
                if backup.exists():
                    backup.unlink()
                self.path.rename(backup)
            except Exception:
                pass

    def log(self, msg):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f'[{ts}] {msg}\n'
        with self._lock:
            try:
                self._rotate()
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(line)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def country_to_flag(code):
    if not code or len(code) != 2:
        return '🏳️'
    c1, c2 = code[0].upper(), code[1].upper()
    return chr(ord(c1) + 127397) + chr(ord(c2) + 127397)


class _FakeResult:
    stdout = ''
    stderr = ''
    returncode = 1


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        fake = _FakeResult()
        fake.stderr = str(exc)
        return fake


def get_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding='utf-8') as f:
                cfg = json.load(f)
            # Remove legacy soft_mode key from older configs
            cfg.pop('soft_mode', None)
            return {**DEFAULT_CONFIG, **cfg}
        except Exception as exc:
            print(f'[vpn-checker] Config read error: {exc}')
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix='.json.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as exc:
        print(f'[vpn-checker] Config write error: {exc}')
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def get_default_iface():
    """Return interface carrying the default IPv4 route."""
    result = _run(['ip', '-j', 'route', 'get', '1.1.1.1'])
    if result.returncode == 0 and result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            if isinstance(data, list) and data:
                return data[0].get('dev')
        except json.JSONDecodeError:
            pass
    # Fallback: plain text parsing
    result = _run(['ip', 'route', 'get', '1.1.1.1'])
    for line in result.stdout.strip().splitlines():
        if 'dev' in line:
            parts = line.split()
            try:
                return parts[parts.index('dev') + 1]
            except (ValueError, IndexError):
                pass
    return None


def get_public_ip_info():
    """Fetch IP info from one of the fallback APIs."""
    last_err = None
    for url in IP_APIS:
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'vpn-checker/1.0'},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            # ipapi.co format
            if 'ipapi' in url:
                if data.get('error'):
                    last_err = data['error']
                    continue
                return {
                    'ip': data.get('ip', '?'),
                    'country': data.get('country_code', '?'),
                    'country_name': data.get('country_name', '?'),
                    'city': data.get('city', '?'),
                    'org': data.get('org', '?'),
                    'error': None,
                }

            # ipinfo.io format
            if 'ipinfo' in url:
                country = data.get('country', '?')
                return {
                    'ip': data.get('ip', '?'),
                    'country': country,
                    'country_name': country,
                    'city': data.get('city', '?'),
                    'org': data.get('org', data.get('asn', {}).get('name', '?')),
                    'error': None,
                }
        except Exception as exc:
            last_err = str(exc)
            continue

    return {'error': last_err or 'all APIs unreachable'}


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class VPNChecker:
    def __init__(self):
        self.config = get_config()
        self.last_status = None
        self.last_info = None
        self._last_iface = None
        self._last_known_country = ''
        self._strict_active = False
        self.indicator = None
        self.menu_items = {}
        self._check_lock = threading.Lock()
        self._check_in_progress = False
        self._shutting_down = False
        self._toggling = False
        self._logger = _Logger(LOG_FILE)

        if HAVE_NOTIFY:
            try:
                Notify.init('vpn-checker')
            except Exception:
                pass

        self._build_ui()
        self._setup_signals()
        self._start_check_loop()

    # -- UI construction --------------------------------------------------
    def _build_ui(self):
        self.indicator = INDICATOR.Indicator.new(
            'vpn-checker',
            'network-vpn-disconnected',
            INDICATOR.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_status(INDICATOR.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()

        self._add_label('status', 'Статус: проверка...', sensitive=False)
        self._add_label('ip', 'IP: -', sensitive=False)
        self._add_label('loc', 'Локация: -', sensitive=False)
        self._add_label('iface', 'Интерфейс: -', sensitive=False)
        self._add_label('org', 'Провайдер: -', sensitive=False)
        self.menu.append(Gtk.SeparatorMenuItem())

        self.strict_item = Gtk.CheckMenuItem(label='🔒 Strict mode (kill switch)')
        self.strict_item.set_active(self.config.get('strict_mode', False))
        self.strict_item.connect('toggled', self._on_strict_toggled)
        self.menu.append(self.strict_item)

        self.notif_item = Gtk.CheckMenuItem(label='🔔 Уведомления')
        self.notif_item.set_active(self.config.get('notifications', True))
        self.notif_item.connect('toggled', self._on_notif_toggled)
        self.menu.append(self.notif_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        refresh = Gtk.MenuItem(label='🔄 Обновить сейчас')
        refresh.connect('activate', lambda *_: self._check_now(self._check_ip))
        self.menu.append(refresh)

        settings = Gtk.MenuItem(label='⚙️ Открыть конфиг')
        settings.connect('activate', self._open_config)
        self.menu.append(settings)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label='❌ Выход')
        quit_item.connect('activate', self._quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def _add_label(self, key, label, sensitive=False):
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(sensitive)
        self.menu.append(item)
        self.menu_items[key] = item

    # -- Signal handling --------------------------------------------------
    def _setup_signals(self):
        def _handler(*_):
            GLib.idle_add(self._quit)
            return True  # Do not raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    # -- VPN detection logic ----------------------------------------------
    @staticmethod
    def _iface_looks_like_vpn(iface):
        if not iface:
            return False
        return iface.startswith(('tun', 'wg', 'ppp', 'singbox'))

    def _check_iface_status(self, iface, config_snapshot):
        """Fast local status check based on interface only.
        Returns True/False/None. None means 'cannot determine from iface alone'."""
        prefix = config_snapshot.get('expected_iface_prefix', '').strip().lower()
        if prefix:
            matched = iface and iface.lower().startswith(prefix)
            self._logger.log(
                f'[iface] iface={iface} prefix={prefix} → status={matched} (reason=prefix_match)'
            )
            return matched
        if self._iface_looks_like_vpn(iface):
            self._logger.log(
                f'[iface] iface={iface} → status=True (reason=heuristic)'
            )
            return True
        home_country = config_snapshot.get('home_country', '').strip().upper()
        if home_country and self._last_known_country == home_country:
            self._logger.log(
                f'[iface] iface={iface} last_country={self._last_known_country} '
                f'home={home_country} → status=False (reason=known_home_country)'
            )
            return False
        self._logger.log(
            f'[iface] iface={iface} → status=None (reason=no_prefix,heuristic=no_match)'
        )
        return None

    def _check_ip_status(self, info, iface, config_snapshot):
        """Status check based on IP geolocation and iface heuristic.
        Returns True/False/None. None means 'cannot determine from IP alone'."""
        # 1. Local iface heuristic — most reliable, check first
        if self._iface_looks_like_vpn(iface):
            self._logger.log(
                f'[ip] iface={iface} → status=True (reason=iface_heuristic)'
            )
            return True

        expected_country = config_snapshot.get('expected_country', '').strip().upper()
        home_country = config_snapshot.get('home_country', '').strip().upper()
        country = info.get('country', '?') if info else '?'

        # 2. Home country mismatch (priority over expected_country)
        if home_country and country and country != '?':
            matched = country != home_country
            self._logger.log(
                f'[ip] country={country} home={home_country} '
                f'→ status={matched} (reason=home_country)'
            )
            return matched

        # 3. Expected country match (only if home_country not set)
        if expected_country and country and country != '?':
            matched = country == expected_country
            self._logger.log(
                f'[ip] country={country} expected={expected_country} '
                f'→ status={matched} (reason=expected_country)'
            )
            return matched

        if not expected_country and not home_country:
            self._logger.log(
                f'[ip] country={country} → status=None (reason=no_heuristics)'
            )
            return None

        self._logger.log(
            f'[ip] country={country} → status=False (reason=fallback)'
        )
        return False

    # -- UI update (must run on main thread) ------------------------------
    def _update_ui(self, status, info, iface):
        if self._shutting_down:
            return False  # remove from idle queue

        prev_status = self.last_status

        # Store partial updates — do not overwrite with None
        if status is not None:
            self.last_status = status
        if info is not None:
            self.last_info = info
        if iface is not None:
            self._last_iface = iface

        # Use stored state for rendering
        status = self.last_status
        info = self.last_info or {}
        iface = self._last_iface

        flag = country_to_flag(info.get('country')) if not info.get('error') else '🏳️'

        if status is True:
            label_text = f'{flag} VPN'
            tooltip = f'VPN включен — {flag} {info.get("country_name", info.get("country", "?"))}'
            icon_name = 'network-vpn'
            status_text = f'{flag} ВКЛЮЧЕН'
        elif status is False:
            label_text = f'{flag} 🔴'
            tooltip = f'VPN выключен — {flag} {info.get("country_name", info.get("country", "?"))}'
            icon_name = 'network-vpn-disconnected'
            status_text = '🔴 ВЫКЛЮЧЕН'
        else:
            label_text = f'{flag} ?'
            tooltip = f'Статус VPN не определен — {flag} {info.get("country_name", info.get("country", "?"))}'
            icon_name = 'dialog-question'
            status_text = '⚪ НЕИЗВЕСТЕН'

        if not info.get('error'):
            tooltip += f'\nIP: {info.get("ip", "?")}\nПровайдер: {info.get("org", "?")}'

        self.indicator.set_label(label_text, '')
        self.indicator.set_title('VPN Checker')
        try:
            self.indicator.set_icon_full(icon_name, tooltip)
        except AttributeError:
            pass

        self.menu_items['status'].set_label(f'Статус: {status_text}')
        self.menu_items['ip'].set_label(
            f'IP: {info.get("ip", "-") if not info.get("error") else "-"}'
        )

        loc = '-'
        if not info.get('error'):
            parts = [p for p in (info.get('city'), info.get('country_name')) if p]
            loc = ', '.join(parts) if parts else '-'
        self.menu_items['loc'].set_label(f'Локация: {flag} {loc}')
        self.menu_items['iface'].set_label(f'Интерфейс: {iface or "-"}')
        self.menu_items['org'].set_label(
            f'Провайдер: {info.get("org", "-") if not info.get("error") else "-"}'
        )

        # Notifications
        if self.config.get('notifications', True):
            if prev_status is None:
                self._logger.log(f'[notify] skip: first run')
            elif status == prev_status:
                self._logger.log(f'[notify] skip: unchanged {status}')
            else:
                self._logger.log(
                    f'[notify] prev={prev_status} → new={status} '
                    f'country={info.get("country","?")} iface={iface}'
                )
                self._notify(status, info)

        # Auto-strict mode
        self._sync_strict_mode(self.last_status)

        return False  # single-shot idle callback

    # -- Notifications ----------------------------------------------------
    def _notify(self, status, info):
        if not HAVE_NOTIFY:
            return
        try:
            if status is True:
                flag = country_to_flag(info.get('country')) if info else ''
                n = Notify.Notification.new(
                    'VPN подключен',
                    f'{flag} {info.get("country_name", "")}\nIP: {info.get("ip", "")}',
                    'network-vpn',
                )
            else:
                n = Notify.Notification.new(
                    '⚠️ VPN ОТКЛЮЧЕН',
                    'Трафик идет напрямую! Подключи VPN.',
                    'dialog-warning',
                )
            n.set_timeout(8000)
            n.show()
        except Exception:
            pass

    # -- Kill switch helpers ----------------------------------------------
    def _run_killswitch(self, action):
        """Run vpn_killswitch.py via pkexec non-blocking."""
        script = Path(__file__).with_name('vpn_killswitch.py').resolve()
        self._logger.log(f'[killswitch] action={action}')
        try:
            subprocess.Popen(
                ['pkexec', sys.executable, str(script), action],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._logger.log('[killswitch] pkexec not found')
            print('[vpn-checker] pkexec not found. Install policykit-1.')
        except Exception as exc:
            self._logger.log(f'[killswitch] error: {exc}')
            print(f'[vpn-checker] Failed to run killswitch: {exc}')

    def _sync_strict_mode(self, vpn_status):
        """Auto-enable/disable strict kill switch based on VPN status."""
        if not self.config.get('strict_mode'):
            return
        if vpn_status is False and not self._strict_active:
            self._logger.log('[strict] VPN OFF → enabling kill switch')
            self._run_killswitch('strict-on')
            self._strict_active = True
        elif vpn_status is True and self._strict_active:
            self._logger.log('[strict] VPN ON → disabling kill switch')
            self._run_killswitch('strict-off')
            self._strict_active = False

    # -- Background checks ------------------------------------------------
    def _check_iface(self):
        """Fast check — runs in main thread (no network calls)."""
        if self._shutting_down:
            return
        config_snapshot = self.config.copy()
        prev_iface = self._last_iface
        iface = get_default_iface()
        prev_vpn = self._iface_looks_like_vpn(prev_iface) if prev_iface else False
        now_vpn = self._iface_looks_like_vpn(iface)

        # Optimistic transition: iface changed between VPN-like and non-VPN
        if prev_iface is not None and prev_vpn != now_vpn:
            if now_vpn:
                self._logger.log(
                    f'[iface] transition {prev_iface}→{iface} → assuming ON'
                )
                self._update_ui(True, None, iface)
            else:
                self._logger.log(
                    f'[iface] transition {prev_iface}→{iface} → assuming OFF'
                )
                self._update_ui(False, None, iface)
            self._check_now(self._check_ip)
            return

        status = self._check_iface_status(iface, config_snapshot)
        self._update_ui(status, None, iface)

    def _check_ip(self):
        """Slow check — runs in worker thread (network API call)."""
        with self._check_lock:
            if self._shutting_down:
                return
            config_snapshot = self.config.copy()
        info = get_public_ip_info()
        # Get iface AFTER the network call so it matches the moment we got the IP
        iface = get_default_iface()
        if info.get('error'):
            self._logger.log(f'[ip] API error: {info["error"]}')
            status = None
        else:
            country = info.get('country', '')
            if country and country != '?':
                self._last_known_country = country.upper()
            status = self._check_ip_status(info, iface, config_snapshot)
        if not self._shutting_down:
            GLib.idle_add(self._update_ui, status, info, None)

    def _check_now(self, target_fn):
        with self._check_lock:
            if self._check_in_progress:
                return
            self._check_in_progress = True

        def _run_check():
            try:
                target_fn()
            finally:
                with self._check_lock:
                    self._check_in_progress = False

        t = threading.Thread(target=_run_check, daemon=True)
        t.start()

    def _start_check_loop(self):
        self._check_iface()
        self._check_now(self._check_ip)
        GLib.timeout_add_seconds(IFACE_CHECK_INTERVAL, self._iface_loop_cb)
        GLib.timeout_add_seconds(IP_CHECK_INTERVAL, self._ip_loop_cb)

    def _iface_loop_cb(self):
        if self._shutting_down:
            return False
        self._check_iface()
        return True

    def _ip_loop_cb(self):
        if self._shutting_down:
            return False
        self._check_now(self._check_ip)
        return True

    # -- Kill switch integration ------------------------------------------
    def _on_strict_toggled(self, widget):
        if self._toggling:
            return
        self._toggling = True
        try:
            active = widget.get_active()
            self.config['strict_mode'] = active
            save_config(self.config)
            if active:
                self._logger.log('[strict] toggle ON')
                # Trigger immediate sync based on current VPN status
                self._sync_strict_mode(self.last_status)
            else:
                self._logger.log('[strict] toggle OFF')
                if self._strict_active:
                    self._run_killswitch('strict-off')
                    self._strict_active = False
        finally:
            self._toggling = False

    def _on_notif_toggled(self, widget):
        self.config['notifications'] = widget.get_active()
        save_config(self.config)

    def _apply_killswitch(self):
        """Deprecated — kept for compatibility; use _run_killswitch."""
        if self.config.get('strict_mode'):
            self._run_killswitch('strict-on')
        else:
            self._run_killswitch('strict-off')

    # -- Settings ---------------------------------------------------------
    def _open_config(self, *_):
        editors = [
            'gnome-text-editor', 'gedit', 'kate', 'mousepad',
            'leafpad', 'xed', 'pluma', 'nano',
        ]
        for editor in editors:
            resolved = shutil.which(editor)
            if resolved:
                subprocess.Popen([resolved, str(CONFIG_FILE)])
                return
        print(f'[vpn-checker] Config file: {CONFIG_FILE}')

    # -- Quit / cleanup ---------------------------------------------------
    def _quit(self, *_):
        if self._shutting_down:
            return
        self._shutting_down = True

        if self.config.get('strict_mode') and self._strict_active:
            print('[vpn-checker] Disabling strict mode on exit...')
            self._run_killswitch('strict-off')

        Gtk.main_quit()

    def run(self):
        Gtk.main()


def main():
    VPNChecker().run()


if __name__ == '__main__':
    main()
