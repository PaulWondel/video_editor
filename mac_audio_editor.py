import sys
import os
import tempfile
import subprocess
import numpy as np

# PySide6 UI Imports
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QSlider, QLabel, QProgressBar, QMessageBox,
    QComboBox, QGroupBox, QDoubleSpinBox, QFormLayout, QRadioButton
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# Matplotlib imports for audio waveform rendering
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from pydub import AudioSegment


class VideoAudioExportThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, track1_path, track2_path, output_path, merge_mode, audio_settings):
        super().__init__()
        self.track1_path = track1_path
        self.track2_path = track2_path
        self.output_path = output_path
        self.merge_mode = merge_mode
        self.audio_settings = audio_settings

    def run(self):
        try:
            target_track = self.audio_settings.get("target_track", 1)
            gain_db = self.audio_settings.get("gain_db", 0.0)
            fade_in = self.audio_settings.get("fade_in_s", 0.0)
            fade_out = self.audio_settings.get("fade_out_s", 0.0)
            start_s = self.audio_settings.get("start_s", 0.0)
            end_s = self.audio_settings.get("end_s", 0.0)

            # Build audio filter chain
            audio_filters = []
            
            if end_s > start_s and end_s > 0:
                audio_filters.append(f"atrim=start={start_s}:end={end_s},asetpts=PTS-STARTPTS")

            if gain_db != 0:
                audio_filters.append(f"volume={gain_db}dB")

            if fade_in > 0:
                audio_filters.append(f"afade=t=in:st=0:d={fade_in}")
            if fade_out > 0 and (end_s - start_s) > fade_out:
                audio_filters.append(f"afade=t=out:st={(end_s - start_s) - fade_out}:d={fade_out}")

            af_str = ",".join(audio_filters) if audio_filters else "anull"

            a0_filter = af_str if target_track == 1 else "aresample=async=1000"
            a1_filter = af_str if target_track == 2 else "aresample=async=1000"

            if self.merge_mode == "Sequence (Track 1 followed by Track 2)":
                filter_complex = (
                    f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0]; "
                    f"[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-ih)/2:(oh-ih)/2,setsar=1,fps=30[v1]; "
                    f"[0:a]{a0_filter}[a0]; [1:a]{a1_filter}[a1]; "
                    f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
                )
            else:  # Side-by-Side (Split Screen)
                filter_complex = (
                    f"[0:v]scale=-1:720[v0]; [1:v]scale=-1:720[v1]; "
                    f"[v0][v1]hstack=inputs=2[v]; "
                    f"[0:a]{a0_filter}[a0]; [1:a]{a1_filter}[a1]; "
                    f"[a0][a1]amix=inputs=2:duration=longest[a]"
                )

            cmd = [
                "ffmpeg", "-y",
                "-i", self.track1_path,
                "-i", self.track2_path,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                self.output_path
            ]

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            _, stderr = process.communicate()

            if process.returncode == 0:
                self.finished.emit(True, "Export completed successfully with audio edits applied!")
            else:
                self.finished.emit(False, f"FFmpeg error:\n{stderr[-400:]}")

        except Exception as e:
            self.finished.emit(False, str(e))


class ModernMacVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Studio with Embedded Audio Waveform Editor")
        self.resize(1150, 880)

        self.track1_path = None
        self.track2_path = None
        self.active_audio_track = 1
        self.audio_segments = {1: None, 2: None}
        self.temp_preview_file = None

        # Track 1 Player
        self.player_t1 = QMediaPlayer()
        self.audio_t1 = QAudioOutput()
        self.player_t1.setAudioOutput(self.audio_t1)

        # Track 2 Player
        self.player_t2 = QMediaPlayer()
        self.audio_t2 = QAudioOutput()
        self.player_t2.setAudioOutput(self.audio_t2)

        # Audio Preview Player
        self.audio_preview_player = QMediaPlayer()
        self.audio_preview_output = QAudioOutput()
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)

        self.setup_ui()
        self.apply_dark_theme()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. Video Tracks Loader Section
        tracks_group = QGroupBox("Import Video Tracks")
        tracks_layout = QVBoxLayout(tracks_group)

        # Track 1 Loader
        t1_box = QHBoxLayout()
        self.btn_t1 = QPushButton("Load Track 1 Video")
        self.btn_t1.clicked.connect(lambda: self.open_file(track_num=1))
        self.lbl_t1_info = QLabel("No video loaded")
        self.lbl_t1_info.setStyleSheet("color: #888888; font-weight: bold;")
        t1_box.addWidget(self.btn_t1)
        t1_box.addWidget(self.lbl_t1_info)
        t1_box.addStretch()
        tracks_layout.addLayout(t1_box)

        # Track 2 Loader
        t2_box = QHBoxLayout()
        self.btn_t2 = QPushButton("Load Track 2 Video")
        self.btn_t2.clicked.connect(lambda: self.open_file(track_num=2))
        self.lbl_t2_info = QLabel("No video loaded")
        self.lbl_t2_info.setStyleSheet("color: #888888; font-weight: bold;")
        t2_box.addWidget(self.btn_t2)
        t2_box.addWidget(self.lbl_t2_info)
        t2_box.addStretch()
        tracks_layout.addLayout(t2_box)

        main_layout.addWidget(tracks_group)

        # 2. Video Previews Section
        previews_layout = QHBoxLayout()

        # Track 1 Preview
        t1_preview_box = QVBoxLayout()
        t1_title = QLabel("Track 1 Preview")
        t1_title.setAlignment(Qt.AlignCenter)
        t1_title.setStyleSheet("font-weight: bold; color: #0d6efd;")
        self.video_widget_t1 = QVideoWidget()
        self.video_widget_t1.setStyleSheet("background-color: #000000; border-radius: 6px;")
        self.player_t1.setVideoOutput(self.video_widget_t1)
        t1_preview_box.addWidget(t1_title)
        t1_preview_box.addWidget(self.video_widget_t1)

        # Track 2 Preview
        t2_preview_box = QVBoxLayout()
        t2_title = QLabel("Track 2 Preview")
        t2_title.setAlignment(Qt.AlignCenter)
        t2_title.setStyleSheet("font-weight: bold; color: #0d6efd;")
        self.video_widget_t2 = QVideoWidget()
        self.video_widget_t2.setStyleSheet("background-color: #000000; border-radius: 6px;")
        self.player_t2.setVideoOutput(self.video_widget_t2)
        t2_preview_box.addWidget(t2_title)
        t2_preview_box.addWidget(self.video_widget_t2)

        previews_layout.addLayout(t1_preview_box, stretch=1)
        previews_layout.addLayout(t2_preview_box, stretch=1)
        main_layout.addLayout(previews_layout, stretch=1)

        # Playback Controls & Timeline Slider
        playback_layout = QHBoxLayout()
        self.btn_play = QPushButton("Play Both Video Previews")
        self.btn_play.clicked.connect(self.toggle_play)

        self.lbl_time = QLabel("00:00 / 00:00")

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.sliderMoved.connect(self.set_player_position)

        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.timeline_slider)
        playback_layout.addWidget(self.lbl_time)
        main_layout.addLayout(playback_layout)

        # 3. Embedded Audio Waveform & Editing Panel
        audio_group = QGroupBox("Integrated Video-Audio Waveform Editor")
        audio_main_layout = QVBoxLayout(audio_group)

        # Target Selector & Preview Action Bar
        target_box = QHBoxLayout()
        target_box.addWidget(QLabel("Edit Audio Stream For:"))
        
        self.rb_track1 = QRadioButton("Track 1 Video")
        self.rb_track1.setChecked(True)
        self.rb_track1.toggled.connect(self.on_audio_target_changed)
        
        self.rb_track2 = QRadioButton("Track 2 Video")
        self.rb_track2.toggled.connect(self.on_audio_target_changed)

        target_box.addWidget(self.rb_track1)
        target_box.addWidget(self.rb_track2)
        target_box.addStretch()

        # Preview Button
        self.btn_preview_audio = QPushButton("🔊 Preview Edited Audio")
        self.btn_preview_audio.setStyleSheet("""
            QPushButton {
                background-color: #17a2b8;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #138496; }
            QPushButton:disabled { background-color: #444444; color: #888888; }
        """)
        self.btn_preview_audio.setEnabled(False)
        self.btn_preview_audio.clicked.connect(self.preview_edited_audio)
        target_box.addWidget(self.btn_preview_audio)

        audio_main_layout.addLayout(target_box)

        # Matplotlib Canvas for Audio Waveform
        self.fig = Figure(figsize=(8, 1.8), facecolor="#121212")
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#1E1E1E")
        self.ax.tick_params(colors="white")
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.ax.spines['top'].set_color('#3A3A3A')
        self.ax.spines['right'].set_color('#3A3A3A')
        audio_main_layout.addWidget(self.canvas)

        # Audio Parameter Spinboxes
        controls_layout = QHBoxLayout()
        
        form1 = QFormLayout()
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setSuffix(" sec")
        self.spin_start.setRange(0, 3600)
        form1.addRow("Start Trim:", self.spin_start)

        self.spin_end = QDoubleSpinBox()
        self.spin_end.setSuffix(" sec")
        self.spin_end.setRange(0, 3600)
        form1.addRow("End Trim:", self.spin_end)

        form2 = QFormLayout()
        self.spin_gain = QDoubleSpinBox()
        self.spin_gain.setSuffix(" dB")
        self.spin_gain.setRange(-30, 30)
        form2.addRow("Volume Gain:", self.spin_gain)

        self.spin_fade_in = QDoubleSpinBox()
        self.spin_fade_in.setSuffix(" sec")
        self.spin_fade_in.setRange(0, 10)
        form2.addRow("Fade In:", self.spin_fade_in)

        form3 = QFormLayout()
        self.spin_fade_out = QDoubleSpinBox()
        self.spin_fade_out.setSuffix(" sec")
        self.spin_fade_out.setRange(0, 10)
        form3.addRow("Fade Out:", self.spin_fade_out)

        controls_layout.addLayout(form1)
        controls_layout.addLayout(form2)
        controls_layout.addLayout(form3)
        audio_main_layout.addLayout(controls_layout)

        main_layout.addWidget(audio_group)

        # 4. Export & Merge Section
        merge_box = QHBoxLayout()
        merge_box.addWidget(QLabel("Merge Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "Sequence (Track 1 followed by Track 2)",
            "Side-by-Side (Split Screen)"
        ])
        merge_box.addWidget(self.combo_mode)
        
        self.btn_export = QPushButton("Merge & Export Video with Audio Edits")
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #0d6efd;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #0b5ed7; }
            QPushButton:disabled { background-color: #444444; color: #888888; }
        """)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_merged_video)

        merge_box.addWidget(self.btn_export)
        main_layout.addLayout(merge_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

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

            # Extract audio waveform for visual editor
            try:
                seg = AudioSegment.from_file(file_path)
                self.audio_segments[track_num] = seg
                if self.active_audio_track == track_num:
                    self.render_waveform(seg)
                    self.btn_preview_audio.setEnabled(True)
            except Exception as e:
                print(f"Failed to parse audio from video: {e}")

            if self.track1_path and self.track2_path:
                self.btn_export.setEnabled(True)

    def on_audio_target_changed(self):
        self.active_audio_track = 1 if self.rb_track1.isChecked() else 2
        seg = self.audio_segments.get(self.active_audio_track)
        if seg:
            self.render_waveform(seg)
            self.btn_preview_audio.setEnabled(True)
        else:
            self.ax.clear()
            self.canvas.draw()
            self.btn_preview_audio.setEnabled(False)

    def render_waveform(self, audio_segment):
        self.ax.clear()
        samples = np.array(audio_segment.get_array_of_samples())

        subsample_rate = max(1, len(samples) // 2000)
        reduced_samples = samples[::subsample_rate]

        duration_sec = len(audio_segment) / 1000.0
        time_axis = np.linspace(0, duration_sec, num=len(reduced_samples))

        self.spin_start.setRange(0, duration_sec)
        self.spin_end.setRange(0, duration_sec)
        if self.spin_end.value() == 0 or self.spin_end.value() > duration_sec:
            self.spin_end.setValue(duration_sec)

        self.ax.plot(time_axis, reduced_samples, color="#0d6efd", alpha=0.8)
        self.ax.set_ylabel("Amp", color="white")
        self.ax.set_xlabel("Time (s)", color="white")
        self.fig.tight_layout()
        self.canvas.draw()

    def preview_edited_audio(self):
        """Generates a temporary edited audio clip and plays it immediately."""
        seg = self.audio_segments.get(self.active_audio_track)
        if not seg:
            return

        # Pause main video players if playing
        self.player_t1.pause()
        self.player_t2.pause()
        self.btn_play.setText("Play Both Video Previews")

        # Toggle audio preview playback if already playing
        if self.audio_preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.audio_preview_player.stop()
            self.btn_preview_audio.setText("🔊 Preview Edited Audio")
            return

        start_ms = int(self.spin_start.value() * 1000)
        end_ms = int(self.spin_end.value() * 1000)
        gain_db = self.spin_gain.value()
        fade_in_ms = int(self.spin_fade_in.value() * 1000)
        fade_out_ms = int(self.spin_fade_out.value() * 1000)

        # Slice audio
        edited_seg = seg[start_ms:end_ms] if end_ms > start_ms else seg[start_ms:]

        # Apply gain
        if gain_db != 0:
            edited_seg = edited_seg + gain_db

        # Apply fades
        if fade_in_ms > 0:
            edited_seg = edited_seg.fade_in(fade_in_ms)
        if fade_out_ms > 0 and len(edited_seg) > fade_out_ms:
            edited_seg = edited_seg.fade_out(fade_out_ms)

        # Export to temp WAV file
        temp_dir = tempfile.gettempdir()
        self.temp_preview_file = os.path.join(temp_dir, "temp_audio_preview.wav")
        edited_seg.export(self.temp_preview_file, format="wav")

        # Play processed audio
        self.audio_preview_player.setSource(QUrl.fromLocalFile(self.temp_preview_file))
        self.audio_preview_player.play()
        self.btn_preview_audio.setText("⏹ Stop Audio Preview")

        # Reset button label when playback finishes
        self.audio_preview_player.playbackStateChanged.connect(self.on_preview_state_changed)

    def on_preview_state_changed(self, state):
        if state != QMediaPlayer.PlayingState:
            self.btn_preview_audio.setText("🔊 Preview Edited Audio")

    def toggle_play(self):
        # Stop audio preview if active
        if self.audio_preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.audio_preview_player.stop()

        is_playing = (self.player_t1.playbackState() == QMediaPlayer.PlayingState)

        if is_playing:
            self.player_t1.pause()
            self.player_t2.pause()
            self.btn_play.setText("Play Both Video Previews")
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
        output_path, _ = QFileDialog.getSaveFileName(self, "Save Exported Video", "output.mp4", "MP4 Files (*.mp4)")
        if not output_path:
            return

        self.player_t1.pause()
        self.player_t2.pause()
        self.audio_preview_player.stop()
        self.btn_play.setText("Play Both Video Previews")

        self.btn_export.setEnabled(False)
        self.progress_bar.setVisible(True)

        audio_settings = {
            "target_track": self.active_audio_track,
            "start_s": self.spin_start.value(),
            "end_s": self.spin_end.value(),
            "gain_db": self.spin_gain.value(),
            "fade_in_s": self.spin_fade_in.value(),
            "fade_out_s": self.spin_fade_out.value()
        }

        mode = self.combo_mode.currentText()

        self.export_thread = VideoAudioExportThread(
            self.track1_path, self.track2_path, output_path, mode, audio_settings
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
            QLabel, QRadioButton { color: #E0E0E0; font-size: 13px; }
            QComboBox, QDoubleSpinBox { background-color: #2D2D2D; color: #FFFFFF; border: 1px solid #3A3A3A; border-radius: 4px; padding: 4px 8px; }
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