#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/vpn-checker"
CACHE_DIR="$HOME/.cache/vpn-checker"
AUTOSTART_DIR="$HOME/.config/autostart"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

# Parse arguments
FORCE_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        --interactive|-i)
            FORCE_INTERACTIVE=true
            ;;
        --help|-h)
            echo "Usage: $0 [--interactive]"
            echo "  --interactive  Ask all config questions even on update"
            exit 0
            ;;
    esac
done

# Detect if this is a fresh install or an update
IS_UPDATE=false
if [ -f "$INSTALL_DIR/vpn-checker" ] || [ -f "$CONFIG_DIR/config.json" ]; then
    IS_UPDATE=true
fi

if [ "$IS_UPDATE" = true ]; then
    echo "=========================================="
    echo "  VPN Checker Updater"
    echo "=========================================="
else
    echo "=========================================="
    echo "  VPN Checker Installer"
    echo "=========================================="
fi

# Do not run under sudo — we install into user directories
if [ "$EUID" -eq 0 ] && [ -n "$SUDO_USER" ]; then
    echo "ERROR: Do not run this installer with sudo."
    echo "       It installs into your user directories (\$HOME/.local/bin, etc.)."
    echo "       Run without sudo: ./install.sh"
    exit 1
fi

# Warn if running as plain root (not recommended)
if [ "$EUID" -eq 0 ] && [ -z "$SUDO_USER" ]; then
    echo "WARNING: Running as root. This will install into /root/.local/..."
    echo "         which is probably not what you want."
    read -rp "Continue anyway? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "[1/5] Installing dependencies..."

