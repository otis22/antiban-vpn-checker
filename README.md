# VPN Checker 🔒

Индикатор VPN в системном трее Linux с флагом страны и kill switch.

> 🤖 Этот проект написан с помощью [Kimi Code](https://www.moonshot.cn/)

> ⚠️ **Privacy warning**: по умолчанию индикатор обращается к внешним сервисам (`ipapi.co`, `ipinfo.io`) для определения вашего публичного IP и страны. Это означает, что ваш текущий IP передаётся третьей стороне. Если вас это беспокоит, отключите автопроверку или используйте только `expected_iface_prefix` без запросов к API.

## ⚠️ Важные ограничения

- **Kill switch не переживает выхода из приложения, краша или перезагрузки.** При выходе из индикатора strict mode **автоматически отключается**, чтобы не оставить систему без интернета. Это осознанный компромисс безопасности vs. доступности. Если нужен перманентный kill switch, настраивайте его отдельно через systemd.
- **Strict mode — не 100% защита.** DNS-запросы и LAN-трафик разрешены даже без VPN. Это необходимо для работы VPN-клиента и локальной сети.

## Что делает

- 🌍 **Флаг страны** вашего IP прямо в трее (обновляется каждые 60 сек)
- 🟢🟡🔴 **Цветной статус**: VPN включён / выключен / неизвестен
- 🔔 **Уведомления** при смене статуса VPN
- 🔒 **Strict mode** — полная блокировка интернета без VPN
- ⚙️ **Работает с любым VPN**: WireGuard, OpenVPN, sing-box и др.
- ⚡ **Быстрое определение** по интерфейсу (каждые 3 сек)

## Предполётный чек-лист (перед включением kill switch)

- [ ] Знаешь команду аварийного отключения: `sudo vpn-killswitch force-off`
- [ ] Имеешь открытый терминал / другой TTY на случай блокировки
- [ ] Понимаешь, что strict mode заблокирует весь интернет без VPN
- [ ] Убедился что нет конфликтов с `ufw`, `firewalld`, Docker
- [ ] Установлен `ip6tables` (иначе IPv6-трафик утечёт в strict mode)

## Быстрая установка

```bash
cd vpn-checker   # перейди в директорию с репозиторием
./install.sh      # не запускай под sudo — скрипт ставит в пользовательские директории
```

Установщик автоопределит твою страну и заполнит конфиг. Если `curl` доступен — просто жми Enter.

Запусти:
```bash
~/.local/bin/vpn-checker
```

## Обновление

Просто запусти `install.sh` повторно — скрипты перезапишутся, конфиг мигрируется, systemd сервис перезапустится:

```bash
cd vpn-checker
./install.sh
```

## Настройка

Открой `~/.config/vpn-checker/config.json`:

```json
{
  "expected_country": "US",
  "expected_iface_prefix": "wg",
  "home_country": "RU",
  "strict_mode": false,
  "notifications": true
}
```

| Параметр | Описание |
|----------|----------|
| `expected_country` | Ожидаемая страна VPN (2 буквы: US, NL, DE...) |
| `expected_iface_prefix` | Префикс интерфейса (опционально). Программа автоопределяет `wg*`, `tun*`, `singbox*`, `ppp*` по умолчанию. Укажи только для кастомного имени или мгновенной реакции |
| `home_country` | Родная страна (2 буквы). Если текущий IP НЕ из неё — считается что VPN включён |
| `strict_mode` | Блокировать весь трафик кроме VPN, LAN и служебного |
| `notifications` | Desktop-уведомления |

> **Рекомендация**: заполни `home_country` (например `"RU"`) — это самый простой способ. Тогда любая другая страна = VPN ON, родная = VPN OFF.
> Программа автоматически определяет популярные VPN-интерфейсы (`wg*`, `tun*`, `singbox*`, `ppp*`) по имени за 3 секунды. `expected_iface_prefix` нужен только если у тебя нестандартное имя интерфейса или ты хочешь ещё быстрее исключить ложные срабатывания.

## Режимы защиты

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

### Что разрешено в Strict mode

| Трафик | Статус |
|--------|--------|
| Loopback (localhost) | ✅ Разрешён |
| Локальная сеть (192.168.x.x, 10.x.x.x) | ✅ Разрешён |
| DNS (UDP/TCP 53) | ✅ Разрешён (нужен для резолва VPN-сервера) |
| NTP (time sync) | ✅ Разрешён |
| VPN-интерфейс (wg0, tun0, singbox_tun...) | ✅ Разрешён |
| IP VPN-сервера | ✅ Разрешён |
| IPv6 (если `ip6tables` установлен) | ✅ Блокируется / Разрешается аналогично |
| Всё остальное | ❌ **Заблокировано** |

**Известные ограничения:**
- **DNS leak**: DNS-запросы разрешены глобально, чтобы VPN-клиент мог резолвить сервер до установки туннеля. Это компромисс по дизайну.
- **LAN доступ**: локальная сеть остаётся доступной даже без VPN.
- **IPv6**: если `ip6tables` не установлен, strict mode **откажется включаться**. Установи `ip6tables` или отключи IPv6 в системе.
- **Конфликты**: kill switch напрямую управляет `iptables` и может конфликтовать с `ufw`, `firewalld`, Docker или другими менеджерами фаервола. Перед включением убедись, что другие фаерволы отключены или совместимы.

### Безопасность

- Правила сохраняются перед изменением; `strict-off` восстанавливает их.
- Работает с **IPv4 и IPv6** (если `ip6tables` доступен).
- Команды iptables выполняются без `shell=True` (защита от инъекций).
- Backup хранится в `/var/tmp/vpn-checker-<uid>/` (не в `$HOME`, чтобы избежать symlink-атак при выполнении через `pkexec`).

## Проверка работы

### Проверить VPN-определение
```bash
# Проверь что индикатор определяет страну верно
vpn-killswitch status
```

### Проверить strict mode
```bash
# 1. Убедись что VPN подключён
# 2. Включи strict mode
sudo ~/.local/bin/vpn-killswitch strict-on

# 3. Проверь что обычный интернет не работает (без VPN)
#    Отключи VPN и попробуй:
curl -I https://example.com   # должен таймаутиться

# 4. Включи VPN обратно — интернет должен появиться
# 5. Выключи strict mode
sudo ~/.local/bin/vpn-killswitch strict-off
```

## Архитектура

```
┌─────────────────┐      ┌──────────────┐      ┌─────────────┐
│  vpn_checker.py │─────→│  ipapi.co    │      │  System Tray│
│  (GTK + tray)   │      │  ipinfo.io   │      │  (флаг VPN) │
└─────────────────┘      └──────────────┘      └─────────────┘
         │
         │  каждые 3 сек  →  ip route get  (быстрая проверка интерфейса)
         │  каждые 60 сек →  IP API        (медленная проверка страны)
         ↓ pkexec
┌─────────────────────────────────┐
│      vpn_killswitch.py          │
│  strict: full internet block    │
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
- iptables + ip6tables (опционально, но **строго рекомендуется для strict mode**)
- PolicyKit (`pkexec`)

## Установка зависимостей вручную (Ubuntu/Debian)

Если `install.sh` не сработал:
```bash
sudo apt update
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-appindicator3-0.1 gir1.2-ayatanaappindicator3-0.1 \
    gir1.2-notify-0.7 iptables ip6tables libnotify-bin policykit-1
```

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

**Неверно определяет VPN**
- Укажи `home_country` в конфиге (например `"RU"`)
- Программа автоопределяет `wg*`, `tun*`, `singbox*`, `ppp*` по умолчанию. Если твой VPN использует другое имя — укажи `expected_iface_prefix`
- Проверь интерфейс: `ip route get 1.1.1.1`

**Уведомления не приходят**
```bash
sudo apt install libnotify-bin gir1.2-notify-0.7
```

**Конфликт с ufw / firewalld / Docker**
- Отключи другие фаерволы перед включением kill switch:
  ```bash
  sudo ufw disable
  sudo systemctl stop firewalld
  ```
- Docker модифицирует iptables; kill switch может нарушить его работу.
- **Rollback после тестов:**
  ```bash
  sudo ufw enable          # вернуть ufw
  sudo systemctl start firewalld   # вернуть firewalld
  ```

**pkexec не запрашивает пароль / не работает**
- Убедись что установлен `policykit-1`
- В Wayland/GNOME должен быть запущен polkit агент (обычно по умолчанию)
- Проверь: `pkexec echo test` — должно запросить пароль

**Strict mode отказывается включаться**
```bash
# Проверь что ip6tables установлен
sudo ip6tables -L -n
# Если нет — установи:
sudo apt install ip6tables
```
