# Автоматическое развёртывание — исходящее соединение Hetzner → GitHub

Схема: push в main → GitHub Actions запускает тесты → только при успехе
передвигает ветку deploy-ready → таймер Hetzner каждые ~10 минут получает
проверенный коммит по исходящему SSH → резервная копия → сборка → обновление
только archive → проверка READY → попытка отката образа при ошибке.

Входящий SSH с IP GitHub НЕ нужен. Firewall Hetzner остаётся закрытым
для посторонних адресов.

## 1. Обновить рабочие скрипты на Hetzner

Под пользователем monitor:

    cd /home/monitor/TelegramMediaArchive_v1
    git status --short
    git pull --ff-only origin main

Если есть локальные изменения, сначала разберите их. При git pull используйте
существующий серверный GitHub-ключ, его парольная фраза может запрашиваться.

## 2. Создать отдельный ключ для чтения GitHub

На Hetzner:

    bash scripts/setup_outbound_autodeploy_on_server.sh prepare

Скопируйте напечатанный публичный ssh-ed25519 ключ. В GitHub откройте
Settings → Deploy keys → Add deploy key, назовите
Hetzner Telegram Media Archive Auto Pull, вставьте публичный ключ
и НЕ включайте Allow write access.

Это отдельный ключ без пароля, который может только читать один приватный
репозиторий. Никогда не отправляйте приватную часть в ChatGPT или GitHub.

## 3. Проверить исходящее соединение

На Hetzner:

    bash scripts/setup_outbound_autodeploy_on_server.sh verify

Успех: TESTED_RELEASE=... и PULL_CHECK_OK или ALREADY_DEPLOYED.

## 4. Первый контролируемый выпуск

Убедитесь, что есть резервная копия и дисковое пространство.
На Hetzner:

    bash scripts/auto_pull_ready_on_server.sh --run

Процедура создаст и проверит резервную копию, соберёт образ, перезапустит
ТОЛЬКО archive и проверит READY. Если запуск неудачен, будет попытка
отката предыдущего образа. Не повторяйте команду при ошибке до изучения
журнала. Данные в volume не откатываются автоматически.

## 5. Включить периодическую проверку

Только после успешного OUTBOUND_DEPLOY_COMPLETED:

    bash scripts/setup_outbound_autodeploy_on_server.sh enable
    bash scripts/setup_outbound_autodeploy_on_server.sh status

Теперь раз в ~10 минут Hetzner проверяет только CI-помеченную ветку
deploy-ready. На сервер не поступают входящие соединения от GitHub.

## Диагностика и выключение

    sudo journalctl -u telegram-media-archive-autopull.service -n 80 --no-pager
    sudo systemctl list-timers telegram-media-archive-autopull.timer
    bash scripts/setup_outbound_autodeploy_on_server.sh disable

В GitHub: Actions → Telegram Media Archive CI показывает тесты и
«Mark tested commit as deploy-ready». Ранее использовавшиеся секреты
ARCHIVE_DEPLOY_SSH_KEY и ARCHIVE_DEPLOY_KNOWN_HOSTS и переменная
ENABLE_AUTO_DEPLOY больше не нужны: после проверки новой схемы их
можно удалить из Secrets and variables → Actions.

## Ограничения

- Текущий механизм откатывает только образ приложения, не миграции SQLite.
- Медиа копируются при работающем архиваторе; это не атомарный снимок.
- Служебные скрипты на сервере не обновляются без отдельного git pull;
  GitHub доставляет только программную часть нового релиза.
- Архивы содержат личные переписки, Telegram-сессию и секреты — их
  необходимо защищать и регулярно проверять.
- Подключение к Docker через sudo доступно пользователю monitor;
  не предоставляйте этот аккаунт сторонним GitHub workflows.

## Резервные копии: только безопасный просмотр

Сначала обновите серверные скрипты командой `git pull --ff-only origin main`
от пользователя `monitor`, если нет несохранённых локальных изменений.

Посмотреть без удаления:

    cd /home/monitor/TelegramMediaArchive_v1
    python3 scripts/backup_retention.py
    find /home/monitor/telegram-media-archive-backups -mindepth 1 -maxdepth 1 -type d | wc -l
    du -sh /home/monitor/telegram-media-archive-backups

Программа покажет BACKUPS_TOTAL, BACKUPS_RETAINED, BACKUPS_ELIGIBLE
и завершается BACKUP_RETENTION_DRY_RUN_ONLY.

Правило хранения по UTC: до 7 дней все копии, 8–30 дней одна
за день, 31–90 дней одна за ISO-неделю. Пять самых новых копий
сохраняются независимо от возраста.

Удаление не включено в systemd и не запускается по таймеру.
Отдельный ключ --apply существует, но использовать его можно только
после анализа предпросмотра и ручного подтверждения правил.

Дополнительные ограничения --apply: только фиксированный каталог,
проверка владельца, блокировка на время deploy, две новейшие копии
должны пройти SQLite-проверку, минимум пять сохранённых копий,
самая новая копия младше семи дней. Любая ошибка прерывает удаление.
Это НЕ заменяет внешнюю резервную копию или проверку всех медиа.
