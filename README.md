# amanu-win

> **Unofficial Windows functional equivalent of [Amanu](https://github.com/gsamat/amanu)** —
> a local-first meeting recorder. Records mic + system audio as separate channels,
> transcribes locally with faster-whisper, summarizes with a local Ollama model,
> and pastes your own words into the focused window automatically.
> Concept and design credit: Amanu by @gsamat (MIT). This is an independent
> Python implementation — not affiliated with the original project.

## Что это

Неофициальный порт идеи Amanu (macOS) на Windows. Одна программа в трее:
- **Запись встречи в два канала** — микрофон (левый) и системный звук / собеседник (правый) через WASAPI loopback, без бота в звонке.
- **Потоковая транскрипция микрофона** — речь чанкуется вживую (первый фрагмент 0–10 с, далее шаг 8 с с пересечением 2 с) и распознаётся прямо во время записи; по остановке остаётся только хвост.
- **LLM-сшивка фрагментов** — локальная LLM получает фрагменты вместе с точным расписанием их получения, удаляет дубли на пересечениях и восстанавливает пунктуацию/заглавные; при сбое LLM — fail-safe наивная склейка.
- **Локальная транскрипция** — faster-whisper на GPU (CUDA) или CPU; каналы распознаются по отдельности, поэтому атрибуция говорящих бесплатна.
- **Локальное резюме** — тема, решения, действия, открытые вопросы через Ollama.
- **Автовставка своей речи** — сшитый текст вставляется в окно с текстовым курсором почти сразу после остановки записи (SuperWhisper-style).
- **Папка на встречу** — `audio.wav`, `transcript.md`, `transcript.json` (с фрагментной provenance), `summary.md`, `meta.json`. Папка и есть база данных, как в оригинале.

Ничего не уходит в облако: запись, транскрипция, пунктуация и резюме — локально.

## Установка из исходников

Требуется: Windows 10/11 x64, Python 3.10+, при желании NVIDIA GPU (без него — CPU-транскрипция).

```powershell
git clone https://github.com/dervish54/amanu-win.git
cd amanu-win
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m amanu_win --setup   # одноразово: Ollama + модели + автозапуск
.venv\Scripts\python.exe -m amanu_win           # трей-приложение
```

`--setup` идемпотентен: ставит Ollama через winget (если нет), скачивает модель резюме
(`ollama pull qwen2.5:7b`), прогревает кэш whisper-модели, создаёт конфиг и ярлык в
автозагрузке (`--no-startup` — без автозапуска). В репозитории нет ни моделей, ни
бинарников — всё скачивается тут.

После `--setup` в `~/.local/bin` появляется шим `amanu` — CLI из любого терминала: `amanu --doctor`, `amanu --process <папка>`.

## Использование

- **Ctrl+Alt+R** — начать/остановить запись (красный/зелёный значок в трее; янтарный — идёт обработка: «transcribing…», «summarizing…»).
- После остановки: транскрипция → пунктуация → автовставка вашей речи в текущее окно → резюме.
- Результаты: `%USERPROFILE%\Recordings\<дата-время>\` (переопределяется `recordings_dir`).

CLI:

```powershell
python -m amanu_win --doctor           # устройства, GPU, Ollama
python -m amanu_win --process <папка>  # дообработать встречу вручную
python -m amanu_win --setup            # повторная установка/диагностика
```

## Сборка .exe

```powershell
.venv\Scripts\python.exe scripts\build_exe.py          # dist\amanu\amanu.exe
.venv\Scripts\python.exe scripts\build_exe.py --out D:\build
```

Бандл portable: на машине без NVIDIA транскрипция уходит на CPU.

## Конфигурация

`%USERPROFILE%\.config\amanu\config.json` — файл только с отличиями от дефолтов:

```json
{
  "recordings_dir": "D:\\Recordings",
  "hotkey": "ctrl+alt+r",
  "keep_audio": true,
  "paste": { "enabled": true },
  "punctuation": { "enabled": true },
  "transcription": {
    "model": "large-v3-turbo",
    "device": "cuda",
    "language": "auto",
    "mic_speaker": "Микрофон",
    "system_speaker": "Собеседник"
  },
  "summary": {
    "ollama_model": "qwen2.5:7b",
    "ollama_url": "http://localhost:11434",
    "language": "ru"
  }
}
```

## Ограничения по сравнению с оригинальным Amanu (macOS)

- Нет автоопределения начала встречи (управление только горячей клавишей/треем).
- Нет контекста из календаря и имён участников.
- Нет эхоподавления и диаризации внутри канала (атрибуция — по каналам).
- Bluetooth-гарнитура в stereo-режиме (A2DP) не отдаёт микрофон — включайте hands-free профиль.

## Тесты

```powershell
.venv\Scripts\python.exe -m pytest tests\
```

34 теста, включая регрессии на реальные баги (растяжение времени записи, автоповтор горячей клавиши, межпоточное обновление трея, крах на пустых каналах).

## Благодарности и лицензия

- [Amanu](https://github.com/gsamat/amanu) (MIT) — концепция, архитектурные решения, формат артефактов.
- Оригинальный Amanu — форк [digimata/quill](https://github.com/digimata/quill).

MIT, см. [LICENSE](LICENSE).
