# Миграции базы данных

Эта папка содержит все миграции базы данных для проекта.

## Структура

Миграции должны именоваться по шаблону: `NN_description.sql`, где:
- `NN` - порядковый номер миграции (01, 02, 03, ...)
- `description` - краткое описание миграции

Примеры:
- `04_add_battles_table.sql`
- `05_add_daily_checkin.sql`

## Применение миграций

### Автоматическое применение всех миграций

**Локально:**
```bash
cd db
./apply_migrations.sh
```

**Через Docker:**
```bash
cd db
./apply_migrations.sh docker
```

Скрипт автоматически:
1. Найдет все миграции в папке `migrations/`
2. Отсортирует их по номеру
3. Применит на обе БД: `lab` и `lab_test`
4. Если есть миграция 05, попытается автоматически сгенерировать ежедневные коды

**Примечание:** Для генерации кодов локально требуется установить зависимости:
```bash
pip install psycopg2-binary python-dotenv
```

Или используйте виртуальное окружение проекта:
```bash
# Если у вас есть виртуальное окружение
source venv/bin/activate  # или .venv/bin/activate
cd db
./apply_migrations.sh
```

### Ручное применение отдельной миграции

**Локально:**
```bash
# На основную БД
psql -U <username> -d lab -f db/migrations/05_add_daily_checkin.sql

# На тестовую БД
psql -U <username> -d lab_test -f db/migrations/05_add_daily_checkin.sql
```

**Через Docker:**
```bash
# На основную БД
docker exec -i <postgres_container> psql -U <username> -d lab < db/migrations/05_add_daily_checkin.sql

# На тестовую БД
docker exec -i <postgres_container> psql -U <username> -d lab_test < db/migrations/05_add_daily_checkin.sql
```

## Список миграций

### 04_add_battles_table.sql
Добавляет таблицу `Battles` для системы батлов.

### 05_add_daily_checkin.sql
Добавляет систему ежедневного чекина:
- Таблица `Daily_codes` - ежедневные коды на год вперед
- Таблица `Daily_checkins` - история чекинов пользователей
- Таблица `User_boost` - персональные бонусы (увеличение шансов)

**Важно:** После применения этой миграции нужно сгенерировать коды.

Скрипт `apply_migrations.sh` попытается сгенерировать коды автоматически, но если это не удастся (например, модуль `psycopg2` не установлен), вы можете сделать это вручную:

```bash
# Убедитесь, что зависимости установлены
pip install psycopg2-binary python-dotenv

# Генерация для основной БД
export POSTGRES_DB=lab
python3 db/utils/generate_daily_codes.py

# Генерация для тестовой БД
export POSTGRES_DB=lab_test
python3 db/utils/generate_daily_codes.py
```

Или через Docker:
```bash
docker exec -e POSTGRES_DB=lab <app_container> python3 /app/db/utils/generate_daily_codes.py
docker exec -e POSTGRES_DB=lab_test <app_container> python3 /app/db/utils/generate_daily_codes.py
```

## Правила создания миграций

1. Все миграции должны использовать `CREATE TABLE IF NOT EXISTS` и `CREATE INDEX IF NOT EXISTS`
2. Миграции должны быть идемпотентными (безопасны для повторного применения)
3. Не удаляйте существующие данные
4. Всегда добавляйте комментарии в начало файла с описанием миграции
5. Нумерация должна быть последовательной

## Откат миграций

Откат миграций не предусмотрен автоматически. Если нужно откатить миграцию, создайте новую миграцию с обратными изменениями или выполните SQL вручную.

Пример отката миграции 05:
```sql
DROP INDEX IF EXISTS idx_user_boost_active;
DROP INDEX IF EXISTS idx_user_boost_user;
DROP INDEX IF EXISTS idx_daily_checkins_date;
DROP INDEX IF EXISTS idx_daily_checkins_user;
DROP INDEX IF EXISTS idx_daily_codes_date;
DROP TABLE IF EXISTS User_boost;
DROP TABLE IF EXISTS Daily_checkins;
DROP TABLE IF EXISTS Daily_codes;
```
