import sys
import os
import subprocess
import numpy as np

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from pydub import AudioSegment

from PySide6.QtCore import Qt, QUrl, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QSlider, QLabel, QProgressBar, QMessageBox,
    QGroupBox, QDoubleSpinBox, QFormLayout, QGraphicsScene,
    QGraphicsView, QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsLineItem
)
from PySide6.QtGui import QColor, QBrush, QPen, QFont, QPainter, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class BoundAudioClipItem(QGraphicsRectItem):
    """Audio clip visually bound below its corresponding parent Video clip."""
    def __init__(self, x, y, width, height, title, parent_clip):
        super().__init__(x, y, width, height, parent_clip)
        self.setBrush(QBrush(QColor("#6f42c1")))
        self.setPen(QPen(QColor("#FFFFFF"), 1))
        self.setFlags(QGraphicsItem.ItemIsSelectable)

        self.text_item = QGraphicsTextItem(f"🎵 {title}", self)
        self.text_item.setDefaultTextColor(Qt.white)
        self.text_item.setFont(QFont("Arial", 8, QFont.Bold))
        self.text_item.setPos(x + 5, y + (height / 6))


class TimelineClipItem(QGraphicsRectItem):
    """Draggable Video Clip Item."""
    def __init__(self, x, y, width, height, title, file_path, duration_sec, audio_seg=None):
        super().__init__(x, y, width, height)
        self.title = title
        self.file_path = file_path
        self.source_duration = duration_sec
        self.audio_seg = audio_seg
        
        self.trim_start = 0.0
        self.trim_end = duration_sec

        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        
        self.setBrush(QBrush(QColor("#28a745")))
        self.setPen(QPen(QColor("#FFFFFF"), 1))

        self.text_item = QGraphicsTextItem(f"🎬 {title}", self)
        self.text_item.setDefaultTextColor(Qt.white)
        self.text_item.setFont(QFont("Arial", 9, QFont.Bold))
        self.text_item.setPos(x + 5, y + (height / 4))

        self.bound_audio = BoundAudioClipItem(
            x, y + height + 15, width, 25, title, self
        )

    @property
    def edited_duration(self):
        return max(0.01, self.trim_end - self.trim_start)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            new_pos = value
            new_pos.setY(0)
            if new_pos.x() < 0:
                new_pos.setX(0)
            return new_pos
        return super().itemChange(change, value)


