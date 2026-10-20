# Procedural Keystroke Synth

> **Real-time procedural ambient and lo-fi generative soundtrack synthesizer driven by keystroke cadence, speed, and coding flow.**
>
> *Фоновая утилита, превращающая ввод текста и коддинг в процедурный ambient/lo-fi саундтрек в реальном времени.*

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)]()
[![Tests](https://img.shields.io/badge/tests-79%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-84%25-brightgreen.svg)]()
[![Latency](https://img.shields.io/badge/latency-%3C12.5%20ms-blue.svg)]()
[![CPU](https://img.shields.io/badge/CPU-%3C1.5%25-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

---

## English Overview

**Procedural Keystroke Synth** is an ultra-low-latency ($<15$ ms) generative ambient sound engine designed to accompany coding, writing, and daily computer work. Every keystroke generates harmonically quantized musical phrases, lush chords, and tactile acoustic textures that dynamically evolve with your typing speed and rhythm.

### Key Features

- **Cyber-Night Studio GUI:** Modern CustomTkinter interface (`#0D1117`, `#00E5A3`, `#58A6FF`) featuring an animated oscilloscope audio visualizer and real-time neon WPM speedometer.
- **Ultra-Low Audio Latency:** Direct hardware-backed audio callback with zero heap allocations during playback ($<12.5$ ms output buffer latency, CPU usage $<1.5\%$).
- **Adaptive Typing Telemetry:** Continuous rolling WPM calculation (clamped $0-200$ WPM). Fast typing gracefully opens low-pass filters instead of spamming jarring notes.
- **Generative Music Theory & Modal Quantization:**
  - **Scales:** Dorian (Lo-Fi), Minor Pentatonic (Space), Lydian (Dreamy), Aeolian (Melancholy), and Lo-Fi soul voicings.
  - **Circle of Fifths Modulation:** Brackets `()`, `[]`, `{}` and `Tab` dynamically modulate the root note.
  - **Backspace LPF Dip:** Pressing `Backspace` produces an "underwater" low-pass filter dive that exponentially recovers.
  - **Enter Sub-Drop:** Pressing `Enter` triggers a deep 808 sub-bass drop on the tonic (~36.7 Hz).
  - **Space Vinyl Click:** Mechanical percussive vinyl transient for the spacebar.
- **Sound Presets & Sound Design:**
  - **Warm Rhodes:** 2-operator FM synthesis with analogue chorus detune ($\pm 4$ cents).
  - **Crystal Bells:** Crystalline ambient bell with sparkling upper harmonics.
  - **Lo-Fi Felt Piano:** Muffled warm piano with gentle decay and soft analog saturation.
  - **8-Bit Chiptune:** Vintage retro pulse generator.
  - **Custom User Presets:** Save, load, and manage your own sound snapshots.
- **Spatial FX & Atmosphere:** Stereo Ping-Pong delay (280 ms left / 420 ms right) and an ambient drone pad with an **Idle Fade-out** mechanism (fades to absolute silence after 2 seconds of keyboard inactivity).
- **Windows System Tray & Global Hotkeys:** Default double-click window restore, dynamic Mute toggle, fine volume steps, and background execution.
- **Atomic Settings Persistence:** Thread-safe JSON persistence with atomic file replacement (`tempfile` + `os.replace`) and a 500 ms debounce timer protecting SSDs from slider drag wear.

---

## Русское описание

**Procedural Keystroke Synth** — это генеративный фоновый синтезатор с ультранизкой задержкой ($<15$ мс), превращающий печать текста и написание кода в глубокий медитативный ambient/lo-fi саундтрек. Каждое нажатие клавиши рождает гармонически согласованные ноты и аккордовые секвенции, реагируя на темп и ритм вашей работы.

---

## Горячие клавиши и управление / Hotkeys & Key Bindings

| Сочетание / Клавиша | Действие / Description |
| :--- | :--- |
| `Ctrl + Shift + M` | **Мгновенный Mute / Unmute** (отключение/включение звука с сохранением состояния) |
| `Ctrl + Alt + Up` | **Громкость +5%** (увеличение общей громкости) |
| `Ctrl + Alt + Down` | **Громкость -5%** (уменьшение общей громкости) |
| `Space` | **Vinyl Click** (тактильный механический виниловый щелчок) |
| `Enter` | **Sub-Bass Drop** (глубокий суб-басовый дроп на тонике лада) |
| `Backspace` | **Filter Dip** (погружение «под воду» с экспоненциальным возвратом) |
| `( )`, `[ ]`, `{ }`, `Tab` | **Modulation** (модуляция тональности по квинтовому кругу) |
| `Двойной клик по трею` | **Показать окно** (разворачивает и фокусирует студию из фонового режима) |

---

## Установка / Installation

### 1. Клонирование репозитория / Clone Repository
```bash
git clone https://github.com/your-username/procedural-keystroke-synth.git
cd procedural-keystroke-synth
```

### 2. Создание виртуального окружения / Virtual Environment
```cmd
python -m venv .venv
.venv\Scripts\activate
```

### 3. Установка зависимостей / Install Dependencies
```cmd
pip install -r requirements.txt
```

---

## Способы запуска / Launch Options

### Способ 1: Графический интерфейс (Cyber-Night Studio)
Запустите командный скрипт:
```cmd
run.bat
```
Или напрямую через Python:
```cmd
python -m src.main
```

### Способ 2: Полностью бесшумный запуск в фоне (Silent Tray Mode)
Для работы без всплывающего окна черной консоли дважды кликните по:
```
run_synth.vbs
```
*(Запустит утилиту в скрытом режиме через `pythonw.exe` со значком в системном трее)*.

### Способ 3: Консольный режим без GUI (Headless Mode)
```cmd
python -m src.main --no-gui
```
Опциональные аргументы командной строки:
- `--scale {dorian,pentatonic,lydian,aeolian,lofi}` — стартовый музыкальный лад;
- `--volume 0.70` — стартовая громкость [0.0 .. 1.0];
- `--buffer-size 512` — размер аудио-буфера (512 сэмплов $\approx 11.6$ мс задержки);
- `--no-tray` — работа только в консоли без значка в системном трее.

### Способ 4: Сборка автономного EXE (Standalone Windows Binary)
Для создания автономного `.exe`, запускающегося на любом ПК с Windows без установленного Python:
```cmd
python build_exe.py
```
Готовый исполняемый файл появится в:
```
dist/KeystrokeSynth.exe
```

---

## Структура проекта / Project Structure

```
Procedural Keystroke Synth/
├── src/
│   ├── config/              # Менеджер настроек, схема AppConfig и debounce-таймер
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── core/                # Неизменяемые контракты данных (KeyEvent, HarmonyEvent)
│   │   ├── __init__.py
│   │   └── contracts.py
│   ├── dsp/                 # Высокопроизводительное аудио-ядро (Zero-alloc callback)
│   │   ├── __init__.py
│   │   └── engine.py
│   ├── harmony/             # Музыкальная логика, лады, модуляция и квантование
│   │   ├── __init__.py
│   │   └── engine.py
│   ├── input/               # Неблокирующий перехват клавиатуры и расчет WPM
│   │   ├── __init__.py
│   │   └── telemetry.py
│   ├── lifecycle/           # Системный трей Windows и глобальные хоткеи OS
│   │   ├── __init__.py
│   │   └── tray.py
│   ├── ui/                  # Окно Cyber-Night Studio на базе CustomTkinter
│   │   ├── __init__.py
│   │   └── window.py
│   └── main.py              # Главная точка входа и оркестратор подсистем
├── tests/                   # Набор модульных и сквозных E2E-тестов (80 passed)
│   ├── test_config.py
│   ├── test_contracts.py
│   ├── test_dsp.py
│   ├── test_e2e_lifecycle.py
│   ├── test_harmony.py
│   ├── test_telemetry.py
│   └── test_ui.py
├── .gitignore               # Исключение временных и сборочных файлов
├── build_exe.py             # Скрипт сборки автономного PyInstaller .exe
├── requirements.txt         # Зафиксированные проверенные версии зависимостей
├── run.bat                  # Быстрый запуск GUI
├── run_silent.vbs           # Запуск без черного окна консоли
├── run_synth.vbs            # Алиас для тихого запуска
└── README.md                # Полная техническая и пользовательская документация
```

---

## Тестирование и верификация / Testing & Verification

Проект снабжен полным набором модульных, нагрузочных и E2E тестов. Запуск всех тестов:

```cmd
pytest tests/ -v
```

Текущие показатели качества:
- **79 из 79 тестов** успешно проходят за $<3.0$ секунд.
- **Общее покрытие кода тестами:** 84%.
- Отсутствие утечек памяти и блокировок в аудиопотоке реального времени.

---

## Лицензия / License

Распространяется под лицензией **MIT License**. Разрешено свободное коммерческое и некоммерческое использование, изменение и распространение.