install_apt() {
    local pkgs=(
        python3-gi
        python3-gi-cairo
        gir1.2-gtk-3.0
        gir1.2-appindicator3-0.1
        gir1.2-ayatanaappindicator3-0.1
        gir1.2-notify-0.7
        iptables
        libnotify-bin
        policykit-1
    )
    local to_install=()
    for pkg in "${pkgs[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null; then
            to_install+=("$pkg")
        fi
    done
    if [ ${#to_install[@]} -gt 0 ]; then
        echo "Installing: ${to_install[*]}"
        sudo apt-get update -qq
        for pkg in "${to_install[@]}"; do
            sudo apt-get install -y -qq "$pkg" || echo "WARNING: Could not install $pkg"
        done
    else
        echo "All apt dependencies already installed."
    fi
}

install_dnf() {
    local pkgs=(
        python3-gobject
        gtk3
        libappindicator-gtk3
        libnotify
        iptables
        polkit
    )
    local to_install=()
    for pkg in "${pkgs[@]}"; do
        if ! rpm -q "$pkg" &>/dev/null; then
            to_install+=("$pkg")
        fi
    done
    if [ ${#to_install[@]} -gt 0 ]; then
        echo "Installing: ${to_install[*]}"
        sudo dnf install -y "${to_install[@]}" || true
    else
        echo "All dnf dependencies already installed."
    fi
}

install_pacman() {
    local pkgs=(
        python-gobject
        gtk3
        libappindicator-gtk3
        libnotify
        iptables
        polkit
    )
    local to_install=()
    for pkg in "${pkgs[@]}"; do
        if ! pacman -Q "$pkg" &>/dev/null; then
            to_install+=("$pkg")
        fi
    done
    if [ ${#to_install[@]} -gt 0 ]; then
        echo "Installing: ${to_install[*]}"
        sudo pacman -S --noconfirm "${to_install[@]}" || true
    else
        echo "All pacman dependencies already installed."
    fi
}

if command -v apt-get &>/dev/null; then
    install_apt
elif command -v dnf &>/dev/null; then
    install_dnf
elif command -v pacman &>/dev/null; then
    install_pacman
else
    echo "WARNING: Could not detect package manager. Please install manually:"
    echo "  - python3-gi (PyGObject)"
    echo "  - GTK3"
    echo "  - AppIndicator3 or AyatanaAppIndicator3"
    echo "  - libnotify"
    echo "  - iptables"
fi

echo ""
echo "[2/5] Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$CACHE_DIR"
mkdir -p "$AUTOSTART_DIR"
mkdir -p "$SYSTEMD_USER_DIR"

echo ""
echo "[3/5] Installing scripts..."
cp "$SCRIPT_DIR/vpn_checker.py" "$INSTALL_DIR/vpn-checker"
cp "$SCRIPT_DIR/vpn_killswitch.py" "$INSTALL_DIR/vpn-killswitch"
chmod +x "$INSTALL_DIR/vpn-checker"
chmod +x "$INSTALL_DIR/vpn-killswitch"

# ---------------------------------------------------------------------------
# Config handling
# ---------------------------------------------------------------------------
CONFIG_CREATED=false

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cat > "$CONFIG_DIR/config.json" << 'EOF'
{
  "expected_country": "",
  "expected_iface_prefix": "",
  "home_country": "",
  "strict_mode": false,
  "notifications": true
}
EOF
    CONFIG_CREATED=true
    echo "Created default config: $CONFIG_DIR/config.json"
else
    # Backup existing config before any migration
    backup_name="config.json.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$CONFIG_DIR/config.json" "$CONFIG_DIR/$backup_name"
    echo "Backed up existing config to $backup_name"

    python3 -c "
import json, sys
DEFAULT = {
    'expected_country': '',
    'expected_iface_prefix': '',
    'home_country': '',
    'strict_mode': False,
    'notifications': True,
}
try:
    with open('$CONFIG_DIR/config.json', 'r') as f:
        cfg = json.load(f)
    changed = False
    if 'soft_mode' in cfg:
        cfg.pop('soft_mode')
        changed = True
    for k, v in DEFAULT.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    with open('$CONFIG_DIR/config.json', 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    if changed:
        print('Migrated existing config')
    else:
        print('Existing config is up to date')
except Exception as e:
    print(f'Could not migrate config: {e}', file=sys.stderr)
    sys.exit(1)
"
fi

# ---------------------------------------------------------------------------
# Interactive configuration
# ---------------------------------------------------------------------------
# Helper: read a value from current config
_cfg_get() {
    python3 -c "import json; print(json.load(open('$CONFIG_DIR/config.json')).get('$1',''))"
}

# Helper: update config safely
_cfg_set() {
    local key="$1"
    local val="$2"
    python3 -c "
import json, sys
try:
    with open('$CONFIG_DIR/config.json', 'r') as f:
        cfg = json.load(f)
    cfg['$key'] = '$val'
    with open('$CONFIG_DIR/config.json', 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
except Exception as e:
    print(f'Config update failed: {e}', file=sys.stderr)
    sys.exit(1)
"
}

_prompt_param() {
    local key="$1"
    local prompt_text="$2"
    local current_val
    current_val=$(_cfg_get "$key")

    if [ "$IS_UPDATE" = true ] && [ "$FORCE_INTERACTIVE" = false ] && [ -n "$current_val" ]; then
        # Already set on update — skip silently
        return
    fi

    if [ -n "$current_val" ]; then
        read -rp "$prompt_text [current: $current_val, Enter to keep]: " new_val
        if [ -z "$new_val" ]; then
            return
        fi
    else
        read -rp "$prompt_text [Enter to skip]: " new_val
    fi

    if [ -n "$new_val" ]; then
        # Sanitize: alphanumeric only (safe for JSON and shell)
        new_val=$(echo "$new_val" | tr -cd 'a-zA-Z0-9' | head -c 20)
        if [ -n "$new_val" ]; then
            _cfg_set "$key" "$new_val"
            echo "  Set $key = $new_val"
        fi
    fi
}

if [ -t 0 ]; then
    echo ""
    if [ "$IS_UPDATE" = true ] && [ "$FORCE_INTERACTIVE" = false ]; then
        echo "Checking for missing config values..."
    else
        echo "Let's configure VPN detection heuristics..."
    fi

    # home_country
    if [ "$IS_UPDATE" = false ] || [ "$FORCE_INTERACTIVE" = true ] || [ -z "$(_cfg_get 'home_country')" ]; then
        detected_country=""
        if command -v curl &>/dev/null; then
            detected_country=$(curl -s --max-time 5 'https://ipapi.co/country/' 2>/dev/null | tr -cd 'A-Za-z' | head -c 2 | tr '[:lower:]' '[:upper:]')
        fi

        current_home=$(_cfg_get 'home_country')
        if [ -n "$current_home" ]; then
            read -rp "Your home country (2-letter code) [current: $current_home, Enter to keep]: " home_country
            home_country=${home_country:-$current_home}
        elif [ -n "$detected_country" ] && [ ${#detected_country} -eq 2 ]; then
            read -rp "Your home country (2-letter code) [detected: $detected_country, Enter to accept]: " home_country
            home_country=${home_country:-$detected_country}
        else
            read -rp "Your home country (2-letter code, e.g. RU, US) [Enter to skip]: " home_country
        fi
        home_country=$(echo "$home_country" | tr '[:lower:]' '[:upper:]' | tr -cd 'A-Z' | head -c 2)
        if [ -n "$home_country" ]; then
            _cfg_set "home_country" "$home_country"
            echo "  Set home_country = $home_country"
        fi
    fi

    # Note: expected_iface_prefix is optional. The app auto-detects common VPN
    # interfaces (wg*, tun*, singbox*, ppp*) by default. Only set this if you
    # need faster detection or a custom interface name.
    if [ "$FORCE_INTERACTIVE" = true ]; then
        _prompt_param "expected_iface_prefix" "Custom VPN interface prefix (optional, e.g. myvpn)"
    fi

    # expected_country (optional, usually less common than home_country)
    _prompt_param "expected_country" "Expected VPN country (2-letter code, e.g. NL, US)"
fi

echo ""
echo "[4/5] Setting up autostart..."

# Desktop entry
cat > "$AUTOSTART_DIR/vpn-checker.desktop" << EOF
[Desktop Entry]
Name=VPN Checker
Comment=VPN status indicator
Exec=$INSTALL_DIR/vpn-checker
Type=Application
Icon=network-vpn
Categories=Network;
X-GNOME-Autostart-enabled=true
EOF

# Systemd user service
if command -v systemctl &>/dev/null && systemctl --user &>/dev/null; then
    cat > "$SYSTEMD_USER_DIR/vpn-checker.service" << EOF
[Unit]
Description=VPN Checker Indicator
After=graphical-session.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/vpn-checker
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    if systemctl --user enable vpn-checker.service; then
        echo "Systemd user service enabled."
        systemctl --user restart vpn-checker.service 2>/dev/null || systemctl --user start vpn-checker.service 2>/dev/null || echo "WARNING: Could not start service now (may start on next login)."
    else
        echo "WARNING: Could not enable systemd user service."
    fi
else
    echo "Systemd not available, relying on .desktop autostart only."
fi

echo ""
echo "[5/5] Updating PATH..."
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    if ! grep -qF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
        echo "Added ~/.local/bin to PATH in .bashrc"
    else
        echo "~/.local/bin already in .bashrc"
    fi
else
    echo "~/.local/bin already in PATH"
fi

echo ""
echo "=========================================="
if [ "$IS_UPDATE" = true ]; then
    echo "  Update complete!"
else
    echo "  Installation complete!"
fi
echo "=========================================="
echo ""
echo "Quick start:"
echo "  vpn-checker              — start indicator now"
echo "  vpn-killswitch status    — check VPN interface"
echo "  sudo vpn-killswitch strict-on   — enable full kill switch"
echo "  sudo vpn-killswitch force-off   — emergency unlock"
echo ""
echo "Config file: $CONFIG_DIR/config.json"
echo ""
echo "Edit config and set at least one of:"
echo "  home_country            — e.g. 'RU', 'US', 'DE' (simplest: any other country = VPN ON)"
echo "  expected_country        — e.g. 'US', 'NL', 'DE' (exact match = VPN ON)"
echo "  expected_iface_prefix   — e.g. 'wg', 'tun', 'singbox'"
echo ""
echo "NOTE: If you use another shell (zsh, fish), add ~/.local/bin to its PATH manually."