class iMovieTimelineView(QGraphicsView):
    """Timeline canvas that safely manages clip objects."""
    clip_selected_signal = Signal(object)
    timeline_changed_signal = Signal()

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #3A3A3A; border-radius: 6px;")
        self.setFixedHeight(160)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.pixels_per_second = 20
        self.video_clips = []
        self.copied_clip_data = None

        self.playhead = QGraphicsLineItem(0, 0, 0, 150)
        self.playhead.setPen(QPen(QColor("#FF3B30"), 2))
        self.playhead.setZValue(100)
        self.scene.addItem(self.playhead)

        self.draw_track_headers()
        self.scene.selectionChanged.connect(self.on_selection_changed)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.reflow_clips()

    def draw_track_headers(self):
        v_label = self.scene.addText("🎬 Video Track")
        v_label.setDefaultTextColor(QColor("#888888"))
        v_label.setPos(5, 2)

        a_label = self.scene.addText("🎵 Bound Audio Track")
        a_label.setDefaultTextColor(QColor("#888888"))
        a_label.setPos(5, 62)

        self.scene.addLine(0, 60, 3000, 60, QPen(QColor("#333333"), 2))

    def get_sorted_clips(self):
        return sorted(self.video_clips, key=lambda c: c.scenePos().x() + c.rect().x())

    def add_clip(self, file_path, duration_sec, audio_seg=None):
        filename = os.path.basename(file_path)
        clip_width = max(40, duration_sec * self.pixels_per_second)

        clip = TimelineClipItem(0, 22, clip_width, 32, filename, file_path, duration_sec, audio_seg)

        self.scene.addItem(clip)
        self.video_clips.append(clip)
        self.reflow_clips()

    def reflow_clips(self):
        self.video_clips = self.get_sorted_clips()
        current_x = 0
        
        for clip in self.video_clips:
            width = max(20, clip.edited_duration * self.pixels_per_second)
            clip.setRect(0, 22, width, 32)
            clip.bound_audio.setRect(0, 69, width, 25)
            clip.setPos(current_x, 0)
            current_x += width

        self.scene.setSceneRect(0, 0, max(1200, current_x + 200), 150)
        self.timeline_changed_signal.emit()

    def set_playhead_time(self, time_sec):
        x = time_sec * self.pixels_per_second
        self.playhead.setLine(x, 0, x, 150)

    def on_selection_changed(self):
        selected_items = self.scene.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        if isinstance(item, TimelineClipItem):
            self.clip_selected_signal.emit(item)
        elif isinstance(item, BoundAudioClipItem):
            self.clip_selected_signal.emit(item.parentItem())

    def cut_clip_at_playhead(self):
        selected = [i for i in self.scene.selectedItems() if isinstance(i, TimelineClipItem)]
        if not selected:
            return

        clip = selected[0]
        clip_x = clip.scenePos().x() + clip.rect().x()
        playhead_x = self.playhead.line().x1()

        if clip_x < playhead_x < (clip_x + clip.rect().width()):
            split_offset_sec = (playhead_x - clip_x) / self.pixels_per_second
            orig_trim_end = clip.trim_end

            clip.trim_end = clip.trim_start + split_offset_sec

            second_clip = TimelineClipItem(
                0, 22, 100, 32,
                f"{clip.title} (Cut)", clip.file_path, clip.source_duration, clip.audio_seg
            )
            second_clip.trim_start = clip.trim_end
            second_clip.trim_end = orig_trim_end

            self.scene.addItem(second_clip)
            self.video_clips.append(second_clip)
            self.reflow_clips()

    def copy_selected_clip(self):
        selected = [i for i in self.scene.selectedItems() if isinstance(i, TimelineClipItem)]
        if selected:
            clip = selected[0]
            self.copied_clip_data = {
                "title": clip.title,
                "file_path": clip.file_path,
                "source_duration": clip.source_duration,
                "trim_start": clip.trim_start,
                "trim_end": clip.trim_end,
                "audio_seg": clip.audio_seg
            }

    def paste_clip(self):
        if self.copied_clip_data:
            clip = TimelineClipItem(
                0, 22, 100, 32,
                self.copied_clip_data["title"],
                self.copied_clip_data["file_path"],
                self.copied_clip_data["source_duration"],
                self.copied_clip_data["audio_seg"]
            )
            clip.trim_start = self.copied_clip_data["trim_start"]
            clip.trim_end = self.copied_clip_data["trim_end"]

            self.scene.addItem(clip)
            self.video_clips.append(clip)
            self.reflow_clips()

    def delete_selected_clip(self):
        selected = [i for i in self.scene.selectedItems() if isinstance(i, TimelineClipItem)]
        for clip in selected:
            self.scene.removeItem(clip)
            if clip in self.video_clips:
                self.video_clips.remove(clip)
        self.reflow_clips()


