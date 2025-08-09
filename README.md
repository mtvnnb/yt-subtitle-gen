# YouTube Subtitle Downloader

This application allows you to download and re-process subtitles from YouTube videos.

## Features

- Download subtitles from YouTube videos.
- Re-process local `.srt` files.
- Splits subtitles into logical sentences.
- GUI built with PySide6.

## How to Run

### 1. Install `uv`

`uv` is an extremely fast Python package installer and resolver, written in Rust. It's a drop-in replacement for `pip` and `pip-tools` workflows.

**macOS and Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Create a Virtual Environment

```bash
uv venv
```

This will create a `.venv` directory in your project folder.

### 3. Activate the Virtual Environment

**macOS and Linux:**

```bash
source .venv/bin/activate
```

**Windows:**

```powershell
.venv\Scripts\activate
```

### 4. Install Dependencies

```bash
uv pip install -r requirements.txt
```

### 5. Run the Application

```bash
uv run main.py
```