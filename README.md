# FABDEM Downloader

Локальное мини-приложение для скачивания **FABDEM V1-2** — глобальной модели рельефа
с удалёнными зданиями и лесом, 1 arc-second (~30 м).

Сервис работает как один Python-процесс: FastAPI + Jinja2 + Leaflet. Отдельного
frontend-проекта, Node.js и сборки нет.

## Что умеет

- скачивать одну стандартную плитку **1° × 1°**;
- вырезать произвольный **прямоугольник**;
- брать **видимую область карты**;
- принимать **полигон**, нарисованный на карте, или файл **KML / KMZ / GeoJSON**;
- маскировать всё вне полигона в NoData;
- сохранять GeoTIFF в **EPSG:4326**, **EPSG:3857** или **UTM (авто)**;
- показывать площадь, количество плиток и примерный размер файла;
- отдавать оригинальный GeoTIFF для режима одной плитки;
- собирать область через 180-й меридиан в ZIP из двух файлов.

## Быстрый старт на Windows

Дважды щёлкните `run.bat` или выполните:

```bat
run.bat
```

Откройте <http://127.0.0.1:8000>.

При первом запуске приложение скачает индекс плиток FABDEM (~5.7 МБ) в
`data/FABDEM_v1-2_tiles.geojson`. Первая плитка скачивается с data.bris и
кэшируется в `data/cache/tiles`.

## Ручной запуск

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`rasterio` для Windows устанавливается колесом и приносит GDAL с собой.
Отдельно ставить GDAL не нужно.

## Как пользоваться

1. **1° плитка** — увеличьте карту до zoom 5+, кликните по квадрату сетки.
2. **Прямоугольник** — нарисуйте рамку или введите W/S/E/N в панели.
3. **Полигон** — нарисуйте область двойным кликом или загрузите KML/KMZ/GeoJSON.
4. **Видимая область** — кнопка берёт текущие границы карты.
5. Проверьте оценку размера и нажмите «Скачать GeoTIFF».

Готовый файл появляется в `data/jobs` и сразу скачивается браузером.

## API

```text
GET  /api/health
GET  /api/tiles.geojson?bbox=west,south,east,north
POST /api/estimate
POST /api/jobs
GET  /api/jobs/{id}
GET  /api/jobs/{id}/download
POST /api/parse-file
```

Swagger: <http://127.0.0.1:8000/docs>.

Пример запроса:

```json
{
  "mode": "bbox",
  "bbox": [37.3, 55.5, 37.9, 55.9],
  "crs": "EPSG:4326",
  "filename": "moscow"
}
```

Для режима плитки:

```json
{
  "mode": "tile",
  "tile": "N055E037"
}
```

Для полигона:

```json
{
  "mode": "polygon",
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[37.1, 55.1], [37.9, 55.1], [37.9, 55.9], [37.1, 55.9], [37.1, 55.1]]]
  },
  "mask": true
}
```

## Настройки

Переменные окружения:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `FABDEM_MAX_TILES` | `9` | максимум плиток 1° на задачу |
| `FABDEM_MAX_FILE_BYTES` | `524288000` | лимит оценочного размера файла |
| `FABDEM_MAX_UPLOAD_BYTES` | `10485760` | лимит KML/GeoJSON |
| `FABDEM_MAX_WORKERS` | `2` | одновременно обрабатываемых задач |
| `FABDEM_SOURCE_BASE_URL` | data.bris | официальный источник ZIP |
| `FABDEM_TILE_MIRROR_URL` | пусто | необязательное зеркало готовых плиток 1° |
| `FABDEM_TILE_INDEX_URL` | data.bris | индекс плиток |
| `FABDEM_BASE_DIR` | папка проекта | куда писать `data/` |

Официальный источник может быть медленным. Для ускорения можно указать
прямое зеркало готовых плиток, например:

```bat
set FABDEM_TILE_MIRROR_URL=https://huggingface.co/buckets/links-ads/fabdem/resolve
```

При отсутствии зеркала используется официальный ZIP через HTTP Range.
Скачанные плитки кэшируются, поэтому повторные запросы быстрее.

## Тестирование

Установите dev-зависимости:

```bat
.venv\Scripts\python -m pip install -e ".[dev]"
```

Быстрые тесты (без сети):

```bat
.venv\Scripts\python -m pytest -q
```

Интеграционный тест с реальным источником FABDEM:

```bat
set RUN_NETWORK_TESTS=1
.venv\Scripts\python -m pytest -q -m integration
```

Проверка синтаксиса frontend:

```bat
node --check app\static\app.js
```

Тесты покрывают:

- имена и границы плиток, отрицательные координаты, 180-й меридиан;
- поиск пересекающихся плиток;
- склейку, обрезку, маску и перепроецирование GeoTIFF;
- KML, KMZ, GeoJSON;
- API, очередь задач и скачивание;
- реальное получение маленькой плитки из data.bris.

## Ограничения

- Полигон, пересекающий 180-й меридиан, пока не поддерживается. Прямоугольник
  через 180° поддерживается и отдаётся ZIP-архивом из двух GeoTIFF.
- Нет авторизации и публичных лимитов по IP: проект рассчитан на локальный запуск.
- Список задач хранится в памяти процесса: после перезапуска сервера ссылки на старые
  задачи пропадают, но готовые файлы остаются в `data/jobs` до очистки.
- Для карты нужен интернет: подложка загружается с OpenStreetMap.
- Полный набор FABDEM не хранится локально. Кэш плиток растёт по мере использования,
  его можно очистить вручную: `data/cache/tiles`.
- Готовые файлы старше 24 часов удаляются при следующем запуске.

Веб-интерфейс дополнительно проверялся через Playwright: загрузка карты, клик по
плитке, выбор bbox, оценка размера, скачивание GeoTIFF и загрузка KML.

## Лицензия данных

FABDEM V1-2 распространяется по лицензии
**CC BY-NC-SA 4.0**: некоммерческое использование, обязательная атрибуция,
ShareAlike. DOI: [10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn](https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn).

Это неофициальный сервис. Он не связан с University of Bristol.

**Реклама и платный доступ к данным несовместимы с некоммерческой лицензией.**
Если нужна коммерческая версия, запросите разрешение у правообладателя или
используйте другой DEM.

При распространении производных тайлов сохраняйте лицензию и атрибуцию:

> FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and
> © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the
> European Union and ESA; all rights reserved.

## Структура

```text
app/
  main.py              FastAPI, страницы и API
  config.py            настройки и пути
  models.py            Pydantic-модели запросов
  services/
    geo.py             геометрия, имена плиток, площадь
    catalog.py         индекс плиток и архивы
    source.py          получение плиток из ZIP / зеркала
    processor.py       склейка, маска, CRS, GeoTIFF
    areas.py           валидация области и лимиты
    jobs.py            фоновая очередь задач
    kml.py             KML/KMZ/GeoJSON
  templates/index.html
  static/
    app.js             карта, рисование, оценка, скачивание
    style.css
    vendor/            Leaflet и Leaflet-Geoman
tests/
data/                  создаётся автоматически
run.bat
```

## Сторонние библиотеки

- Leaflet — BSD-2-Clause;
- Leaflet-Geoman — MIT;
- FastAPI, rasterio/GDAL, Shapely, pyproj, fastkml, remotezip — согласно их лицензиям.