class ExportThread(QThread):
    """Safe background FFmpeg thread for merging clips."""
    export_finished = Signal(bool, str)

    def __init__(self, ordered_clips, target_file):
        super().__init__()
        self.ordered_clips = ordered_clips
        self.target_file = target_file

    def has_audio_stream(self, file_path):
        """Checks whether the video file actually contains an audio track."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "default=nw=1:nk=1", file_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return "audio" in res.stdout.lower()
        except Exception:
            return False

    def run(self):
        if not self.ordered_clips:
            self.export_finished.emit(False, "No clips in timeline.")
            return

        try:
            cmd = ["ffmpeg", "-y"]
            filter_inputs = ""
            concat_str = ""

            for idx, clip in enumerate(self.ordered_clips):
                cmd.extend(["-i", clip.file_path])
                
                # Video Filter
                filter_inputs += (
                    f"[{idx}:v]trim=start={clip.trim_start}:end={clip.trim_end},"
                    f"setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=decrease,"
                    f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1,fps=30[v{idx}]; "
                )
                
                # Safe Audio Filter Handling
                if self.has_audio_stream(clip.file_path):
                    filter_inputs += (
                        f"[{idx}:a]atrim=start={clip.trim_start}:end={clip.trim_end},"
                        f"asetpts=PTS-STARTPTS,aresample=44100:async=1[a{idx}]; "
                    )
                else:
                    filter_inputs += (
                        f"anullsrc=r=44100:cl=stereo,atrim=end={clip.edited_duration},"
                        f"asetpts=PTS-STARTPTS[a{idx}]; "
                    )

                concat_str += f"[v{idx}][a{idx}]"

            filter_complex = f"{filter_inputs}{concat_str}concat=n={len(self.ordered_clips)}:v=1:a=1[v][a]"
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k",
                self.target_file
            ])

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                self.export_finished.emit(True, "Export completed successfully!")
            else:
                self.export_finished.emit(False, res.stderr)
        except Exception as e:
            self.export_finished.emit(False, str(e))


class ModernMacVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iMovie Studio")
        self.resize(1180, 960)

        self.selected_clip = None

        self.main_audio = QAudioOutput()
        self.main_audio.setVolume(1.0)
        self.main_player = QMediaPlayer()
        self.main_player.setAudioOutput(self.main_audio)

        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(33)
        self.playback_timer.timeout.connect(self.sync_timeline_playback)

        self.setup_ui()
        self.setup_keyboard_shortcuts()
        self.apply_dark_theme()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        toolbar_box = QHBoxLayout()
        self.btn_load_video = QPushButton("➕ Import Video Clip")
        self.btn_load_video.clicked.connect(self.import_video)
        toolbar_box.addWidget(self.btn_load_video)

        btn_cut = QPushButton("✂ Cut At Playhead (S)")
        btn_cut.clicked.connect(lambda: self.timeline_view.cut_clip_at_playhead())
        btn_copy = QPushButton("📋 Copy (Cmd+C)")
        btn_copy.clicked.connect(lambda: self.timeline_view.copy_selected_clip())
        btn_paste = QPushButton("📌 Paste (Cmd+V)")
        btn_paste.clicked.connect(lambda: self.timeline_view.paste_clip())
        btn_del = QPushButton("🗑 Delete (Del)")
        btn_del.clicked.connect(lambda: self.timeline_view.delete_selected_clip())

        toolbar_box.addWidget(btn_cut)
        toolbar_box.addWidget(btn_copy)
        toolbar_box.addWidget(btn_paste)
        toolbar_box.addWidget(btn_del)
        toolbar_box.addStretch()

        main_layout.addLayout(toolbar_box)

        preview_group = QGroupBox("Gapless Live Monitor")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_widget = QVideoWidget()
        self.preview_widget.setStyleSheet("background-color: #000000; border-radius: 6px;")
        self.preview_widget.setMinimumHeight(280)
        self.main_player.setVideoOutput(self.preview_widget)
        preview_layout.addWidget(self.preview_widget)

        playback_layout = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play Timeline")
        self.btn_play.clicked.connect(self.toggle_play)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.sliderMoved.connect(self.seek_timeline)

        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.timeline_slider)
        playback_layout.addWidget(self.lbl_time)
        preview_layout.addLayout(playback_layout)

        main_layout.addWidget(preview_group)

        timeline_group = QGroupBox("iMovie Multi-Track Timeline Sequence")
        timeline_layout = QVBoxLayout(timeline_group)
        self.timeline_view = iMovieTimelineView()
        self.timeline_view.clip_selected_signal.connect(self.on_clip_selected)
        timeline_layout.addWidget(self.timeline_view)
        main_layout.addWidget(timeline_group)

        audio_group = QGroupBox("Audio Waveform Editor (Active Clip)")
        audio_main_layout = QVBoxLayout(audio_group)

        self.lbl_selected_info = QLabel("Select a clip on the timeline to view waveform data")
        self.lbl_selected_info.setStyleSheet("color: #17a2b8; font-weight: bold;")
        audio_main_layout.addWidget(self.lbl_selected_info)

        self.fig = Figure(figsize=(8, 1.3), facecolor="#121212")
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#1E1E1E")
        self.ax.tick_params(colors="white")
        audio_main_layout.addWidget(self.canvas)

        controls_layout = QHBoxLayout()
        form1 = QFormLayout()
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setSuffix(" sec")
        self.spin_start.valueChanged.connect(self.on_audio_parameters_changed)
        form1.addRow("Trim Start:", self.spin_start)

        self.spin_end = QDoubleSpinBox()
        self.spin_end.setSuffix(" sec")
        self.spin_end.valueChanged.connect(self.on_audio_parameters_changed)
        form1.addRow("Trim End:", self.spin_end)

        controls_layout.addLayout(form1)
        audio_main_layout.addLayout(controls_layout)

        main_layout.addWidget(audio_group)

        export_box = QHBoxLayout()
        self.btn_export = QPushButton("Merge & Export Final Video")
        self.btn_export.setStyleSheet("background-color: #0d6efd; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_export.clicked.connect(self.export_timeline)
        export_box.addStretch()
        export_box.addWidget(self.btn_export)
        main_layout.addLayout(export_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

    def setup_keyboard_shortcuts(self):
        QShortcut(QKeySequence("S"), self, lambda: self.timeline_view.cut_clip_at_playhead())
        QShortcut(QKeySequence.Copy, self, lambda: self.timeline_view.copy_selected_clip())
        QShortcut(QKeySequence.Paste, self, lambda: self.timeline_view.paste_clip())
        QShortcut(QKeySequence.Delete, self, lambda: self.timeline_view.delete_selected_clip())

    def import_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Video", "", "Video Files (*.mp4 *.mov *.avi)")
        if file_path:
            try:
                seg = AudioSegment.from_file(file_path)
                duration_sec = len(seg) / 1000.0
                self.timeline_view.add_clip(file_path, duration_sec, seg)
            except Exception:
                self.timeline_view.add_clip(file_path, 5.0, None)

    def on_clip_selected(self, clip_item):
        self.selected_clip = clip_item
        self.lbl_selected_info.setText(f"Editing: {clip_item.title}")

        self.spin_start.blockSignals(True)
        self.spin_end.blockSignals(True)
        self.spin_start.setRange(0, clip_item.source_duration)
        self.spin_end.setRange(0, clip_item.source_duration)
        self.spin_start.setValue(clip_item.trim_start)
        self.spin_end.setValue(clip_item.trim_end)
        self.spin_start.blockSignals(False)
        self.spin_end.blockSignals(False)

        if clip_item.audio_seg:
            self.render_waveform(clip_item.audio_seg, clip_item.trim_start, clip_item.trim_end)

        self.main_player.setSource(QUrl.fromLocalFile(clip_item.file_path))
        self.main_player.setPosition(int(clip_item.trim_start * 1000))

    def render_waveform(self, audio_segment, trim_start, trim_end):
        try:
            self.ax.clear()
            samples = np.array(audio_segment.get_array_of_samples())
            if len(samples) > 0:
                subsample = max(1, len(samples) // 1000)
                reduced = samples[::subsample]
                duration_sec = len(audio_segment) / 1000.0
                time_axis = np.linspace(0, duration_sec, num=len(reduced))

                self.ax.plot(time_axis, reduced, color="#6f42c1", alpha=0.7)
                self.ax.axvspan(trim_start, trim_end, color="#28a745", alpha=0.3)

            self.fig.tight_layout()
            self.canvas.draw_idle()
        except Exception as e:
            print(f"Waveform rendering skipped safely: {e}")

    def on_audio_parameters_changed(self):
        if self.selected_clip:
            self.selected_clip.trim_start = self.spin_start.value()
            self.selected_clip.trim_end = self.spin_end.value()
            self.timeline_view.reflow_clips()
            if self.selected_clip.audio_seg:
                self.render_waveform(self.selected_clip.audio_seg, self.spin_start.value(), self.spin_end.value())

    def toggle_play(self):
        if self.main_player.playbackState() == QMediaPlayer.PlayingState:
            self.main_player.pause()
            self.playback_timer.stop()
            self.btn_play.setText("▶ Play Timeline")
        else:
            if self.selected_clip:
                self.main_player.play()
                self.playback_timer.start()
                self.btn_play.setText("⏸ Pause")

    def sync_timeline_playback(self):
        if not self.selected_clip:
            return

        current_ms = self.main_player.position()
        end_ms = int(self.selected_clip.trim_end * 1000)

        if current_ms >= end_ms:
            self.main_player.pause()
            self.playback_timer.stop()
            self.btn_play.setText("▶ Play Timeline")
            self.main_player.setPosition(int(self.selected_clip.trim_start * 1000))
        else:
            sec = current_ms / 1000.0
            self.timeline_view.set_playhead_time(sec)

    def seek_timeline(self, val):
        if self.selected_clip:
            clip_dur = self.selected_clip.edited_duration
            target_sec = self.selected_clip.trim_start + ((val / 1000.0) * clip_dur)
            self.main_player.setPosition(int(target_sec * 1000))
            self.timeline_view.set_playhead_time(target_sec)

    def export_timeline(self):
        output_path, _ = QFileDialog.getSaveFileName(self, "Export Final Timeline", "output.mp4", "MP4 Files (*.mp4)")
        if output_path:
            self.main_player.pause()
            self.progress_bar.setVisible(True)
            self.btn_export.setEnabled(False)

            ordered_clips = self.timeline_view.get_sorted_clips()
            self.export_thread = ExportThread(ordered_clips, output_path)
            self.export_thread.export_finished.connect(self.on_export_finished)
            self.export_thread.start()

    def on_export_finished(self, success, msg):
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        if success:
            QMessageBox.information(self, "Export Status", msg)
        else:
            QMessageBox.critical(self, "Export Error", msg)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QGroupBox { color: #E0E0E0; font-weight: bold; border: 1px solid #3A3A3A; border-radius: 6px; margin-top: 6px; padding-top: 10px; }
            QLabel { color: #E0E0E0; font-size: 13px; }
            QDoubleSpinBox { background-color: #2D2D2D; color: #FFFFFF; border: 1px solid #3A3A3A; border-radius: 4px; padding: 4px 8px; }
            QPushButton { background-color: #2D2D2D; color: #FFFFFF; border: 1px solid #3A3A3A; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #3D3D3D; }
        """)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ModernMacVideoEditor()
    window.show()
    sys.exit(app.exec())