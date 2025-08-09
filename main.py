import os
import re
import sys
import requests
import xml.etree.ElementTree as ET
import yt_dlp

# Import the GUI library components
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QGroupBox, QTextEdit, QPushButton, QHBoxLayout,
                               QFileDialog, QMessageBox, QLineEdit)

# --- BACKEND LOGIC (Standalone Helper Functions) ---

def format_time_srt(seconds: float) -> str:
    if seconds < 0: seconds = 0
    millis = int((seconds - int(seconds)) * 1000)
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

def parse_time(time_str: str) -> float:
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    if '.' in parts[-1]:
        seconds_ms = parts[-1].split('.')
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(seconds_ms[0])
        millis = int(seconds_ms[1].ljust(3, '0'))
        return hours * 3600 + minutes * 60 + seconds + millis / 1000.0
    else:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        return hours * 3600 + minutes * 60 + seconds

def get_video_id(url: str) -> str | None:
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

def write_srt_file(entries: list, output_path: str):
    with open(output_path, "w", encoding="utf-8") as srt_file:
        for i, entry in enumerate(entries, 1):
            start_formatted = format_time_srt(entry['start'])
            end_formatted = format_time_srt(entry['end'])
            # Final safety check
            if entry['end'] < entry['start']: end_formatted = start_formatted
            srt_file.write(f"{i}\n{start_formatted} --> {end_formatted}\n{entry['content']}\n\n")

# --- WORKER CLASS for Threading ---

class Worker(QObject):
    log_message = Signal(str)
    finished = Signal()

    def __init__(self, tasks, output_dir):
        super().__init__()
        self.tasks = tasks
        self.output_dir = output_dir

    def enforce_no_overlap(self, entries: list):
        """
        NEW: Final cleanup pass to guarantee no timestamps overlap.
        """
        for i in range(len(entries) - 1):
            current_entry = entries[i]
            next_entry = entries[i+1]
            
            # If the end of the current entry is after the start of the next one...
            if current_entry['end'] > next_entry['start']:
                # ...trim the current entry's end time to match the next one's start time.
                current_entry['end'] = next_entry['start']

    def split_into_sentences(self, entries: list) -> list:
        final_entries = []
        sentence_pattern = r'([^.?!]+[.?!])'
        for entry in entries:
            content = entry['content'].strip()
            sentences = [s.strip() for s in re.findall(sentence_pattern, content)]
            if not sentences or len(sentences) <= 1:
                final_entries.append(entry)
                continue
            total_len = sum(len(s) for s in sentences)
            if total_len == 0: continue
            duration = entry['end'] - entry['start']
            current_time = entry['start']
            for sentence in sentences:
                sentence_len = len(sentence)
                sentence_duration = duration * (sentence_len / total_len) if duration > 0 and total_len > 0 else 0
                sentence_end_time = current_time + sentence_duration
                final_entries.append({"start": current_time, "end": sentence_end_time, "content": sentence})
                current_time = sentence_end_time
        return final_entries

    def create_logical_blocks(self, chunks: list) -> list:
        logical_blocks = []
        buffer = ""
        start_time = -1
        last_end_time = -1
        for chunk in chunks:
            text = chunk['content']
            if start_time < 0:
                start_time = chunk['start']
            buffer += text + " "
            last_end_time = chunk['end']
            if text.strip().endswith(('.', '?', '!')):
                logical_blocks.append({"start": start_time, "end": last_end_time, "content": buffer.strip()})
                buffer = ""
                start_time = -1
        if buffer.strip() and start_time >= 0:
            logical_blocks.append({"start": start_time, "end": last_end_time, "content": buffer.strip()})
        return logical_blocks

    @Slot()
    def run(self):
        self.log_message.emit("\n--- Starting New Batch ---")
        os.makedirs(self.output_dir, exist_ok=True)
        for task in self.tasks:
            try:
                if task['type'] == 'url':
                    self.process_youtube_url(task['value'])
                elif task['type'] == 'file':
                    self.process_local_srt_file(task['value'])
                self.log_message.emit("-" * 25)
            except Exception as e:
                self.log_message.emit(f"[!!!] CRITICAL ERROR on {task['value']}: {e}")
        self.finished.emit()

    def process_youtube_url(self, video_url: str):
        self.log_message.emit(f"[*] Processing URL: {video_url}")
        video_id = get_video_id(video_url)
        if not video_id:
            self.log_message.emit(f"[!] Invalid YouTube URL, skipping.")
            return

        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True, 'writeautomaticsub': True, 'subtitleslangs': ['en']}) as ydl:
            info = ydl.extract_info(video_url, download=False)
        title = info.get('title', 'N/A')
        
        subtitle_url, source = self.find_subtitle_url(info)
        if not subtitle_url:
            self.log_message.emit(f"[!] No English subtitles found for '{title}'")
            return
        self.log_message.emit(f"    > Found {source} subtitles for '{title}'")
        
        response = requests.get(subtitle_url)
        root = ET.fromstring(response.text)
        namespaces = {'ttml': 'http://www.w3.org/ns/ttml'}
        
        raw_chunks = [
            {"start": parse_time(p.attrib['begin']), "end": parse_time(p.attrib['end']), "content": " ".join(p.text.strip().split())}
            for p in root.findall('.//ttml:p', namespaces) if p.text and p.attrib.get('begin')
        ]
        
        logical_blocks = self.create_logical_blocks(raw_chunks)
        final_entries = self.split_into_sentences(logical_blocks)
        
        # Apply the final non-overlap cleanup pass
        self.enforce_no_overlap(final_entries)
        
        output_path = os.path.join(self.output_dir, f"{video_id}.srt")
        write_srt_file(final_entries, output_path)
        self.log_message.emit(f"    > SUCCESS: Rearranged and created file: {output_path}")

    def find_subtitle_url(self, info):
        subs_info = info.get('subtitles') or info.get('automatic_captions')
        source = "manual" if 'subtitles' in info else "auto-generated"
        if subs_info and 'en' in subs_info:
            for sub_format in subs_info['en']:
                if sub_format['ext'] == 'ttml':
                    return sub_format['url'], source
        return None, None

    def process_local_srt_file(self, filepath: str):
        self.log_message.emit(f"[*] Processing local file: {filepath}")
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        srt_pattern = re.compile(r'\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)\n\n', re.DOTALL)
        
        raw_chunks = [
            {"start": parse_time(m.group(1)), "end": parse_time(m.group(2)), "content": " ".join(m.group(3).strip().split('\n'))}
            for m in srt_pattern.finditer(content)
        ]
        
        if not raw_chunks:
            self.log_message.emit(f"[!] No valid entries found in {os.path.basename(filepath)}")
            return
        
        logical_blocks = self.create_logical_blocks(raw_chunks)
        final_entries = self.split_into_sentences(logical_blocks)
        
        # Apply the final non-overlap cleanup pass
        self.enforce_no_overlap(final_entries)
        
        filename = os.path.basename(filepath)
        output_path = os.path.join(self.output_dir, filename)
        
        write_srt_file(final_entries, output_path)
        self.log_message.emit(f"    > SUCCESS: Rearranged and created new file: {output_path}")

