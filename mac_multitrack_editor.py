import sys
import os
import subprocess

# PySide6 UI Imports
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QSlider, QLabel, QProgressBar, QMessageBox,
    QComboBox, QGroupBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class ExportThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, track1_path, track2_path, output_path, merge_mode):
        super().__init__()
        self.track1_path = track1_path
        self.track2_path = track2_path
        self.output_path = output_path
        self.merge_mode = merge_mode

    def run(self):
        try:
            if self.merge_mode == "Sequence (Track 1 followed by Track 2)":
                # Rescale both inputs to standard 1080p and pad to retain aspect ratio before concat
                cmd = [
                    "ffmpeg", "-y",
                    "-i", self.track1_path,
                    "-i", self.track2_path,
                    "-filter_complex",
                    "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0]; "
                    "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1]; "
                    "[0:a]aresample=async=1000[a0]; [1:a]aresample=async=1000[a1]; "
                    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
                    "-map", "[v]",
                    "-map", "[a]",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    self.output_path
                ]

            elif self.merge_mode == "Side-by-Side (Split Screen)":
                # Match heights so hstack does not fail
                cmd = [
                    "ffmpeg", "-y",
                    "-i", self.track1_path,
                    "-i", self.track2_path,
                    "-filter_complex",
                    "[0:v]scale=-1:720[v0]; [1:v]scale=-1:720[v1]; "
                    "[v0][v1]hstack=inputs=2[v]; "
                    "[0:a][1:a]amix=inputs=2:duration=longest[a]",
                    "-map", "[v]",
                    "-map", "[a]",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-af", "aresample=async=1000",
                    self.output_path
                ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            _, stderr = process.communicate()

            if process.returncode == 0:
                self.finished.emit(True, "FFmpeg export completed successfully!")
            else:
                self.finished.emit(False, f"FFmpeg error:\n{stderr[-400:]}")

        except Exception as e:
            self.finished.emit(False, str(e))


class ModernMacVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mac Multi-Track Video Editor (Dual Preview)")
        self.resize(1000, 720)

        self.track1_path = None
        self.track2_path = None

        # Track 1 Player
        self.player_t1 = QMediaPlayer()
        self.audio_t1 = QAudioOutput()
        self.player_t1.setAudioOutput(self.audio_t1)

        # Track 2 Player
        self.player_t2 = QMediaPlayer()
        self.audio_t2 = QAudioOutput()
        self.player_t2.setAudioOutput(self.audio_t2)

        self.setup_ui()
        self.apply_dark_theme()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(12)

        # Tracks Loader Group
        tracks_group = QGroupBox("Video Tracks")
        tracks_layout = QVBoxLayout(tracks_group)

        # Track 1 Loader
        t1_box = QHBoxLayout()
        self.btn_t1 = QPushButton("Load Track 1")
        self.btn_t1.clicked.connect(lambda: self.open_file(track_num=1))
        self.lbl_t1_info = QLabel("No video loaded")
        self.lbl_t1_info.setStyleSheet("color: #888888; font-weight: bold;")
        t1_box.addWidget(self.btn_t1)
        t1_box.addWidget(self.lbl_t1_info)
        t1_box.addStretch()
        tracks_layout.addLayout(t1_box)

        # Track 2 Loader
        t2_box = QHBoxLayout()
        self.btn_t2 = QPushButton("Load Track 2")
        self.btn_t2.clicked.connect(lambda: self.open_file(track_num=2))
        self.lbl_t2_info = QLabel("No video loaded")
        self.lbl_t2_info.setStyleSheet("color: #888888; font-weight: bold;")
        t2_box.addWidget(self.btn_t2)
        t2_box.addWidget(self.lbl_t2_info)
        t2_box.addStretch()
        tracks_layout.addLayout(t2_box)

        layout.addWidget(tracks_group)

        # Previews Container
        previews_layout = QHBoxLayout()

        # Track 1 Display
        t1_preview_box = QVBoxLayout()
        t1_title = QLabel("Track 1 Preview")
        t1_title.setAlignment(Qt.AlignCenter)
        t1_title.setStyleSheet("font-weight: bold; color: #0d6efd;")
        self.video_widget_t1 = QVideoWidget()
        self.video_widget_t1.setStyleSheet("background-color: #000000; border-radius: 8px;")
        self.player_t1.setVideoOutput(self.video_widget_t1)
        t1_preview_box.addWidget(t1_title)
        t1_preview_box.addWidget(self.video_widget_t1)

        # Track 2 Display
        t2_preview_box = QVBoxLayout()
        t2_title = QLabel("Track 2 Preview")
        t2_title.setAlignment(Qt.AlignCenter)
        t2_title.setStyleSheet("font-weight: bold; color: #0d6efd;")
        self.video_widget_t2 = QVideoWidget()
        self.video_widget_t2.setStyleSheet("background-color: #000000; border-radius: 8px;")
        self.player_t2.setVideoOutput(self.video_widget_t2)
        t2_preview_box.addWidget(t2_title)
        t2_preview_box.addWidget(self.video_widget_t2)

        previews_layout.addLayout(t1_preview_box, stretch=1)
        previews_layout.addLayout(t2_preview_box, stretch=1)
        layout.addLayout(previews_layout, stretch=1)

        # Controls & Slider
        playback_layout = QHBoxLayout()
        self.btn_play = QPushButton("Play Both Previews")
        self.btn_play.clicked.connect(self.toggle_play)

        self.lbl_time = QLabel("00:00 / 00:00")

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.sliderMoved.connect(self.set_player_position)

        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.timeline_slider)
        playback_layout.addWidget(self.lbl_time)
        layout.addLayout(playback_layout)

        # Mode Selector
        merge_box = QHBoxLayout()
        merge_box.addWidget(QLabel("Merge Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "Sequence (Track 1 followed by Track 2)",
            "Side-by-Side (Split Screen)"
        ])
        merge_box.addWidget(self.combo_mode)
        merge_box.addStretch()
        layout.addLayout(merge_box)

        # Export Controls
        export_layout = QHBoxLayout()
        self.btn_export = QPushButton("Merge & Export Tracks")
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #0d6efd;
                color: white;
                font-weight: bold;
                padding: 10px 18px;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #0b5ed7; }
            QPushButton:disabled { background-color: #444444; color: #888888; }
        """)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_merged_video)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)

        export_layout.addWidget(self.progress_bar)
        export_layout.addStretch()
        export_layout.addWidget(self.btn_export)
        layout.addLayout(export_layout)

        # Signals
        self.player_t1.positionChanged.connect(self.position_changed)

    def open_file(self, track_num):
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Select Track {track_num} Video", "", "Video Files (*.mp4 *.mov *.avi)"
        )
        if file_path:
            filename = os.path.basename(file_path)
            if track_num == 1:
                self.track1_path = file_path
                self.lbl_t1_info.setText(filename)
                self.player_t1.setSource(QUrl.fromLocalFile(file_path))
            elif track_num == 2:
                self.track2_path = file_path
                self.lbl_t2_info.setText(filename)
                self.player_t2.setSource(QUrl.fromLocalFile(file_path))

            if self.track1_path and self.track2_path:
                self.btn_export.setEnabled(True)

    def toggle_play(self):
        is_playing = (self.player_t1.playbackState() == QMediaPlayer.PlayingState)

        if is_playing:
            self.player_t1.pause()
            self.player_t2.pause()
            self.btn_play.setText("Play Both Previews")
        else:
            self.player_t1.play()
            self.player_t2.play()
            self.btn_play.setText("Pause")

    def position_changed(self, position):
        if not self.timeline_slider.isSliderDown():
            max_duration = max(self.player_t1.duration(), self.player_t2.duration())
            if max_duration > 0:
                val = int((position / max_duration) * 1000)
                self.timeline_slider.setValue(val)
                self.update_time_label(position, max_duration)

    def set_player_position(self, value):
        max_duration = max(self.player_t1.duration(), self.player_t2.duration())
        if max_duration > 0:
            target_pos = int((value / 1000.0) * max_duration)
            self.player_t1.setPosition(target_pos)
            self.player_t2.setPosition(target_pos)

    def update_time_label(self, current_ms, total_ms):
        c_sec = current_ms // 1000
        t_sec = total_ms // 1000
        self.lbl_time.setText(f"{c_sec // 60:02d}:{c_sec % 60:02d} / {t_sec // 60:02d}:{t_sec % 60:02d}")

    def export_merged_video(self):
        output_path, _ = QFileDialog.getSaveFileName(self, "Save Merged Video", "merged.mp4", "MP4 Files (*.mp4)")
        if not output_path:
            return

        self.player_t1.pause()
        self.player_t2.pause()
        self.btn_play.setText("Play Both Previews")

        self.btn_export.setEnabled(False)
        self.progress_bar.setVisible(True)

        mode = self.combo_mode.currentText()

        self.export_thread = ExportThread(
            self.track1_path, self.track2_path, output_path, mode
        )
        self.export_thread.finished.connect(self.on_export_finished)
        self.export_thread.start()

    def on_export_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        if success:
            QMessageBox.information(self, "Export Complete", message)
        else:
            QMessageBox.critical(self, "Export Error", message)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QGroupBox { color: #E0E0E0; font-weight: bold; border: 1px solid #3A3A3A; border-radius: 6px; margin-top: 6px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
            QLabel { color: #E0E0E0; font-size: 13px; }
            QComboBox { background-color: #2D2D2D; color: #FFFFFF; border: 1px solid #3A3A3A; border-radius: 4px; padding: 4px 8px; }
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