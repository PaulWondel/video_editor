import sys
import os

# PySide6 UI Imports
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QSlider, QLabel, QProgressBar, QMessageBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# MoviePy 2.x import
from moviepy import VideoFileClip


class ExportThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, input_path, output_path, start_time, end_time):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.start_time = start_time
        self.end_time = end_time

    def run(self):
        try:
            clip = VideoFileClip(self.input_path)
            
            # Handle version differences for subclip
            if hasattr(clip, "subclipped"):
                trimmed = clip.subclipped(self.start_time, self.end_time)
            else:
                trimmed = clip.subclip(self.start_time, self.end_time)

            trimmed.write_videofile(
                self.output_path,
                codec="libx264",
                audio_codec="aac",
                preset="fast",
                logger=None
            )
            
            trimmed.close()
            clip.close()
            self.finished.emit(True, "Video exported successfully!")
        except Exception as e:
            self.finished.emit(False, str(e))


class ModernMacVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mac Video Editor (PySide6)")
        self.resize(800, 600)

        self.video_path = None
        self.duration_secs = 0.0

        # Media Player Setup
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)

        self.setup_ui()
        self.apply_dark_theme()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(12)

        # File Chooser Bar
        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("Import Video File")
        self.btn_open.clicked.connect(self.open_file)
        
        self.lbl_file_info = QLabel("No video loaded")
        self.lbl_file_info.setStyleSheet("color: #888888; font-weight: bold;")
        
        top_bar.addWidget(self.btn_open)
        top_bar.addWidget(self.lbl_file_info)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Video Output Widget
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000; border-radius: 8px;")
        self.media_player.setVideoOutput(self.video_widget)
        layout.addWidget(self.video_widget, stretch=1)

        # Playback Controls
        playback_layout = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self.toggle_play)
        
        self.lbl_time = QLabel("00:00 / 00:00")
        
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.sliderMoved.connect(self.set_player_position)

        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.timeline_slider)
        playback_layout.addWidget(self.lbl_time)
        layout.addLayout(playback_layout)

        # Trimming Controls
        trim_card = QWidget()
        trim_card.setStyleSheet("background-color: #1e1e1e; border-radius: 8px;")
        trim_layout = QVBoxLayout(trim_card)

        start_box = QHBoxLayout()
        start_box.addWidget(QLabel("Trim Start:"))
        self.slider_start = QSlider(Qt.Horizontal)
        self.slider_start.valueChanged.connect(self.on_trim_start_changed)
        self.lbl_start_val = QLabel("0.0s")
        start_box.addWidget(self.slider_start)
        start_box.addWidget(self.lbl_start_val)
        trim_layout.addLayout(start_box)

        end_box = QHBoxLayout()
        end_box.addWidget(QLabel("Trim End:"))
        self.slider_end = QSlider(Qt.Horizontal)
        self.slider_end.valueChanged.connect(self.on_trim_end_changed)
        self.lbl_end_val = QLabel("0.0s")
        end_box.addWidget(self.slider_end)
        end_box.addWidget(self.lbl_end_val)
        trim_layout.addLayout(end_box)

        layout.addWidget(trim_card)

        # Export Controls
        export_layout = QHBoxLayout()
        self.btn_export = QPushButton("Export Trimmed Video")
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #0d6efd;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #0b5ed7; }
            QPushButton:disabled { background-color: #444444; color: #888888; }
        """)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_video)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)

        export_layout.addWidget(self.progress_bar)
        export_layout.addStretch()
        export_layout.addWidget(self.btn_export)
        layout.addLayout(export_layout)

        # Connect Player Signals
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "", "Video Files (*.mp4 *.mov *.avi)"
        )
        if file_path:
            self.video_path = file_path
            self.lbl_file_info.setText(os.path.basename(file_path))
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.btn_export.setEnabled(True)

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.btn_play.setText("Play")
        else:
            self.media_player.play()
            self.btn_play.setText("Pause")

    def position_changed(self, position):
        if self.media_player.duration() > 0:
            val = int((position / self.media_player.duration()) * 1000)
            self.timeline_slider.setValue(val)
            self.update_time_label(position, self.media_player.duration())

    def duration_changed(self, duration):
        self.duration_secs = duration / 1000.0
        self.slider_start.setRange(0, int(self.duration_secs))
        self.slider_end.setRange(0, int(self.duration_secs))
        self.slider_start.setValue(0)
        self.slider_end.setValue(int(self.duration_secs))

    def set_player_position(self, value):
        if self.media_player.duration() > 0:
            position = int((value / 1000.0) * self.media_player.duration())
            self.media_player.setPosition(position)

    def on_trim_start_changed(self, val):
        if val >= self.slider_end.value():
            self.slider_start.setValue(self.slider_end.value() - 1)
            return
        self.lbl_start_val.setText(f"{val}.0s")
        self.media_player.setPosition(val * 1000)

    def on_trim_end_changed(self, val):
        if val <= self.slider_start.value():
            self.slider_end.setValue(self.slider_start.value() + 1)
            return
        self.lbl_end_val.setText(f"{val}.0s")
        self.media_player.setPosition(val * 1000)

    def update_time_label(self, current_ms, total_ms):
        c_sec = current_ms // 1000
        t_sec = total_ms // 1000
        self.lbl_time.setText(f"{c_sec // 60:02d}:{c_sec % 60:02d} / {t_sec // 60:02d}:{t_sec % 60:02d}")

    def export_video(self):
        output_path, _ = QFileDialog.getSaveFileName(self, "Save Clip", "trimmed.mp4", "MP4 Files (*.mp4)")
        if not output_path:
            return

        self.btn_export.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.export_thread = ExportThread(
            self.video_path, output_path, self.slider_start.value(), self.slider_end.value()
        )
        self.export_thread.finished.connect(self.on_export_finished)
        self.export_thread.start()

    def on_export_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        if success:
            QMessageBox.information(self, "Export Complete", message)
        else:
            QMessageBox.critical(self, "Export Error", f"Details: {message}")

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QLabel { color: #E0E0E0; font-size: 13px; }
            QPushButton {
                background-color: #2D2D2D;
                color: #FFFFFF;
                border: 1px solid #3A3A3A;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #3D3D3D; }
            QSlider::groove:horizontal { height: 4px; background: #3A3A3A; }
            QSlider::handle:horizontal {
                background: #0d6efd;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
        """)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ModernMacVideoEditor()
    window.show()
    sys.exit(app.exec())