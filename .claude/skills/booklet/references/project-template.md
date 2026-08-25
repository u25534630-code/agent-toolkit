# Шаблон проекта лид-магнита

## Структура папки проекта

```
lead-magnet-projects/[slug]/
├── project.json               # Метаданные проекта
├── research/                  # Результаты исследований
│   ├── openai-result.md       # Deep Research (OpenAI)
│   ├── perplexity-result.md   # Deep Research (Perplexity)
│   ├── google-result.md       # Deep Research (Google)
│   └── web-search-notes.md    # Заметки из веб-поиска
├── content.md                 # Основной контент лид-магнита
├── layout-notes.md            # Опциональные заметки по HTML-оформлению
├── visuals.json               # Промпты схем и инфографики (создаёт команда visuals)
├── brand.yaml                 # Опционально: свой бренд только для этого проекта
├── screenshot-map.md          # Список скриншотов для автора
├── assets/
│   ├── illustrations/         # Схемы и инфографика от gpt-image-2
│   │   ├── put-ot-golosa.png
│   │   ├── sravnenie-podhodov.png
│   │   └── ...
│   └── screenshots/           # Скриншоты (добавляет автор)
│       ├── 01-interface.png
│       └── ...
└── output/
    ├── [slug].html            # Брендированное HTML-превью
    ├── [slug].pdf             # Финальный сжатый PDF
    └── .tmp/                  # Временные файлы: raw PDF, QA PNG, тесты сжатия; git игнорирует
```

---

## project.json — Шаблон

```json
{
  "name": "Название лид-магнита",
  "slug": "nazvanie-lid-magnita",
  "created": "2025-01-30",
  "language": "ru",

  "topic": "Подробное описание темы в 2-3 предложениях",
  "target_audience": {
    "who": "Кто целевая аудитория",
    "level": "Уровень знаний: новичок/средний/продвинутый",
    "pains": ["Боль 1", "Боль 2"],
    "desires": ["Желание 1", "Желание 2"]
  },
  "main_problem": "Главная проблема, которую решает материал",

  "format": "guide|checklist|tools|ideas|templates",
  "size": "short|medium|large",
  "estimated_pages": 15,

  "related_product": {
    "name": "Название курса/продукта",
    "link": "https://..."
  },

  "status": {
    "research": "not_started|in_progress|completed",
    "content": "not_started|in_progress|completed",
    "html": "not_started|draft|approved",
    "visuals": "not_started|in_progress|completed|blocked",
    "screenshots": "0/3",
    "pdf": "not_started|completed",
    "qa": "not_started|auto_checked|needs_fixes|passed",
    "cover": "not_started|completed"
  },

  "author": {
    "name": "Имя автора",
    "title": "Должность/Описание",
    "photo": "assets/author.jpg"
  }
}
```

---

## Команды для работы с проектом

### Deep Research
```bash
# Perplexity — базовый вариант
python tools/research-perplexity/deep_research.py "Тема исследования: [тема]. Целевая аудитория: [ЦА]. Ключевые вопросы: 1) ... 2) ... 3) ..." -o lead-magnet-projects/[slug]/research/perplexity.md

# OpenAI (быстро, o4-mini по умолчанию)
python tools/research-openai/deep_research.py "Тема..." -o lead-magnet-projects/[slug]/research/openai.md

# OpenAI с моделью o3 (качественно)
python tools/research-openai/deep_research.py "Тема..." -m o3 -o lead-magnet-projects/[slug]/research/openai-o3.md

# Gemini
python tools/research-google/deep_research.py "Тема..." -o lead-magnet-projects/[slug]/research/google.md
```

### Схемы и инфографика

Полный маршрут и правила промптов — `references/visuals-image2.md`.

```bash
# 1. собрать плейсхолдеры <!-- ДИАГРАММА: ... --> из content.md
uv run <SKILL>/scripts/visuals.py scan lead-magnet-projects/[slug]/content.md

# 2. заполнить поле prompt в visuals.json, затем сгенерировать
uv run <SKILL>/scripts/visuals.py generate lead-magnet-projects/[slug]/content.md

# 3. заменить плейсхолдеры на markdown-картинки
uv run <SKILL>/scripts/visuals.py apply lead-magnet-projects/[slug]/content.md

# перерисовать одну штуку
uv run <SKILL>/scripts/visuals.py generate lead-magnet-projects/[slug]/content.md --only put-ot-golosa --force
```

### Вставка изображений в content.md

```markdown
![Подпись, которая попадёт под изображение в PDF](assets/screenshots/01-interface.png "https://service.com")
```

Путь указывать относительно папки проекта. Title в кавычках попадёт в адресную строку browser-frame. Генератор HTML встроит локальное изображение в файл, поэтому итоговый PDF не зависит от внешних ассетов.

Скриншоты сайтов делать в едином размере `1440×760`, `fullPage: false`.

---

## Чек-лист создания лид-магнита

### Этап 1: Подготовка
- [ ] Создан проект через `/booklet new`
- [ ] Заполнен project.json с описанием ЦА

### Этап 2: Исследование
- [ ] Запущен Deep Research
- [ ] Проведён дополнительный веб-поиск (при необходимости)
- [ ] Собраны примеры и кейсы
- [ ] Найдены актуальные данные (цены, статистика)

### Этап 3: Контент
- [ ] Создана структура контента
- [ ] Написаны все разделы
- [ ] Добавлены примеры и таблицы
- [ ] Расставлены плейсхолдеры <!-- ДИАГРАММА: ... -->, минимум один, норма 2–4
- [ ] Составлен список скриншотов

### Этап 4: Визуальные материалы
- [ ] Заполнены промпты в visuals.json
- [ ] Сгенерированы схемы и инфографика, каждая просмотрена глазами
- [ ] Либо, если Replicate был недоступен: статус blocked, долг записан в pending и назван в отчёте
- [ ] Добавлены скриншоты (автор)
- [ ] Все изображения оптимизированы для мобильных

### Этап 5: PDF
- [ ] Создан HTML с фирменным дизайном из `brand.yaml`
- [ ] HTML согласован до PDF-экспорта
- [ ] Создан PDF из утверждённого HTML
- [ ] Проверено отображение на телефоне
- [ ] Добавлен брендинг и CTA
- [ ] Финальная вычитка

### Стандартные команды HTML → PDF

```bash
uv run .claude/skills/booklet/scripts/build_html.py \
  lead-magnet-projects/[slug]/content.md \
  -o lead-magnet-projects/[slug]/output/[slug].html

mkdir -p lead-magnet-projects/[slug]/output/.tmp

uv run .claude/skills/booklet/scripts/build_pdf.py \
  lead-magnet-projects/[slug]/output/[slug].html \
  -o lead-magnet-projects/[slug]/output/.tmp/[slug]-raw.pdf

uv run .claude/skills/booklet/scripts/compress_pdf.py \
  lead-magnet-projects/[slug]/output/.tmp/[slug]-raw.pdf \
  -o lead-magnet-projects/[slug]/output/[slug].pdf

uv run .claude/skills/booklet/scripts/qa_pdf.py \
  lead-magnet-projects/[slug]/output/[slug].pdf \
  -o lead-magnet-projects/[slug]/output/.tmp/qa-preview
```

Генератор по умолчанию использует вариант `standard`: локальные шрифты из скилла, крупные Montserrat-заголовки и обложку из `brand.yaml`.
