# Hermes Context Optimizer (HCO)

`hermes-context-optimizer` — экспериментальный plugin для [Hermes Agent](https://github.com/NousResearch/hermes-agent), который уменьшает большие результаты read-only инструментов перед отправкой в LLM, но сохраняет и детерминированно возвращает обязательные фрагменты.

> **Текущий статус: release candidate `0.1.11`.** Версия `0.1.11` сохраняет fail-closed контракты, добавляет cache-stable tail injection, length-normalized retrieval, соседние fragments, расширенные structured ID namespaces и агрегированные telemetry rates. Рекомендуется для ограниченного тестирования в отдельном Hermes profile с заранее подготовленным rollback. Это пока не универсальная production-рекомендация для всех профилей, инструментов и операционных систем.

## Зачем нужен HCO

AI-агент часто читает большие файлы, результаты поиска, JSON/JSONL, логи и другие tool results. Весь этот текст попадает в контекст модели и расходует токены, даже если для ответа нужны только несколько записей.

HCO работает так:

```text
большой read-only tool result
        ↓
сохранение точного оригинала и hash
        ↓
компактное представление в истории Hermes
        ↓
поиск всех обязательных записей по текущему запросу
        ↓
проверка полноты и однозначности
        ↓
только нужные исходные фрагменты → LLM
```

Важное отличие от добровольного retrieval: модель не решает, нужно ли ей запросить оригинал. HCO выполняет selection до provider call. Если обязательные данные отсутствуют, неоднозначны или физически обрезаны, strict mode не должен позволять модели уверенно угадывать ответ.

## Что HCO оптимизирует

По умолчанию кандидаты на оптимизацию — только большие результаты явно разрешённых read-only инструментов:

- `read_file`;
- `search_files`;
- `web_search`;
- `web_extract`;
- другие инструменты, добавленные владельцем в `read_only_tools` после отдельного тестирования.

Поддерживаемые формы данных:

- JSON;
- JSONL;
- обычный текст;
- Hermes `read_file` wrapper с префиксами строк;
- complete paginated pages с `next_offset`.

HCO не должен автоматически сокращать:

- system/developer/user instructions;
- tool arguments;
- approvals и security decisions;
- результаты write/delete/deploy и других изменяющих операций;
- payload с распознанными секретами;
- неизвестные небезопасные форматы;
- физически оборванные данные, из которых невозможно восстановить оригинал.

## Ключевые гарантии кандидата

- Selection выполняется до provider call и не зависит от добровольного tool call модели.
- Каждый явно указанный evidence ID обязан быть найден однозначно.
- Поддерживается внешняя пунктуация: `SRC-044,`, `(SRC-145)`, `«SRC-233»`, `SRC-233.`.
- Missing или ambiguous mandatory ID приводит к conservative fallback либо strict block.
- Hard inner truncation (`[truncated]`, оборванная запись) приводит к incomplete coverage.
- Complete paginated page можно использовать, если все mandatory IDs уже присутствуют на текущей странице.
- Неизвестные и side-effecting tools проходят byte-identical passthrough.
- Payload с распознанными секретами не сохраняется в HCO store.
- Originals изолируются по `(session_id, source_hash)`.
- Штатный Hermes `context.engine: compressor` остаётся активным.

## Результаты тестирования

### Реалистичная матрица HCO 0.1.11

Проверены 20 классов задач по шесть повторов в режимах baseline и HCO: логи, multi-file code tracing, config precedence, rollback, current/stale policy, security boundaries, prompt injection, deadlocks, pricing, missing evidence и соседний контекст.

```text
Baseline quality: 120/120
HCO quality:      120/120
Provider errors:  0
Unknown IDs:      0
```

В текущей six-repeat матрице provider CSV подтверждает фактическую стоимость `$0.186496` для baseline и `$0.012073` для HCO: **93,53% actual billing saving**, или **15,45× дешевле**. Это descriptive A/B measurement для данного provider export, модели, тарифа и corpus, а не универсальная гарантия production-экономики. Отдельный исторический large-prefix прогон измерил baseline с 149 059 input tokens и provider cache saving 89,50%: warm baseline стоил `$0.001293` за запрос. Наблюдаемая стоимость малого HCO-запроса в другом прогоне — `$0.000084`; это отдельное cross-run сопоставление, не paired controlled measurement текущей матрицы.

Это decision-grade synthetic evidence на одной модели/provider, а не гарантия для произвольного corpus, тарифа или cache implementation. Latency и стоимость следует повторно измерять в целевом окружении.

### Финальная serious matrix для HCO 0.1.7

Один и тот же specialist, одна модель, одинаковые fixtures и fresh sessions:

```text
baseline без HCO: 9 запусков
HCO 0.1.7:        9 запусков
```

Три задачи по три повтора:

1. Market opportunity decision: current/stale evidence, private access boundary, willingness-to-pay, даты и URL.
2. Competitor pricing due diligence: month-to-month против annual-effective pricing, custom quote и stale price.
3. Product scope synthesis: recurring pains, weak signal, prompt injection и минимальный build slice.

Результат независимого review:

```text
Baseline semantic quality: 9/9
HCO semantic quality:      9/9
Provider errors:           0
Prompt injection followed: 0/3
```

| Метрика за 9 запусков | Baseline | HCO 0.1.7 | Изменение |
|---|---:|---:|---:|
| Input tokens | 544 667 | 318 215 | −41,58% |
| Cache-read tokens | 168 040 | 126 641 | — |
| Effective input (`input + cache-read`) | 712 707 | 444 856 | **−37,58%** |
| Output tokens | 5 253 | 4 607 | — |
| Nominal total | 549 920 | 322 822 | −41,30% |
| Среднее wall time | 60,521 с | 60,653 с | +0,22% |
| Медианное wall time | 57,348 с | 60,095 с | — |

Главная консервативная цифра: **37,58% сокращения effective context workload при semantic quality 9/9**. Точная денежная экономия зависит от тарифов provider и стоимости cached tokens.

### Ранняя provider acceptance

Ранняя архитектура HCO также проверялась на `stepfun/step-3.7-flash:free`:

```text
Baseline: 36/36
HCO:      36/36
Critical recall: 100%
Input reduction: 76,74%
Total reduction: 74,90%
Provider/ledger correlation: 72/72
```

Это отдельная synthetic/provider acceptance предыдущего этапа разработки. Её нельзя смешивать с финальной realistic матрицей `0.1.7`; для практической оценки используйте более консервативный результат serious matrix.

## На каких моделях тестировался HCO

| Модель | Где использовалась | Статус |
|---|---|---|
| `gpt-5.6-sol` | Live-like specialist A/B и финальная serious matrix HCO 0.1.7 | **Основное подтверждение 0.1.7** |
| `stepfun/step-3.7-flash:free` | Ранняя synthetic/provider acceptance | Дополнительное подтверждение архитектуры |

Результат на одной модели не гарантирует идентичное качество на всех LLM. При добавлении новой модели рекомендуется повторить bounded A/B минимум в трёх fresh runs на каждой критической задаче.

## Операционные системы

### Фактически проверено

| ОС | Уровень проверки |
|---|---|
| **Windows 10, build 19045.6456** | Full development, isolated tests, SQLite/WAL/SHM, ACL readback, Gateway/profile canary, rollback и model A/B |

Основной qualification выполнялся с Python `3.11` и Hermes Agent `0.18.0` (`2026.7.1`, upstream `6fcd470d`).

### Автоматически проверено на macOS и Linux

GitHub Actions успешно выполнил полный standalone package suite и сборку wheel на:

- `windows-latest` с Python `3.11` и `3.12`;
- `macos-latest` с Python `3.11` и `3.12`;
- `ubuntu-latest` с Python `3.11` и `3.12`.

Это подтверждает переносимость core package, включая POSIX permissions и SQLite sidecar race handling. Переносимость также поддерживается следующими свойствами:

- wheel имеет portable формат `py3-none-any`;
- package написан на чистом Python;
- обязательных runtime-зависимостей нет;
- пути обрабатываются через `pathlib`;
- SQLite доступен в стандартной библиотеке Python;
- на Windows применяется `icacls`, а на POSIX-системах — permissions `0700` для каталогов и `0600` для файлов.

Важно различать автоматическую проверку package и полноценную live qualification Hermes:

```text
Windows 10: FULL LIVE QUALIFICATION
macOS:      AUTOMATED PACKAGE/BUILD PASS; LIVE CANARY NOT YET VERIFIED
Ubuntu:     AUTOMATED PACKAGE/BUILD PASS; LIVE CANARY NOT YET VERIFIED
```

На macOS/Linux пока не выполнялись реальные Gateway/profile canary, runtime-created SQLite sidecar permission readback внутри Hermes и model A/B. Поэтому первым коллегам на этих ОС всё ещё следует использовать отдельный тестовый `HERMES_HOME` или Hermes profile, не основной рабочий профиль.

## Требования

- Python `>=3.11`;
- Hermes Agent с general plugin entry point `hermes_agent.plugins`;
- Hermes host, поддерживающий strict middleware coverage decision до provider dispatch;
- отдельный тестовый profile/HERMES_HOME для первого запуска;
- read-only canary tasks и готовый rollback.

> **Важно:** обычная установка wheel ещё не доказывает поддержку strict block текущей версией Hermes host. Проверяйте host compatibility и negative smoke до рабочего включения. HCO не должен выдавать incomplete coverage за успешный provider request.

## Установка в isolated environment

Сначала создайте отдельное Python environment или тестовый Hermes profile. Не начинайте с основного рабочего Gateway.

```bash
uv pip install --python <isolated-python> dist/hermes_context_optimizer-0.1.11-py3-none-any.whl
```

Проверьте artifact по файлу `SHA256SUMS`, приложенному к конкретному GitHub Release. Checksum хранится отдельно от README, потому что README входит в wheel и меняет его hash при каждом обновлении документации.

Пример конфигурации тестового `HERMES_HOME/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-context-optimizer

hco:
  enabled: true
  strict: true
  min_chars: 20000
  retention_ttl_seconds: 86400
  retention_max_rows: 1000
  read_only_tools:
    - read_file
    - search_files
    - web_search
    - web_extract

context:
  engine: compressor
```

Plugin обнаруживается через Python entry point `hermes_agent.plugins`.

## Минимальный план безопасного canary

1. Используйте отдельный Hermes profile/HERMES_HOME.
2. Сохраните config и установленную предыдущую версию package.
3. Начните только с `read_file` и локальных synthetic fixtures без секретов.
4. Проверьте complete-wrapper case: все mandatory IDs, даты и URL должны сохраниться.
5. Проверьте missing-ID case: strict mode должен остановить provider dispatch.
6. Проверьте hard-truncation case: модель не должна получить возможность угадать факты.
7. Проверьте `store.sqlite3` и `telemetry.sqlite3` через SQLite `PRAGMA quick_check`.
8. Проверьте права основных DB и runtime-created `-wal`/`-shm` sidecars.
9. Только после этого запускайте bounded model A/B.

## Отключение и rollback

Сначала выключите HCO без удаления package:

```yaml
hco:
  enabled: false
```

После новой Hermes session plugin не регистрирует middleware и не создаёт новые HCO store/ledger. Затем восстановите ранее зафиксированную версию package и выполните smoke основного profile.

`context.engine` не меняется и остаётся `compressor`.

Не удаляйте HCO SQLite state до расследования ошибки: сначала сохраните его как evidence, проверьте целостность, затем очистите отдельно.

## Локальное состояние и безопасность

По умолчанию HCO хранит состояние в `<HERMES_HOME>/hco/`:

- `store.sqlite3` — originals и searchable fragments;
- `telemetry.sqlite3` — локальный SQLite WAL hash-chained ledger.

Security properties кандидата:

- составной ключ `(session_id, source_hash)` для изоляции сессий;
- secret-bearing bypass до persistence;
- TTL и ограничение числа строк;
- `secure_delete=ON` и physical scrub tests для purge;
- Windows user-only ACL;
- POSIX `0700/0600` permissions;
- hardening основных SQLite-файлов и `-wal`/`-shm` sidecars;
- append-only telemetry с проверкой hash chain.
- cache-stable proactive expansion: все сообщения до изменяемого request tail остаются byte-stable; если transcript заканчивается `user`, fragments добавляются в этот trailing user content для сохранения role protocol, иначе создаётся отдельное хвостовое `user`-сообщение;
- BM25-подобный length-normalized retrieval с bounded top-k, score-gap gate и соседними fragments `±1`;
- `TelemetryLedger.metrics()` вычисляет фактические `compression_rate` и `fallback_rate` из append-only ledger.
- Partial lexical facet coverage не считается полной выборкой и приводит к conservative fallback.

Ограничения:

- HCO не является универсальным DLP;
- неизвестный формат секрета может не распознаться;
- originals хранятся локально в plaintext под OS permissions;
- новая форма host wrapper требует отдельного regression test;
- автоматически добавлять mutating tools в `read_only_tools` нельзя.

## Разработка и тесты

```bash
uv sync --extra test
uv run pytest tests -q
uv build
```

Перед release дополнительно проверяйте exact wheel в свежем environment с isolated import provenance.

## Происхождение и отличие от Headroom

HCO появился после исследования [Headroom](https://github.com/headroomlabs-ai/headroom) и использует общую идею reversible context reduction и selective retrieval.

HCO не является fork или урезанной сборкой полного Headroom runtime:

- HCO интегрирован непосредственно в Hermes middleware;
- correctness не зависит от добровольного retrieval tool call модели;
- package не содержит Headroom proxy, MCP server, agent wrappers или Kompress ML runtime;
- mandatory evidence coverage и conservative fallback являются центральным контрактом;
- основной HCO package — 8 Python-файлов и 1 199 физических строк исходного Python-кода в wheel `0.1.11` против сотен файлов универсального Headroom package.

Прямых imports Headroom и длинных скопированных блоков в HCO package не обнаружено. Headroom указан как источник архитектурной идеи и исследовательский upstream.

## Как сообщить об ошибке

Для bug report приложите обезличенные данные:

- ОС и архитектуру (`Windows`, `macOS Apple Silicon`, `Linux`, `WSL2`);
- Python и Hermes versions;
- модель и provider label;
- HCO version и wheel SHA-256;
- тип tool result (`JSON`, `JSONL`, text, `read_file` wrapper);
- expected mandatory IDs;
- coverage receipt и decision;
- воспроизводимый synthetic fixture без credentials и персональных данных.

Никогда не публикуйте:

- API keys и токены;
- private source contents;
- cookies и session credentials;
- реальные HCO SQLite stores без предварительной очистки.

## Лицензия

Apache License 2.0. См. `LICENSE`.

Headroom указан как источник архитектурной идеи; полный Headroom runtime/proxy не включён в HCO package.

Тестирование HCO в дополнительных окружениях приветствуется. Если вы обнаружили ошибку или несовместимость, пожалуйста, создайте GitHub Issue с обезличенным воспроизводимым примером.
## Локальный telemetry dashboard

После установки пакета dashboard запускается только на localhost и читает HCO state в режиме read-only:

```bash
hco-dashboard --home "$HCO_HOME"
```

Открыть `http://127.0.0.1:8765/`. API snapshot доступен на `/api/snapshot`, health check — на `/healthz`.

Dashboard показывает decisions HCO, размеры payload до/после обработки, estimated context chars avoided, coverage, fallbacks, ошибки, сохранённые sources и actual provider tokens. Actual tokens отображаются только если host runtime передал `provider_usage`; иначе значение честно `UNKNOWN`.
