# VPN Checker 🔒

> 🤖 Написано с помощью [Kimi Code](https://www.moonshot.cn/)

## Зачем это нужно

**Проблема:** Claude, Codex и другие AI-сервисы банят аккаунт, если замечают запрос без VPN. Достаточно одного промпта из родной страны — и доступ пропадает.

**Решение:** Этот сервис сидит в трее, следит за VPN каждую секунду и реагирует **примерно за 1 секунду**:
- 🟢 VPN включён — работаем спокойно
- 🔴 VPN выключился — **сразу** блокирует весь интернет (kill switch), пока VPN не вернётся
- 🔔 Показывает уведомление, чтобы ты заметил

Работает с любым VPN: WireGuard, OpenVPN, sing-box и другими.

---

## Быстрая установка

```bash
cd vpn-checker
./install.sh
```

Не запускай под `sudo` — ставит в пользовательские директории.

Перезапусти сессию (или запусти вручную):
```bash
~/.local/bin/vpn-checker
```

## Обновление

```bash
cd vpn-checker
./install.sh
```

Скрипт сам перезапишет файлы, мигрирует конфиг и перезапустит сервис.

---

## Как это работает

| Ситуация | Что происходит |
|----------|----------------|
| VPN включился (`singbox_tun`, `wg0`, `tun0`...) | 🟢 Зелёный статус в трее |
| VPN выключился (обычный Wi-Fi/Ethernet) | 🔴 Красный статус + уведомление |
| Strict mode ON | 🔒 При VPN OFF — блокирует весь интернет, при VPN ON — разблокирует |

Интерфейс сканируется **каждую секунду**, IP-проверка — каждые 60 секунд.

---

## Настройка (необязательно)

При первой установке скрипт спросит `home_country` (например `RU`). Этого достаточно — программа сама определит популярные VPN-интерфейсы (`wg*`, `tun*`, `singbox*`, `ppp*`).

Если хочешь что-то изменить — открой `~/.config/vpn-checker/config.json`:

```json
{
  "home_country": "RU",
  "expected_country": "",
  "expected_iface_prefix": "",
  "strict_mode": false,
  "notifications": true
}
```

| Параметр | Зачем |
|----------|-------|
| `home_country` | Твоя страна. Если IP из неё — значит VPN выключен |
| `strict_mode` | Автоматически блокировать интернет без VPN |
| `notifications` | Показывать всплывающие уведомления |
| `expected_iface_prefix` | Только если VPN использует нестандартное имя интерфейса |

---

## Strict mode (kill switch)

Включается в меню трея (🔒 Strict mode). Когда активен:
- VPN ON — интернет работает
- VPN OFF — **весь исходящий трафик блокируется** кроме VPN, локальной сети и DNS

> ⚠️ При выходе из программы strict mode **автоматически отключается**, чтобы не оставить систему без интернета.

Аварийное отключение (если всё сломалось):
```bash
sudo vpn-killswitch force-off
```

---

## Требования

- Ubuntu / Debian / Fedora / Arch (Linux с systemd)
- Python 3.7+
- GTK3, AppIndicator, libnotify
- `iptables` + `ip6tables` (для strict mode)

Если `install.sh` не сработал — установи зависимости вручную:
```bash
sudo apt install -y python3-gi gir1.2-appindicator3-0.1 \
    gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 \
    iptables ip6tables libnotify-bin policykit-1
```

---

## Типичные проблемы

**Не вижу иконку в трее (GNOME)**
```bash
sudo apt install gnome-shell-extension-appindicator
```

**Strict mode заблокировал всё**
```bash
sudo vpn-killswitch force-off
```

**VPN определяется неверно**
- Проверь `home_country` в конфиге
- Проверь интерфейс: `ip route get 1.1.1.1`

**Уведомления не приходят**
```bash
sudo apt install libnotify-bin gir1.2-notify-0.7
```

**Strict mode не включается**
```bash
sudo apt install ip6tables
```

---

## Альтернативная защита

Kill switch реагирует примерно за **1 секунду**. За это время короткий запрос теоретически может уйти напрямую.

Более надёжный паттерн — запускать AI CLI через **wrapper + OS-level firewall guard**:

**Wrapper** задаёт proxy-переменные:

```bash
export HTTPS_PROXY=http://127.0.0.1:<port>
export HTTP_PROXY=http://127.0.0.1:<port>
export ALL_PROXY=socks5h://127.0.0.1:<port>
exec "$@"
```

**Preflight**: перед запуском проверь, что proxy/TUN/VPN в ожидаемом состоянии.

**Runtime guard**: процесс запускается в отдельной Unix-группе или security context. Firewall разрешает этой группе только соединение к `127.0.0.1:<port>` и блокирует весь остальной outbound, включая IPv6.

> ⚠️ Env proxy сам по себе **не является kill-switch**. Без firewall guard процесс может обойти proxy или уйти напрямую.

**Шаблон команд**:

```bash
# Установить wrapper и guard
sudo install -m 755 wrapper.sh /usr/local/bin/ai-wrapper
sudo ai-fw-guard enable --proxy-port <port>

# Проверить fail-closed: прямой curl из guarded context не работает
sudo -g ai_guarded curl https://ifconfig.me
# → ожидается: timeout / connection refused

# Проверить, что через proxy работает
HTTPS_PROXY=http://127.0.0.1:<port> sudo -g ai_guarded curl https://ifconfig.me

# Запускать AI CLI через wrapper
ai-wrapper <ai_cli_command>
```

Tray/status-checker утилита помогает мониторить VPN вручную, но **не заменяет firewall guard**.

---

## Логи

Если что-то работает не так — смотри лог:
```bash
tail -f ~/.cache/vpn-checker/vpn-checker.log
```

Там пишется каждое решение программы: какой интерфейс, какая страна, почему выбран тот или иной статус.
