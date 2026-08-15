# agent-toolkit

Набор скиллов, плагинов и настроек для Claude Code. Подключается к любому проекту, где нужен.

## Что внутри

| | Количество |
|---|---|
| Скиллы | 105 |
| Плагины | 32 |
| MCP-серверы | 1 |
| Субагенты | 5 |

Из скиллов активны 7, остальные 98 спят и вызываются по имени. Из плагинов работает 1. Почему так устроено и как это менять, написано в [SKILLS.md](SKILLS.md).

## Быстрый старт

Напиши в чат слэш и имя скилла:

```
/impeccable
```

Полный список открывается, если набрать один слэш.

## Как подключить к другому проекту

**Локально в терминале:**

```bash
claude --add-dir /путь/к/agent-toolkit
```

**В облачной сессии:** попроси Claude подключить репозиторий `agent-toolkit`, и скиллы станут доступны в текущем разговоре.

## Структура

```
.claude/skills/        скиллы (89 симлинков в .agents/skills плюс свои папки)
.claude/agents/        субагенты
.claude/settings.json  маркетплейс плагинов и список включённых
.mcp.json              MCP-серверы
skills-lock.json       источники и хеши установленных скиллов
CLAUDE.md              правила работы над кодом
SKILLS.md              инструкция по вызову
```

## Откуда собрано

| Источник | Скиллов |
|---|---|
| `coreyhaines31/marketingskills` | 49 |
| `muratcankoylan/agent-skills-for-context-engineering` | 17 |
| `Leonxlnx/taste-skill` | 12 |
| `emilkowalski/skills` | 9 |
| `nextlevelbuilder/ui-ux-pro-max-skill` | 7 |
| `N1arko/redaktura-skills` | 6 |
| `pbakaus/impeccable` | 1 |
| `AgriciDaniel/banana-claude` | 1 |
| `hardikpandya/stop-slop` | 1 |
| `wshuyi/remotion-video-skill` | 1 |
| `tenfoldmarc/llm-council-skill` | 1 |

Плагины из маркетплейса `fcakyon/claude-codex-settings`.

## Ключи

В репозиторий не кладём. В облачных сессиях они живут в поле Environment variables в настройках окружения, локально задаются через `claude mcp add --env`.