# --- FRONTEND LOGIC (PySide6 UI) ---

class SubtitleApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Processor Pro")
        self.setGeometry(100, 100, 800, 650)

        self.output_dir = os.path.join(os.getcwd(), "subtitles")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        input_group = QGroupBox("Input (YouTube URLs or Local .srt Paths)")
        input_layout = QVBoxLayout()
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Paste YouTube URLs here, or add local files using the button below...")
        input_layout.addWidget(self.input_text)
        
        button_layout = QHBoxLayout()
        self.add_files_button = QPushButton("Add Local .srt Files...")
        self.add_files_button.clicked.connect(self.add_local_files_to_input)
        self.start_button = QPushButton("Start Processing")
        self.start_button.setStyleSheet("font-weight: bold;")
        self.start_button.clicked.connect(self.start_processing)
        button_layout.addWidget(self.add_files_button)
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        input_layout.addLayout(button_layout)
        input_group.setLayout(input_layout)
        
        output_group = QGroupBox("Output Folder (for all processed files)")
        output_layout = QHBoxLayout()
        self.output_path_display = QLineEdit(self.output_dir)
        self.output_path_display.setReadOnly(True)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.select_output_folder)
        output_layout.addWidget(self.output_path_display)
        output_layout.addWidget(self.browse_button)
        output_group.setLayout(output_layout)

        log_group = QGroupBox("Execution Log")
        log_layout = QVBoxLayout()
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        log_layout.addWidget(self.log_widget)
        log_group.setLayout(log_layout)
        
        main_layout.addWidget(input_group, 1)
        main_layout.addWidget(output_group, 0)
        main_layout.addWidget(log_group, 2)

        self.log_message(f"Welcome! All processed files will be saved in '{self.output_dir}'.")

    @Slot(str)
    def log_message(self, message):
        self.log_widget.append(message)

    @Slot()
    def select_output_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_dir)
        if directory:
            self.output_dir = directory
            self.output_path_display.setText(self.output_dir)
            self.log_message(f"Output folder changed to: {self.output_dir}")

    @Slot()
    def add_local_files_to_input(self):
        filepaths, _ = QFileDialog.getOpenFileNames(self, "Select .srt files", "", "SRT files (*.srt);;All files (*.*)")
        if filepaths:
            self.input_text.append("\n".join(filepaths))

    @Slot()
    def start_processing(self):
        all_lines = self.input_text.toPlainText().strip()
        if not all_lines:
            QMessageBox.warning(self, "Input Required", "The input box is empty.")
            return

        tasks = []
        for line in all_lines.splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith("http"):
                tasks.append({'type': 'url', 'value': line})
            elif os.path.exists(line):
                tasks.append({'type': 'file', 'value': line})
            else:
                self.log_message(f"[!] SKIPPING: Unrecognized input: {line}")
        
        if not tasks:
            QMessageBox.critical(self, "No Valid Input", "Could not find any valid URLs or existing file paths.")
            return

        self.set_ui_state(is_running=True)

        self.thread = QThread()
        self.worker = Worker(tasks, self.output_dir)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.log_message.connect(self.log_message)
        self.worker.finished.connect(lambda: self.log_message("\n[+] All tasks completed."))
        self.worker.finished.connect(lambda: self.set_ui_state(False))

        self.thread.start()

    def set_ui_state(self, is_running):
        self.start_button.setDisabled(is_running)
        self.add_files_button.setDisabled(is_running)
        self.browse_button.setDisabled(is_running)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SubtitleApp()
    window.show()
    sys.exit(app.exec())