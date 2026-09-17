# Чистая установка finv 4.1 на РедОС 8

`deploy.sh` предназначен только для новой операционной системы и пустой локальной
PostgreSQL. Он не переносит данные 4.0, не импортирует CSV и не обновляет
существующую установку.

Скрипт откажется от работы, если уже существует хотя бы один из объектов:

- `/opt/finv`;
- `/etc/systemd/system/finv.service`;
- системный пользователь `finv`;
- роль PostgreSQL `finv_user`;
- база PostgreSQL `finv`.

Он никогда не удаляет эти объекты автоматически.

## Подготовка выпуска

Перенесите исходники 4.1 на сервер одним архивом без локального `venv`, SQLite,
логов и пользовательских данных:

```bash
BUNDLE="$HOME/finv-4.1-$(date +%Y%m%d-%H%M%S).tar.gz"
tar --exclude='__pycache__' --exclude='*.pyc' \
  -czf "$BUNDLE" \
  -C /home/fnkpp/finv-testing \
  finv inventory templates static tools/deploy_safe.py \
  manage.py requirements.txt deploy.sh DEPLOY.md LICENSE
sha256sum "$BUNDLE"
```

После переноса проверьте SHA-256 и распакуйте архив на локальный диск сервера:

```bash
sudo install -d -m 755 /var/tmp/finv-4.1
sudo tar --no-same-owner -xzf /путь/к/finv-4.1-ДАТА.tar.gz -C /var/tmp/finv-4.1
cd /var/tmp/finv-4.1
```

## Автоматическая установка

Для текущего HTTP-доступа по адресу `172.27.5.2:8000`:

```bash
sudo env FINV_ALLOWED_HOSTS=172.27.5.2 FINV_TRANSPORT=http \
  bash ./deploy.sh --install
```

Пароль роли PostgreSQL и `FINV_SECRET_KEY` генерируются автоматически и
сохраняются только в `/opt/finv/.env` с режимом `0600`. Пароль по умолчанию
`finv_password` больше не используется.

Скрипт автоматически:

1. Устанавливает Python, PostgreSQL, клиентские библиотеки, rsync и SELinux tools.
2. Инициализирует и запускает локальный PostgreSQL.
3. Создает системного пользователя `finv`.
4. Создает пустую БД `finv` и роль `finv_user` со случайным паролем.
5. Добавляет в `pg_hba.conf` узкое SCRAM-правило только для этой БД, роли и
   `127.0.0.1`, затем проверяет реальное подключение.
6. Копирует только код 4.1 в `/opt/finv`, создает серверный venv и устанавливает
   `requirements.txt`.
7. Выполняет `check`, полный набор миграций `0001`-`0007`, проверку схемы и
   `collectstatic`.
8. Создает `finv.service`, настраивает nginx для static/media и запускает службы.

При HTTP Gunicorn слушает только `127.0.0.1:8001`, а nginx принимает запросы на
порту `8000` и обслуживает `/static/` и `/media/`. Если firewalld активен, installer
открывает `8000/tcp`; для SELinux порт регистрируется как `http_port_t`. Ограничьте
доступ доверенной сетью согласно политике сервера.

## Первый администратор

После успешной установки выполните команду, которую также напечатает installer:

```bash
sudo -u finv bash -c '
  set -a
  source /opt/finv/.env
  set +a
  /opt/finv/venv/bin/python /opt/finv/manage.py createsuperuser
'
```

Стандартная учетная запись `admin/admin123` больше не создается.

После входа назначьте пользователю нужную группу в `/admin/`:

- `viewer` - просмотр;
- `editor` - просмотр и изменение без удаления;
- `administrator` - полный доступ приложения.

## Проверка

```bash
sudo systemctl status finv --no-pager
sudo journalctl -u finv -n 100 --no-pager
curl -i -H 'Host: 172.27.5.2' http://127.0.0.1:8000/login/
```

Откройте `http://172.27.5.2:8000/` и проверьте вход, справочники, оборудование,
расходники, документы, профиль, уведомления, журнал аудита и загрузку файлов.

## HTTPS

Режим `https-proxy` выбирайте только после настройки внешнего nginx и TLS. При
установке дополнительно требуется внешний URL проверки:

```bash
sudo env FINV_ALLOWED_HOSTS=inventory.example.org \
  FINV_TRANSPORT=https-proxy \
  FINV_READY_URL=https://inventory.example.org/login/ \
  bash ./deploy.sh --install
```

В этом режиме Gunicorn слушает только `127.0.0.1:8000`, включаются secure cookies,
SSL redirect, proxy headers и HSTS. Автоматическая HTTP-конфигурация nginx не
создается: внешний nginx и сертификаты должны быть подготовлены заранее.

## Ошибка установки

Не запускайте installer повторно поверх частично созданной системы и не удаляйте
БД отдельными командами наугад. Сохраните полный вывод и проверьте:

```bash
sudo systemctl status postgresql finv --no-pager
sudo journalctl -u postgresql -u finv -n 200 --no-pager
```

Для гарантированно чистого повторения используйте новый снимок/переустановку ОС
либо сначала разберите конкретный этап отказа. `deploy.sh --update`, импорт данных
4.0 и автоматическое удаление намеренно не поддерживаются.
