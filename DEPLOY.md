# Развёртывание finv на РедОС 8.0.2

## 1. Установка зависимостей

```bash
# Установка Python и PostgreSQL
sudo dnf install -y python3 python3-pip python3-devel postgresql-server postgresql-devel
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

## 2. Настройка PostgreSQL

```bash
# Создание базы и пользователя
sudo -u postgres psql -c "CREATE USER finv_user WITH PASSWORD 'finv_password';"
sudo -u postgres psql -c "CREATE DATABASE finv OWNER finv_user;"
sudo -u postgres psql -c "ALTER USER finv_user CREATEDB;"

# Настройка доступа (pg_hba.conf)
# Замените METHOD на md5 для локальных подключений
sudo nano /var/lib/pgsql/data/pg_hba.conf
# Перезапуск PostgreSQL
sudo systemctl restart postgresql
```

## 3. Развёртывание приложения

```bash
# Копирование проекта
sudo mkdir -p /opt/finv
sudo cp -r * /opt/finv/
cd /opt/finv

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Применение миграций
python manage.py migrate

# Сбор статики
python manage.py collectstatic --noinput

# Создание суперпользователя
python manage.py createsuperuser
```

## 4. Запуск через Gunicorn + systemd

```bash
# Создание systemd-сервиса
sudo nano /etc/systemd/system/finv.service
```

```ini
[Unit]
Description=finv inventory system
After=network.target postgresql.service

[Service]
Type=simple
User=finv
Group=finv
WorkingDirectory=/opt/finv
Environment="PATH=/opt/finv/venv/bin"
ExecStart=/opt/finv/venv/bin/gunicorn finv.wsgi:application -b 0.0.0.0:8000 -w 4
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finv
```

## 5. Настройка Nginx (опционально)

```bash
sudo dnf install -y nginx
sudo nano /etc/nginx/conf.d/finv.conf
```

```nginx
server {
    listen 80;
    server_name your-server-ip;

    location /static/ {
        alias /opt/finv/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
sudo systemctl enable --now nginx
```

## 6. Создание ролей

Зайдите в админ-панель `/admin/`, создайте группы:
- **viewer** — доступ только на просмотр
- **editor** — права на добавление/изменение записей

Назначьте пользователям соответствующие группы.
