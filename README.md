# VPN Checker 🔒

Индикатор VPN в системном трее Linux с флагом страны и kill switch.

> ⚠️ **Privacy warning**: по умолчанию индикатор обращается к внешним сервисам (`ipapi.co`, `ipinfo.io`) для определения вашего публичного IP и страны. Это означает, что ваш текущий IP передаётся третьей стороне. Если вас это беспокоит, отключите автопроверку или используйте только `expected_iface_prefix` без запросов к API.

## Что делает

- 🌍 **Флаг страны** вашего IP прямо в трее (обновляется каждые 30 сек)
- 🟢🟡🔴 **Цветной статус**: VPN включён / выключен / неизвестен
- 🔔 **Уведомления** при смене статуса VPN
- 🔒 **Strict mode** — полная блокировка интернета без VPN
- 🛡️ **Soft mode** — блокировка только Anthropic/OpenAI (Claude/Codex) без VPN
- ⚙️ **Работает с любым VPN**: WireGuard, OpenVPN и др.

## Быстрая установка

```bash
cd vpn-checker   # перейди в директорию с репозиторием
./install.sh      # не запускай под sudo — скрипт ставит в пользовательские директории
```

Запусти:
```bash
~/.local/bin/vpn-checker
```

## Настройка

Открой `~/.config/vpn-checker/config.json`:

```json
{
  "expected_country": "US",
  "expected_iface_prefix": "wg",
  "home_country": "RU",
  "strict_mode": false,
  "soft_mode": false,
  "notifications": true
}
```

| Параметр | Описание |
|----------|----------|
| `expected_country` | Ожидаемая страна VPN (2 буквы: US, NL, DE...) |
| `expected_iface_prefix` | Префикс интерфейса: `wg` (WireGuard), `tun` (OpenVPN) |
| `home_country` | Родная страна (2 буквы). Если текущий IP НЕ из неё — считается что VPN включён |
| `strict_mode` | Блокировать весь трафик кроме VPN, LAN и служебного |
| `soft_mode` | Блокировать только Anthropic/OpenAI при отключённом VPN |
| `notifications` | Desktop-уведомления |

> **Рекомендация**: заполни `home_country` (например `"RU"`) — это самый простой способ. Тогда любая другая страна = VPN ON, родная = VPN OFF. Альтернативно можно использовать `expected_country` или `expected_iface_prefix`.

## Режимы защиты

### 🛡️ Soft mode (рекомендуется для Claude/Codex)

Блокирует доступ к сервисам Anthropic и OpenAI **только если VPN выключен**. Остальной интернет работает без ограничений.

> **Важно**: `strict_mode` и `soft_mode` взаимоисключают друг друга. Включение одного автоматически выключает другой в GUI.

**Что блокируется:**
- IP-диапазоны Anthropic: `160.79.104.0/23`, `209.249.57.0/24`, `2607:6bc0::/32`
- Домены через `/etc/hosts`: `claude.ai`, `anthropic.com`, `chatgpt.com`, `openai.com`, `api.openai.com` и др.

**Как работает:**
- VPN включён → блокировка снята, сервисы доступны
- VPN выключен → блокировка активна, Claude/Codex не открываются
- При выходе из индикатора блокировка **автоматически снимается**

```bash
# Включить через терминал
sudo ~/.local/bin/vpn-killswitch soft-on

# Выключить
sudo ~/.local/bin/vpn-killswitch soft-off
```

### 🔒 Strict mode (kill switch)

Блокирует **весь** исходящий трафик, кроме VPN, локальной сети и служебного (DNS, NTP).

> ⚠️ **Важно**: при выходе из индикатора strict mode **автоматически отключается**, чтобы не оставить систему без интернета. Это компромисс безопасности vs. доступности. Если нужен перманентный kill switch, настраивайте его отдельно через systemd или cron, а не через GUI.

```bash
# Включить
sudo ~/.local/bin/vpn-killswitch strict-on

# Выключить и восстановить правила
sudo ~/.local/bin/vpn-killswitch strict-off

# Аварийное разблокирование (если всё сломалось)
sudo ~/.local/bin/vpn-killswitch force-off

# Статус
~/.local/bin/vpn-killswitch status
```

### Сравнение режимов

| | Soft mode | Strict mode |
|---|---|---|
| Что блокируется | Только Anthropic/OpenAI | Весь интернет |
| Браузер работает | ✅ Да | ❌ Нет (без VPN) |
| Другие сервисы | ✅ Доступны | ❌ Заблокированы |
| Защита от утечек | 🟡 Частичная | 🟢 Полная |
| Удобство | 🟢 Высокое | 🔴 Низкое |

