# Шрифты

Оба семейства распространяются под SIL Open Font License 1.1 — вшивать их
в PDF и перераспространять вместе со скиллом можно, продавать отдельно нельзя.

| Семейство | Роль | Автор | Лицензия |
|---|---|---|---|
| Geist Sans | основной текст | Vercel в соавторстве с basement.studio | `geist-sans/OFL.txt` |
| Geist Mono | код, команды, колонтитулы | Vercel в соавторстве с basement.studio | `geist-mono/OFL.txt` |
| Montserrat | заголовки | The Montserrat Project Authors | `montserrat/OFL.txt` |

Свои шрифты подставляются через `brand.yaml`: положи файлы в
`assets/fonts/<имя-папки>` и укажи её в секции `fonts`. Генератор ждёт те же
имена файлов, что у текущих семейств.
