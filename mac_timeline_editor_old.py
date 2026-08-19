import sys
import os
import tempfile
import subprocess
import numpy as np

# PySide6 UI Imports
from PySide6.QtCore import Qt, QUrl, QThread, Signal, QRectF, QPointF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QSlider, QLabel, QProgressBar, QMessageBox,
    QComboBox, QGroupBox, QDoubleSpinBox, QFormLayout, QGraphicsScene,
    QGraphicsView, QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QRadioButton
)
from PySide6.QtGui import QColor, QBrush, QPen, QFont, QPainter
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# Matplotlib imports
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from pydub import AudioSegment


class TimelineClipItem(QGraphicsRectItem):
    """Visual draggable block representing a video or audio clip on the timeline."""
    def __init__(self, x, y, width, height, title, file_path, track_num, is_audio=False):
        super().__init__(x, y, width, height)
        self.is_audio = is_audio
        self.file_path = file_path
        self.track_num = track_num
        self.y_fixed = y
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        
        # Color coding: Green for Video, Purple for Audio
        fill_color = QColor("#6f42c1") if is_audio else QColor("#28a745")
        self.setBrush(QBrush(fill_color))
        self.setPen(QPen(QColor("#FFFFFF"), 1))

        # Clip Label
        self.text_item = QGraphicsTextItem(title, self)
        self.text_item.setDefaultTextColor(Qt.white)
        self.text_item.setFont(QFont("Arial", 9, QFont.Bold))
        self.text_item.setPos(x + 5, y + (height / 4))

    def itemChange(self, change, value):
        # Lock vertical movement to keep items on their respective track row
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            new_pos = value
            new_pos.setY(0)  # Restrict vertical drift from base Y
            if new_pos.x() < 0:
                new_pos.setX(0)  # Restrict dragging past time 0
            return new_pos
        return super().itemChange(change, value)


class CapCutTimelineView(QGraphicsView):
    """Interactive Timeline View representing video sequence and audio tracks."""
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #3A3A3A; border-radius: 6px;")
        self.setFixedHeight(140)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.pixels_per_second = 20  # Timeline zoom scale (1 sec = 20px)
        self.video_clips = []
        self.audio_clips = []

        self.draw_timeline_tracks()

    def draw_timeline_tracks(self):
        self.scene.clear()
        self.video_clips.clear()
        self.audio_clips.clear()

        # Track 1 Header (Video Sequence)
        v_label = self.scene.addText("🎬 Video Track")
        v_label.setDefaultTextColor(QColor("#888888"))
        v_label.setPos(5, 5)

        # Track 2 Header (Audio Overlay)
        a_label = self.scene.addText("🎵 Audio Track")
        a_label.setDefaultTextColor(QColor("#888888"))
        a_label.setPos(5, 65)

        # Draw track separator line
        self.scene.addLine(0, 60, 2000, 60, QPen(QColor("#333333"), 2))

    def add_clip(self, file_path, duration_sec, track_num=1, is_audio=False):
        filename = os.path.basename(file_path)
        clip_width = max(40, duration_sec * self.pixels_per_second)

        if not is_audio:
            # Place video clip next to the last video clip sequentially
            start_x = sum([item.rect().width() for item in self.video_clips])
            clip = TimelineClipItem(start_x, 25, clip_width, 30, filename, file_path, track_num, is_audio=False)
            self.scene.addItem(clip)
            self.video_clips.append(clip)
        else:
            # Place audio clip on the dedicated audio track row (y=85)
            start_x = sum([item.rect().width() for item in self.audio_clips])
            clip = TimelineClipItem(start_x, 85, clip_width, 30, filename, file_path, track_num, is_audio=True)
            self.scene.addItem(clip)
            self.audio_clips.append(clip)

        self.scene.setSceneRect(0, 0, max(1000, self.scene.itemsBoundingRect().width() + 100), 120)

    def get_ordered_video_tracks(self):
        """Returns video track paths ordered by horizontal timeline position."""
        sorted_clips = sorted(self.video_clips, key=lambda clip: clip.scenePos().x() + clip.rect().x())
        return [clip.file_path for clip in sorted_clips], [clip.track_num for clip in sorted_clips]

    def get_audio_clip_info(self):
        """Returns details for the primary audio clip placed on the audio track."""
        if not self.audio_clips:
            return None
        audio_clip = self.audio_clips[0]
        start_x = audio_clip.scenePos().x() + audio_clip.rect().x()
        start_time_sec = max(0.0, start_x / self.pixels_per_second)
        return {
            "file_path": audio_clip.file_path,
            "start_delay_ms": int(start_time_sec * 1000)
        }


class VideoAudioExportThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, ordered_paths, ordered_nums, audio_clip_info, output_path, merge_mode, audio_settings):
        super().__init__()
        self.ordered_paths = ordered_paths
        self.ordered_nums = ordered_nums
        self.audio_clip_info = audio_clip_info
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

            a_filters = {}
            for idx, num in enumerate(self.ordered_nums):
                a_filters[idx] = af_str if num == target_track else "aresample=async=1000"

            cmd = ["ffmpeg", "-y", "-i", self.ordered_paths[0], "-i", self.ordered_paths[1]]

            # Append external standalone audio track if present on the timeline
            if self.audio_clip_info:
                cmd.extend(["-i", self.audio_clip_info["file_path"]])
                delay = self.audio_clip_info["start_delay_ms"]
                audio_overlay_filter = f"adelay={delay}|{delay},volume=1.0[aext]; "
                mix_inputs = 3
                ext_mix = "[aext]"
            else:
                audio_overlay_filter = ""
                mix_inputs = 2
                ext_mix = ""

            if self.merge_mode == "Sequence (Track 1 followed by Track 2)":
                filter_complex = (
                    f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0]; "
                    f"[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1]; "
                    f"[0:a]{a_filters[0]}[a0]; [1:a]{a_filters[1]}[a1]; "
                    f"{audio_overlay_filter}"
                    f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][amix]; "
                    f"{'[2:a]' + audio_overlay_filter if self.audio_clip_info else ''}"
                    f"{'[amix]' + ext_mix + 'amix=inputs=2:duration=first[a]' if self.audio_clip_info else '[amix]anull[a]'}"
                )
            else:
                filter_complex = (
                    f"[0:v]scale=-1:720[v0]; [1:v]scale=-1:720[v1]; "
                    f"[v0][v1]hstack=inputs=2[v]; "
                    f"[0:a]{a_filters[0]}[a0]; [1:a]{a_filters[1]}[a1]; "
                    f"{'[2:a]' + audio_overlay_filter if self.audio_clip_info else ''}"
                    f"[a0][a1]{ext_mix}amix=inputs={mix_inputs}:duration=longest[a]"
                )

            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                self.output_path
            ])

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            _, stderr = process.communicate()

            if process.returncode == 0:
                self.finished.emit(True, "Export completed successfully with timeline setup!")
            else:
                self.finished.emit(False, f"FFmpeg error:\n{stderr[-400:]}")

        except Exception as e:
            self.finished.emit(False, str(e))


class ModernMacVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iMovie Style Video Studio & Timeline")
        self.resize(1180, 950)

        self.track1_path = None
        self.track2_path = None
        self.standalone_audio_path = None
        self.active_audio_track = 1
        self.audio_segments = {1: None, 2: None, "audio_track": None}

        # Media Players
        self.player_t1 = QMediaPlayer()
        self.audio_t1 = QAudioOutput()
        self.player_t1.setAudioOutput(self.audio_t1)

        self.player_t2 = QMediaPlayer()
        self.audio_t2 = QAudioOutput()
        self.player_t2.setAudioOutput(self.audio_t2)

        self.audio_preview_player = QMediaPlayer()
        self.audio_preview_output = QAudioOutput()
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)

        self.setup_ui()
        self.apply_dark_theme()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. Media Loader Section
        tracks_group = QGroupBox("1. Import Media Clips")
        tracks_layout = QVBoxLayout(tracks_group)

        t1_box = QHBoxLayout()
        self.btn_t1 = QPushButton("Load Video Slot 1")
        self.btn_t1.clicked.connect(lambda: self.open_file(track_num=1, is_audio=False))
        self.lbl_t1_info = QLabel("No video loaded")
        self.lbl_t1_info.setStyleSheet("color: #888888; font-weight: bold;")
        t1_box.addWidget(self.btn_t1)
        t1_box.addWidget(self.lbl_t1_info)
        t1_box.addStretch()
        tracks_layout.addLayout(t1_box)

        t2_box = QHBoxLayout()
        self.btn_t2 = QPushButton("Load Video Slot 2")
        self.btn_t2.clicked.connect(lambda: self.open_file(track_num=2, is_audio=False))
        self.lbl_t2_info = QLabel("No video loaded")
        self.lbl_t2_info.setStyleSheet("color: #888888; font-weight: bold;")
        t2_box.addWidget(self.btn_t2)
        t2_box.addWidget(self.lbl_t2_info)
        t2_box.addStretch()
        tracks_layout.addLayout(t2_box)

        # Audio Track Loader
        ta_box = QHBoxLayout()
        self.btn_ta = QPushButton("🎵 Load Audio to Timeline Track")
        self.btn_ta.setStyleSheet("background-color: #6f42c1; color: white; font-weight: bold;")
        self.btn_ta.clicked.connect(lambda: self.open_file(track_num=3, is_audio=True))
        self.lbl_ta_info = QLabel("No audio track loaded")
        self.lbl_ta_info.setStyleSheet("color: #888888; font-weight: bold;")
        ta_box.addWidget(self.btn_ta)
        ta_box.addWidget(self.lbl_ta_info)
        ta_box.addStretch()
        tracks_layout.addLayout(ta_box)

        main_layout.addWidget(tracks_group)

        # 2. Video Previews Section
        previews_layout = QHBoxLayout()

        t1_preview_box = QVBoxLayout()
        t1_title = QLabel("Video Slot 1 Preview")
        t1_title.setAlignment(Qt.AlignCenter)
        t1_title.setStyleSheet("font-weight: bold; color: #0d6efd;")
        self.video_widget_t1 = QVideoWidget()
        self.video_widget_t1.setStyleSheet("background-color: #000000; border-radius: 6px;")
        self.player_t1.setVideoOutput(self.video_widget_t1)
        t1_preview_box.addWidget(t1_title)
        t1_preview_box.addWidget(self.video_widget_t1)

        t2_preview_box = QVBoxLayout()
        t2_title = QLabel("Video Slot 2 Preview")
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
        self.btn_play = QPushButton("Play Video Previews")
        self.btn_play.clicked.connect(self.toggle_play)

        self.lbl_time = QLabel("00:00 / 00:00")

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.sliderMoved.connect(self.set_player_position)

        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.timeline_slider)
        playback_layout.addWidget(self.lbl_time)
        main_layout.addLayout(playback_layout)

        # 3. CapCut / iMovie Interactive Sequence Timeline
        timeline_group = QGroupBox("2. Timeline Sequence Editor (iMovie Track Style)")
        timeline_layout = QVBoxLayout(timeline_group)
        self.timeline_view = CapCutTimelineView()
        timeline_layout.addWidget(self.timeline_view)
        main_layout.addWidget(timeline_group)

        # 4. Integrated Audio Waveform Editor
        audio_group = QGroupBox("3. Integrated Audio Waveform Editor")
        audio_main_layout = QVBoxLayout(audio_group)

        target_box = QHBoxLayout()
        target_box.addWidget(QLabel("Edit Audio Stream For:"))
        
        self.rb_track1 = QRadioButton("Video Slot 1")
        self.rb_track1.setChecked(True)
        self.rb_track1.toggled.connect(self.on_audio_target_changed)
        
        self.rb_track2 = QRadioButton("Video Slot 2")
        self.rb_track2.toggled.connect(self.on_audio_target_changed)

        target_box.addWidget(self.rb_track1)
        target_box.addWidget(self.rb_track2)
        target_box.addStretch()

        self.btn_preview_audio = QPushButton("🔊 Preview Audio Trim")
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

        self.fig = Figure(figsize=(8, 1.5), facecolor="#121212")
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#1E1E1E")
        self.ax.tick_params(colors="white")
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.ax.spines['top'].set_color('#3A3A3A')
        self.ax.spines['right'].set_color('#3A3A3A')
        audio_main_layout.addWidget(self.canvas)

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

        # 5. Export Section
        merge_box = QHBoxLayout()
        merge_box.addWidget(QLabel("Merge Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "Sequence (Track 1 followed by Track 2)",
            "Side-by-Side (Split Screen)"
        ])
        merge_box.addWidget(self.combo_mode)
        
        self.btn_export = QPushButton("Merge & Export Final Timeline")
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

        self.player_t1.positionChanged.connect(self.position_changed)

    def open_file(self, track_num, is_audio=False):
        filter_str = "Audio Files (*.mp3 *.wav *.aac *.m4a)" if is_audio else "Video Files (*.mp4 *.mov *.avi)"
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Media File", "", filter_str)
        if file_path:
            filename = os.path.basename(file_path)
            try:
                seg = AudioSegment.from_file(file_path)
                duration_sec = len(seg) / 1000.0

                if is_audio:
                    self.standalone_audio_path = file_path
                    self.lbl_ta_info.setText(filename)
                    self.timeline_view.add_clip(file_path, duration_sec, track_num=3, is_audio=True)
                else:
                    if track_num == 1:
                        self.track1_path = file_path
                        self.lbl_t1_info.setText(filename)
                        self.player_t1.setSource(QUrl.fromLocalFile(file_path))
                    elif track_num == 2:
                        self.track2_path = file_path
                        self.lbl_t2_info.setText(filename)
                        self.player_t2.setSource(QUrl.fromLocalFile(file_path))

                    self.audio_segments[track_num] = seg
                    self.timeline_view.add_clip(file_path, duration_sec, track_num=track_num, is_audio=False)

                    if self.active_audio_track == track_num:
                        self.render_waveform(seg)
                        self.btn_preview_audio.setEnabled(True)

            except Exception as e:
                print(f"Failed to load media clip: {e}")

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
        seg = self.audio_segments.get(self.active_audio_track)
        if not seg:
            return

        self.player_t1.pause()
        self.player_t2.pause()
        self.btn_play.setText("Play Video Previews")

        if self.audio_preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.audio_preview_player.stop()
            self.btn_preview_audio.setText("🔊 Preview Audio Trim")
            return

        start_ms = int(self.spin_start.value() * 1000)
        end_ms = int(self.spin_end.value() * 1000)
        gain_db = self.spin_gain.value()
        fade_in_ms = int(self.spin_fade_in.value() * 1000)
        fade_out_ms = int(self.spin_fade_out.value() * 1000)

        edited_seg = seg[start_ms:end_ms] if end_ms > start_ms else seg[start_ms:]

        if gain_db != 0:
            edited_seg = edited_seg + gain_db

        if fade_in_ms > 0:
            edited_seg = edited_seg.fade_in(fade_in_ms)
        if fade_out_ms > 0 and len(edited_seg) > fade_out_ms:
            edited_seg = edited_seg.fade_out(fade_out_ms)

        temp_dir = tempfile.gettempdir()
        self.temp_preview_file = os.path.join(temp_dir, "temp_audio_preview.wav")
        edited_seg.export(self.temp_preview_file, format="wav")

        self.audio_preview_player.setSource(QUrl.fromLocalFile(self.temp_preview_file))
        self.audio_preview_player.play()
        self.btn_preview_audio.setText("⏹ Stop Preview")

        self.audio_preview_player.playbackStateChanged.connect(self.on_preview_state_changed)

    def on_preview_state_changed(self, state):
        if state != QMediaPlayer.PlayingState:
            self.btn_preview_audio.setText("🔊 Preview Audio Trim")

    def toggle_play(self):
        if self.audio_preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.audio_preview_player.stop()

        is_playing = (self.player_t1.playbackState() == QMediaPlayer.PlayingState)

        if is_playing:
            self.player_t1.pause()
            self.player_t2.pause()
            self.btn_play.setText("Play Video Previews")
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
        self.btn_play.setText("Play Video Previews")

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
        ordered_paths, ordered_nums = self.timeline_view.get_ordered_video_tracks()
        audio_clip_info = self.timeline_view.get_audio_clip_info()

        self.export_thread = VideoAudioExportThread(
            ordered_paths, ordered_nums, audio_clip_info, output_path, mode, audio_settings
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

    def closeEvent(self, event):
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, "temp_audio_preview.wav")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ModernMacVideoEditor()
    window.show()
    sys.exit(app.exec())