### Что разрешено в Strict mode

| Трафик | Статус |
|--------|--------|
| Loopback (localhost) | ✅ Разрешён |
| Локальная сеть (192.168.x.x, 10.x.x.x) | ✅ Разрешён |
| DNS (UDP/TCP 53) | ✅ Разрешён (нужен для резолва VPN-сервера) |
| NTP (time sync) | ✅ Разрешён |
| VPN-интерфейс (wg0, tun0...) | ✅ Разрешён |
| IP VPN-сервера | ✅ Разрешён |
| IPv6 (если `ip6tables` установлен) | ✅ Блокируется / Разрешается аналогично |
| Всё остальное | ❌ **Заблокировано** |

**Известные ограничения:**
- **DNS leak**: DNS-запросы разрешены глобально, чтобы VPN-клиент мог резолвить сервер до установки туннеля. Это компромисс по дизайну.
- **LAN доступ**: локальная сеть остаётся доступной даже без VPN.
- **IPv6**: если `ip6tables` не установлен, IPv6-трафик НЕ будет заблокирован. Установите `ip6tables` или отключите IPv6 в системе.
- **Конфликты**: kill switch напрямую управляет `iptables` и может конфликтовать с `ufw`, `firewalld`, Docker или другими менеджерами фаервола. Перед включением убедитесь, что другие фаерволы отключены или совместимы.

### Безопасность

- Правила сохраняются перед изменением; `strict-off` восстанавливает их.
- Работает с **IPv4 и IPv6** (если `ip6tables` доступен).
- Команды iptables выполняются без `shell=True` (защита от инъекций).
- Backup хранится в `/var/tmp/vpn-checker-<uid>/` (не в `$HOME`, чтобы избежать symlink-атак при выполнении через `pkexec`).

## Архитектура

```
┌─────────────────┐      ┌──────────────┐      ┌─────────────┐
│  vpn_checker.py │─────→│  ipapi.co    │      │  System Tray│
│  (GTK + tray)   │      │  ipinfo.io   │      │  (флаг VPN) │
└─────────────────┘      └──────────────┘      └─────────────┘
         │
         ↓ pkexec
┌─────────────────────────────────┐
│      vpn_killswitch.py          │
│  strict: full internet block    │
│  soft:   Anthropic/OpenAI only  │
└─────────────────────────────────┘
```

## Автозапуск

Установщик настраивает два способа (для надёжности):
1. `.desktop` файл в `~/.config/autostart` — работает везде.
2. `systemd --user` сервис — предпочтительный способ на современных DE.

Если оба способа активны, запустится только один инстанс (благодаря идентификатору `'vpn-checker'` в AppIndicator). Проверить статус:
```bash
systemctl --user status vpn-checker
```

## Требования

- Python 3.7+ (тестировалось на Ubuntu 22.04/24.04)
- GTK3 + GObject introspection
- AppIndicator3 (или AyatanaAppIndicator3)
- libnotify
- iptables + ip6tables (опционально, но рекомендуется)
- PolicyKit (`pkexec`)

## Типичные проблемы

**Не вижу иконку в трее (GNOME)**
```bash
sudo apt install gnome-shell-extension-appindicator
# Или: https://extensions.gnome.org/extension/615/appindicator-support/
```

**Включил strict mode, а VPN не подключается**
```bash
# Аварийное отключение прямо в терминале (другой TTY или заранее открытый)
sudo ~/.local/bin/vpn-killswitch force-off
```

**Soft mode не блокирует OpenAI**
- OpenAI использует Cloudflare (CDN), поэтому блокировка по IP невозможна без ложных срабатываний. Soft mode блокирует домены через `/etc/hosts` — убедитесь, что ваш браузер/приложение использует системный DNS.

**Неверно определяет VPN**
- Укажи `expected_country` или `expected_iface_prefix` в конфиге
- Проверь интерфейс: `ip route get 1.1.1.1`

**Уведомления не приходят**
```bash
sudo apt install libnotify-bin gir1.2-notify-0.7
```

**Конфликт с ufw / firewalld / Docker**
- Отключите другие фаерволы перед включением kill switch:
  ```bash
  sudo ufw disable
  sudo systemctl stop firewalld
  ```
- Docker модифицирует iptables; kill switch может нарушить его работу.
