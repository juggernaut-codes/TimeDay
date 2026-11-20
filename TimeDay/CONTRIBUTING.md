# Участие в разработке TimeDay

Для разработки нужны Linux, Python 3.10+ и системные зависимости из [README](README.md#установка-и-обновление-на-linux-mint). GTK устанавливается пакетами дистрибутива; отдельная установка через pip не требуется.

## Запуск без установки

Из корня проекта:

```bash
python3 -m timeday
```

Для проверки интерфейса используйте временные данные, чтобы не менять свои задачи и статистику:

```bash
TIMEDAY_DEV_DIR="$(mktemp -d)"
mkdir -p "$TIMEDAY_DEV_DIR/config" "$TIMEDAY_DEV_DIR/desktop"
printf 'XDG_DESKTOP_DIR="%s/desktop"\n' "$TIMEDAY_DEV_DIR" > "$TIMEDAY_DEV_DIR/config/user-dirs.dirs"
XDG_DATA_HOME="$TIMEDAY_DEV_DIR/data" \
XDG_CONFIG_HOME="$TIMEDAY_DEV_DIR/config" \
XDG_CACHE_HOME="$TIMEDAY_DEV_DIR/cache" \
python3 -m timeday
```

Установленное приложение перед этим нужно полностью закрыть через «Выйти» в трее: TimeDay использует один экземпляр на сеанс D-Bus. В этом примере тестовые ярлыки и настройки также остаются во временной папке.

## Проверки перед pull request

```bash
python3 -m unittest discover -s tests -v
python3 -m timeday --version
desktop-file-validate data/io.github.timeday.TimeDay.desktop
```

Тесты не требуют открытого графического окна. Изменения интерфейса дополнительно проверьте в обычном и компактном окне, а изменения жизненного цикла — при обычном запуске, запуске с `--background` и повторном открытии из трея.

Новые строки интерфейса оборачивайте в `tr(...)` и добавляйте переводы в `timeday/locales/en.json` и `timeday/locales/uk.json`. Названия пользовательских задач, событий и приложений не переводятся.

В pull request опишите проблему, новое поведение и выполненные проверки. Для изменений интерфейса приложите снимок окна с тестовыми данными. Не добавляйте базы SQLite, резервные копии, личные журналы или содержимое домашнего каталога.
