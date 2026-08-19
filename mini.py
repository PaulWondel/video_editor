import sys
import os
import subprocess
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QDoubleSpinBox, QProgressBar, QMessageBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class FastCutThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, input_file, output_file, start_sec, end_sec):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.start_sec = start_sec
        self.end_sec = end_sec

    def run(self):
        duration = self.end_sec - self.start_sec
        if duration <= 0:
            self.finished.emit(False, "End time must be greater than start time.")
            return

        # Uses stream copying (-c copy) for instantaneous cutting without re-encoding
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(self.start_sec),
            "-i", self.input_file,
            "-t", str(duration),
            "-c", "copy",
            self.output_file
        ]
        
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                self.finished.emit(True, "Video cut successfully!")
            else:
                self.finished.emit(False, res.stderr)
        except Exception as e:
            self.finished.emit(False, str(e))


class MiniVideoTrimmer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fast Video Trimmer")
        self.resize(700, 500)
        self.file_path = None
        self.duration_sec = 0.0

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Video Player Preview
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")
        self.player.setVideoOutput(self.video_widget)
        layout.addWidget(self.video_widget)

        # File controls
        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("📁 Open Video")
        self.btn_open.clicked.connect(self.open_file)
        self.lbl_file = QLabel("No file selected")
        top_bar.addWidget(self.btn_open)
        top_bar.addWidget(self.lbl_file)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Trim range controls
        controls = QHBoxLayout()
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setSuffix("s")
        self.spin_start.setDecimals(1)

        self.spin_end = QDoubleSpinBox()
        self.spin_end.setSuffix("s")
        self.spin_end.setDecimals(1)

        self.btn_play = QPushButton("▶ Play Range")
        self.btn_play.clicked.connect(self.play_range)

        controls.addWidget(QLabel("Start:"))
        controls.addWidget(self.spin_start)
        controls.addWidget(QLabel("End:"))
        controls.addWidget(self.spin_end)
        controls.addWidget(self.btn_play)
        controls.addStretch()
        layout.addLayout(controls)

        # Export controls
        export_bar = QHBoxLayout()
        self.btn_export = QPushButton("✂ Cut & Save Video")
        self.btn_export.setStyleSheet("background-color: #0d6efd; color: white; font-weight: bold; padding: 6px 12px;")
        self.btn_export.clicked.connect(self.export_video)
        export_bar.addStretch()
        export_bar.addWidget(self.btn_export)
        layout.addLayout(export_bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.mov *.mkv *.avi)")
        if file_path:
            self.file_path = file_path
            self.lbl_file.setText(os.path.basename(file_path))
            
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.durationChanged.connect(self.on_duration_changed)

    def on_duration_changed(self, duration_ms):
        self.duration_sec = duration_ms / 1000.0
        self.spin_start.setRange(0, self.duration_sec)
        self.spin_end.setRange(0, self.duration_sec)
        self.spin_start.setValue(0)
        self.spin_end.setValue(self.duration_sec)

    def play_range(self):
        if self.file_path:
            self.player.setPosition(int(self.spin_start.value() * 1000))
            self.player.play()

    def export_video(self):
        if not self.file_path:
            QMessageBox.warning(self, "Error", "Please load a video file first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(self, "Save Trimmed Video", "trimmed_output.mp4", "MP4 Files (*.mp4)")
        if out_path:
            self.player.pause()
            self.progress.show()
            self.btn_export.setEnabled(False)

            self.cut_thread = FastCutThread(
                self.file_path, out_path, 
                self.spin_start.value(), self.spin_end.value()
            )
            self.cut_thread.finished.connect(self.on_export_finished)
            self.cut_thread.start()

    def on_export_finished(self, success, msg):
        self.progress.hide()
        self.btn_export.setEnabled(True)
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.critical(self, "Error", msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MiniVideoTrimmer()
    window.show()
    sys.exit(app.exec())