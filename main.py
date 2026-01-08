import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QProcess, QPointF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QBrush, QMovie, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def find_ffmpeg_tool(tool_name: str) -> str | None:
    suffixes = (".exe", "") if sys.platform.startswith("win") else ("", ".exe")
    candidates: list[Path] = []

    script_dir = Path(__file__).resolve().parent
    for suffix in suffixes:
        candidates.append(script_dir / "ffmpeg_bin" / f"{tool_name}{suffix}")
    for suffix in suffixes:
        candidates.append(script_dir / f"{tool_name}{suffix}")

    exe_dir = Path(sys.executable).resolve().parent
    for suffix in suffixes:
        candidates.append(exe_dir / "ffmpeg_bin" / f"{tool_name}{suffix}")
        candidates.append(exe_dir / f"{tool_name}{suffix}")
        candidates.append(exe_dir.parent / "Resources" / "ffmpeg_bin" / f"{tool_name}{suffix}")
        candidates.append(exe_dir.parent / "Resources" / f"{tool_name}{suffix}")
        candidates.append(exe_dir.parent / "Frameworks" / "ffmpeg_bin" / f"{tool_name}{suffix}")
        candidates.append(exe_dir.parent / "Frameworks" / f"{tool_name}{suffix}")

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        for suffix in suffixes:
            candidates.append(base / "ffmpeg_bin" / f"{tool_name}{suffix}")
            candidates.append(base / f"{tool_name}{suffix}")

    for path in candidates:
        if path.is_file():
            return str(path)

    for candidate in (tool_name, f"{tool_name}.exe"):
        found = shutil.which(candidate)
        if found:
            return found

    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        base = Path(prefix)
        for suffix in suffixes:
            path = base / f"{tool_name}{suffix}"
            if path.is_file():
                return str(path)
    return None


def probe_video_size(ffprobe_path: str, video_path: str) -> tuple[int, int]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("No video stream found")

    width = int(streams[0]["width"])
    height = int(streams[0]["height"])
    return width, height


@dataclass(frozen=True)
class CropSpec:
    x: int
    y: int
    side: int


@dataclass
class SizeSearchState:
    target_bytes: int
    quality_min: int
    quality_max: int
    crop: CropSpec
    out_side: int
    fps: int
    final_path: Path
    phase: str
    low: int
    high: int
    attempt: int = 0
    max_attempts: int = 12
    best_quality: int | None = None
    best_size_bytes: int | None = None
    best_temp_path: Path | None = None
    temp_paths: list[Path] = field(default_factory=list)


class CropRectItem(QGraphicsRectItem):
    def __init__(
        self,
        side: float,
        bounds: QRectF,
        on_moved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(0, 0, side, side)
        self._bounds = QRectF(bounds)
        self._on_moved = on_moved
        self._handle_size = 10.0
        self._min_side = 16.0
        self._resizing = False
        self._active_handle: str | None = None
        self._anchor_scene: QPointF | None = None
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

        pen = QPen(Qt.red)
        pen.setWidth(3)
        self.setPen(pen)
        self.setBrush(QBrush(Qt.transparent))

    def set_bounds(self, bounds: QRectF) -> None:
        self._bounds = QRectF(bounds)
        self.setPos(self._clamped_pos(self.pos()))

    def set_on_moved(self, on_moved: Callable[[], None] | None) -> None:
        self._on_moved = on_moved

    def _clamped_pos(self, pos: QPointF) -> QPointF:
        side = float(self.rect().width())
        min_x = self._bounds.left()
        min_y = self._bounds.top()
        max_x = self._bounds.left() + self._bounds.width() - side
        max_y = self._bounds.top() + self._bounds.height() - side

        clamped_x = min(max(pos.x(), min_x), max_x)
        clamped_y = min(max(pos.y(), min_y), max_y)
        return QPointF(clamped_x, clamped_y)

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionChange and isinstance(value, QPointF):
            return self._clamped_pos(value)
        if change == QGraphicsRectItem.ItemPositionHasChanged and self._on_moved:
            self._on_moved()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        side = float(self.rect().width())

        circle_pen = QPen(Qt.red)
        circle_pen.setWidth(2)
        circle_pen.setStyle(Qt.DashLine)
        inset = circle_pen.widthF() / 2.0
        if side > inset * 2:
            painter.save()
            painter.setPen(circle_pen)
            painter.setBrush(Qt.transparent)
            painter.drawEllipse(QRectF(inset, inset, side - inset * 2, side - inset * 2))
            painter.restore()

        h = float(self._handle_size)
        half = h / 2.0
        painter.save()
        painter.setPen(Qt.red)
        painter.setBrush(Qt.red)
        for x, y in ((0.0, 0.0), (side, 0.0), (0.0, side), (side, side)):
            painter.drawRect(QRectF(x - half, y - half, h, h))
        painter.restore()

    def _hit_test_handle(self, pos: QPointF) -> str | None:
        side = float(self.rect().width())
        tol = float(self._handle_size) * 1.2

        def near(cx: float, cy: float) -> bool:
            return abs(pos.x() - cx) <= tol and abs(pos.y() - cy) <= tol

        if near(0.0, 0.0):
            return "tl"
        if near(side, 0.0):
            return "tr"
        if near(0.0, side):
            return "bl"
        if near(side, side):
            return "br"
        return None

    def _set_cursor_for_handle(self, handle: str | None) -> None:
        if handle in ("tl", "br"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif handle in ("tr", "bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def hoverMoveEvent(self, event) -> None:
        if self._resizing:
            event.accept()
            return
        self._set_cursor_for_handle(self._hit_test_handle(event.pos()))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        if not self._resizing:
            self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self._hit_test_handle(event.pos())
            if handle:
                self._resizing = True
                self._active_handle = handle
                rect = self.rect()
                if handle == "br":
                    self._anchor_scene = self.mapToScene(QPointF(0.0, 0.0))
                elif handle == "tl":
                    self._anchor_scene = self.mapToScene(QPointF(rect.width(), rect.height()))
                elif handle == "tr":
                    self._anchor_scene = self.mapToScene(QPointF(0.0, rect.height()))
                else:  # "bl"
                    self._anchor_scene = self.mapToScene(QPointF(rect.width(), 0.0))
                self._set_cursor_for_handle(handle)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._resizing or not self._active_handle or not self._anchor_scene:
            super().mouseMoveEvent(event)
            return

        anchor = self._anchor_scene
        p = event.scenePos()
        handle = self._active_handle

        if handle == "br":
            desired_side = max(p.x() - anchor.x(), p.y() - anchor.y())
            max_side = min(self._bounds.right() - anchor.x(), self._bounds.bottom() - anchor.y())
            top_left = anchor
        elif handle == "tl":
            desired_side = max(anchor.x() - p.x(), anchor.y() - p.y())
            max_side = min(anchor.x() - self._bounds.left(), anchor.y() - self._bounds.top())
            top_left = QPointF(anchor.x() - desired_side, anchor.y() - desired_side)
        elif handle == "tr":
            desired_side = max(p.x() - anchor.x(), anchor.y() - p.y())
            max_side = min(self._bounds.right() - anchor.x(), anchor.y() - self._bounds.top())
            top_left = QPointF(anchor.x(), anchor.y() - desired_side)
        else:  # "bl"
            desired_side = max(anchor.x() - p.x(), p.y() - anchor.y())
            max_side = min(anchor.x() - self._bounds.left(), self._bounds.bottom() - anchor.y())
            top_left = QPointF(anchor.x() - desired_side, anchor.y())

        if max_side <= 0:
            event.accept()
            return
        side = min(max_side, max(self._min_side, desired_side))
        if side <= 0:
            event.accept()
            return

        if handle == "tl":
            top_left = QPointF(anchor.x() - side, anchor.y() - side)
        elif handle == "tr":
            top_left = QPointF(anchor.x(), anchor.y() - side)
        elif handle == "bl":
            top_left = QPointF(anchor.x() - side, anchor.y())
        else:
            top_left = anchor

        self.setRect(0.0, 0.0, side, side)
        self.setPos(top_left)
        if self._on_moved:
            self._on_moved()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing and event.button() == Qt.LeftButton:
            self._resizing = False
            self._active_handle = None
            self._anchor_scene = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_scene()

    def fit_scene(self) -> None:
        scene = self.scene()
        if not scene:
            return
        rect = scene.sceneRect()
        if rect.isNull() or rect.isEmpty():
            return
        self.fitInView(rect, Qt.KeepAspectRatio)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("动态勋章制作")

        self._ffmpeg_path = find_ffmpeg_tool("ffmpeg")
        self._ffprobe_path = find_ffmpeg_tool("ffprobe")

        self._video_path: str | None = None
        self._video_size: tuple[int, int] | None = None
        self._output_dir: str | None = None
        self._ffmpeg_process: QProcess | None = None
        self._ffmpeg_output: list[str] = []
        self._pending_out_path: Path | None = None
        self._final_out_path: Path | None = None
        self._current_quality: int | None = None
        self._ffmpeg_finalized = False
        self._size_search: SizeSearchState | None = None
        self._job_was_canceled = False

        self._player = QMediaPlayer(self)
        try:
            self._player.setLoops(QMediaPlayer.Infinite)
        except Exception:
            pass

        self._scene = QGraphicsScene(self)

        self._video_item = QGraphicsVideoItem()
        self._video_item.setZValue(0)
        self._scene.addItem(self._video_item)

        self._video_pixmap_item = QGraphicsPixmapItem()
        self._video_pixmap_item.setZValue(1)
        self._video_pixmap_item.setVisible(False)
        self._scene.addItem(self._video_pixmap_item)

        self._player.setVideoOutput(self._video_item)

        self._crop_item: CropRectItem | None = None

        self._video_view = VideoView(self._scene)
        self._video_view.setMinimumHeight(320)

        self._playback_movie: QMovie | None = None
        self._playback_movie_initialized = False

        self._btn_add_video = QPushButton("添加视频")
        self._btn_add_video.clicked.connect(self._select_video)
        self._video_path_edit = QLineEdit()
        self._video_path_edit.setReadOnly(True)

        self._btn_output_dir = QPushButton("保存目录")
        self._btn_output_dir.clicked.connect(self._select_output_dir)
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setReadOnly(True)

        self._output_name_edit = QLineEdit()
        self._output_name_edit.setPlaceholderText("输出文件名（含 .webp）")
        self._output_name_edit.editingFinished.connect(self._ensure_output_suffix)

        self._quality_slider = QSlider(Qt.Horizontal)
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(100)
        self._quality_value_label = QLabel("100")
        self._quality_slider.valueChanged.connect(lambda v: self._quality_value_label.setText(str(v)))

        self._size_combo = QComboBox()
        self._size_combo.addItem("原始", None)
        self._size_combo.addItem("1080x1080", 1080)
        self._size_combo.addItem("800x800", 800)
        self._size_combo.addItem("600x600", 600)
        self._size_combo.setCurrentIndex(1)

        self._fps_combo = QComboBox()
        for fps in (60, 30, 24, 15):
            self._fps_combo.addItem(f"{fps} fps", fps)
        self._fps_combo.setCurrentIndex(1)

        self._limit_size_check = QCheckBox("限制大小")
        self._limit_size_mb_spin = QSpinBox()
        self._limit_size_mb_spin.setRange(1, 1024)
        self._limit_size_mb_spin.setValue(5)
        self._limit_size_mb_spin.setSuffix(" MB")
        self._limit_size_mb_spin.setEnabled(False)
        self._limit_size_check.toggled.connect(self._limit_size_mb_spin.setEnabled)

        self._btn_make = QPushButton("勋章制作")
        self._btn_make.clicked.connect(self._make_badge)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel_make_badge)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._status = QLabel("请选择一个视频")

        root = QWidget()
        layout = QVBoxLayout(root)

        row1 = QHBoxLayout()
        row1.addWidget(self._btn_add_video)
        row1.addWidget(self._video_path_edit, 1)
        layout.addLayout(row1)

        layout.addWidget(self._video_view, 1)

        settings = QGroupBox("输出设置")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)

        row2 = QHBoxLayout()
        row2.addWidget(self._btn_output_dir)
        row2.addWidget(self._output_dir_edit, 1)
        settings_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("输出文件名"))
        row3.addWidget(self._output_name_edit, 1)
        settings_layout.addLayout(row3)

        row_quality = QHBoxLayout()
        row_quality.addWidget(QLabel("质量"))
        row_quality.addWidget(self._quality_slider, 1)
        row_quality.addWidget(self._quality_value_label)
        settings_layout.addLayout(row_quality)

        row_size_fps = QHBoxLayout()
        row_size_fps.addWidget(QLabel("尺寸"))
        row_size_fps.addWidget(self._size_combo)
        row_size_fps.addSpacing(10)
        row_size_fps.addWidget(QLabel("帧率"))
        row_size_fps.addWidget(self._fps_combo)
        row_size_fps.addStretch(1)
        settings_layout.addLayout(row_size_fps)

        row_limit = QHBoxLayout()
        row_limit.addWidget(self._limit_size_check)
        row_limit.addWidget(QLabel("目标"))
        row_limit.addWidget(self._limit_size_mb_spin)
        row_limit.addStretch(1)
        settings_layout.addLayout(row_limit)

        layout.addWidget(settings)

        row4 = QHBoxLayout()
        row4.addWidget(self._btn_make)
        row4.addWidget(self._btn_cancel)
        row4.addWidget(self._progress, 1)
        layout.addLayout(row4)

        layout.addWidget(self._status)

        self.setCentralWidget(root)

    def _select_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频",
            "",
            "Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;All Files (*)",
        )
        if not file_path:
            return

        self._video_path = file_path
        self._video_path_edit.setText(file_path)

        input_path = Path(file_path)
        self._output_dir = str(input_path.resolve().parent)
        self._output_dir_edit.setText(self._output_dir)
        self._output_name_edit.setText(f"{input_path.stem}.webp")
        self._stop_playback_movie()

        if not self._ffprobe_path:
            QMessageBox.warning(
                self,
                "缺少 ffprobe",
                "未找到 ffprobe（通常和 ffmpeg 一起安装）。请将 ffprobe 放到程序目录下的 ffmpeg_bin 文件夹中，或安装 ffmpeg 并确保 ffprobe 在 PATH 中。",
            )
            return

        try:
            width, height = probe_video_size(self._ffprobe_path, file_path)
        except Exception as exc:
            QMessageBox.critical(self, "读取视频失败", str(exc))
            return

        self._video_size = (width, height)
        self._scene.setSceneRect(QRectF(0, 0, width, height))
        self._video_pixmap_item.setPixmap(QPixmap())
        self._video_pixmap_item.setVisible(False)
        self._video_item.setVisible(True)
        self._video_item.setPos(0, 0)
        self._video_item.setSize(QSizeF(width, height))

        side = height if width >= height else width
        x = (width - side) / 2.0
        y = (height - side) / 2.0

        bounds = QRectF(0, 0, width, height)
        if self._crop_item is None:
            self._crop_item = CropRectItem(float(side), bounds)
            self._scene.addItem(self._crop_item)
        else:
            self._crop_item.setRect(0, 0, float(side), float(side))
            self._crop_item.set_bounds(bounds)

        self._crop_item.setPos(QPointF(x, y))

        self._player.setSource(QUrl.fromLocalFile(file_path))
        self._player.play()

        self._video_view.fit_scene()
        self._status.setText("拖动红色正方形选择裁剪区域（拖动四角可缩放）")

    def _select_output_dir(self) -> None:
        start_dir = self._output_dir or ""
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", start_dir)
        if not directory:
            return
        self._output_dir = directory
        self._output_dir_edit.setText(directory)

    def _ensure_output_suffix(self) -> None:
        text = self._output_name_edit.text().strip()
        if not text:
            return
        if not text.lower().endswith(".webp"):
            self._output_name_edit.setText(f"{text}.webp")

    def _stop_playback_movie(self) -> None:
        if self._playback_movie:
            try:
                self._playback_movie.frameChanged.disconnect(self._on_playback_movie_frame_changed)
            except Exception:
                pass
            self._playback_movie.stop()
            self._playback_movie = None
        self._playback_movie_initialized = False
        self._video_pixmap_item.setVisible(False)
        self._video_item.setVisible(True)
        if self._crop_item:
            self._crop_item.setVisible(True)

    def _play_output_webp(self, out_path: Path) -> None:
        self._stop_playback_movie()
        movie = QMovie(str(out_path))
        if not movie.isValid():
            QMessageBox.warning(self, "无法预览", "无法播放该 WebP（Qt 缺少 WebP 动图支持）。")
            return

        movie.setCacheMode(QMovie.CacheAll)
        movie.frameChanged.connect(self._on_playback_movie_frame_changed)
        self._playback_movie = movie
        self._playback_movie_initialized = False
        try:
            self._player.stop()
        except Exception:
            pass
        self._video_item.setVisible(False)
        self._video_pixmap_item.setVisible(True)
        if self._crop_item:
            self._crop_item.setVisible(False)
        movie.start()
        self._on_playback_movie_frame_changed(movie.currentFrameNumber())

    def _on_playback_movie_frame_changed(self, _frame_number: int) -> None:
        movie = self._playback_movie
        if not movie:
            return
        pixmap = movie.currentPixmap()
        if pixmap.isNull():
            return
        self._video_pixmap_item.setPixmap(pixmap)
        if not self._playback_movie_initialized:
            self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
            self._video_view.fit_scene()
            self._playback_movie_initialized = True

    def _current_crop(self) -> CropSpec | None:
        if not self._crop_item or not self._video_size:
            return None

        width, height = self._video_size
        side = int(round(self._crop_item.rect().width()))
        pos = self._crop_item.pos()
        x = int(round(pos.x()))
        y = int(round(pos.y()))

        x = max(0, min(x, width - side))
        y = max(0, min(y, height - side))
        side = max(1, min(side, width, height))
        return CropSpec(x=x, y=y, side=side)

    def _build_vf(self, crop: CropSpec, out_side: int, fps: int) -> str:
        filters: list[str] = [f"crop={crop.side}:{crop.side}:{crop.x}:{crop.y}"]
        if out_side != crop.side:
            filters.append(f"scale={out_side}:{out_side}:flags=lanczos")
        if fps > 0:
            filters.append(f"fps={fps}")
        filters.append("format=rgba")
        filters.append(
            "geq="
            "r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            "a='if(lte((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2),(W/2)*(W/2)),255,0)'"
        )
        return ",".join(filters)

    def _new_temp_webp_path(self, final_path: Path) -> Path:
        token = uuid.uuid4().hex
        name = f".{final_path.stem}.tmp_{token}{final_path.suffix}"
        return final_path.with_name(name)

    def _start_ffmpeg_encode(self, out_path: Path, vf: str, quality: int) -> None:
        args = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            self._video_path or "",
            "-vf",
            vf,
            "-an",
            "-vsync",
            "0",
            "-c:v",
            "libwebp",
            "-lossless",
            "0",
            "-q:v",
            str(quality),
            "-preset",
            "icon",
            "-compression_level",
            "6",
            "-loop",
            "0",
            "-pix_fmt",
            "yuva420p",
            str(out_path),
        ]

        self._pending_out_path = out_path
        self._current_quality = quality
        self._ffmpeg_output = []
        self._ffmpeg_finalized = False

        self._ffmpeg_process = QProcess(self)
        self._ffmpeg_process.setProgram(self._ffmpeg_path or "ffmpeg")
        self._ffmpeg_process.setArguments(args)
        self._ffmpeg_process.setProcessChannelMode(QProcess.MergedChannels)
        self._ffmpeg_process.readyReadStandardOutput.connect(self._on_ffmpeg_output)
        self._ffmpeg_process.errorOccurred.connect(self._on_ffmpeg_error)
        self._ffmpeg_process.finished.connect(self._on_ffmpeg_finished)
        self._ffmpeg_process.start()

    def _make_badge(self) -> None:
        if not self._video_path:
            QMessageBox.information(self, "提示", "请先添加一个视频")
            return
        if not self._output_dir:
            QMessageBox.information(self, "提示", "请选择保存目录")
            return
        if not self._ffmpeg_path:
            QMessageBox.warning(
                self,
                "缺少 ffmpeg",
                "未找到 ffmpeg。请将 ffmpeg 放到程序目录下的 ffmpeg_bin 文件夹中，或安装 ffmpeg 并确保在 PATH 中。",
            )
            return

        crop = self._current_crop()
        if not crop:
            QMessageBox.information(self, "提示", "裁剪框未就绪")
            return

        output_name = self._output_name_edit.text().strip()
        if not output_name:
            QMessageBox.information(self, "提示", "请输入输出文件名")
            return

        if not output_name.lower().endswith(".webp"):
            output_name = f"{output_name}.webp"
            self._output_name_edit.setText(output_name)
        out_path = Path(self._output_dir) / output_name
        if out_path.exists():
            choice = QMessageBox.question(
                self,
                "文件已存在",
                f"文件已存在：\n{out_path}\n是否覆盖？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return

        if self._ffmpeg_process and self._ffmpeg_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "正在制作中，请稍候…")
            return

        if self._playback_movie:
            self._stop_playback_movie()
            if self._video_size:
                width, height = self._video_size
                self._scene.setSceneRect(QRectF(0, 0, width, height))
            self._player.setSource(QUrl.fromLocalFile(self._video_path))
            self._player.play()
            self._video_view.fit_scene()

        out_side = int(self._size_combo.currentData() or crop.side)
        fps = int(self._fps_combo.currentData() or 30)
        quality = int(self._quality_slider.value())

        vf = self._build_vf(crop=crop, out_side=out_side, fps=fps)

        self._final_out_path = out_path
        self._size_search = None
        self._job_was_canceled = False

        self._btn_make.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._btn_add_video.setEnabled(False)
        self._btn_output_dir.setEnabled(False)
        self._output_name_edit.setEnabled(False)
        self._quality_slider.setEnabled(False)
        self._size_combo.setEnabled(False)
        self._fps_combo.setEnabled(False)
        self._limit_size_check.setEnabled(False)
        self._limit_size_mb_spin.setEnabled(False)
        self._progress.setRange(0, 0)
        self._status.setText("正在制作，请稍候…")

        if self._limit_size_check.isChecked():
            target_mb = int(self._limit_size_mb_spin.value())
            target_bytes = target_mb * 1024 * 1024

            quality_min = 1
            quality_max = max(quality_min, quality)

            self._size_search = SizeSearchState(
                target_bytes=target_bytes,
                quality_min=quality_min,
                quality_max=quality_max,
                crop=crop,
                out_side=out_side,
                fps=fps,
                final_path=out_path,
                phase="test_max",
                low=quality_min,
                high=quality_max,
            )
            temp_path = self._new_temp_webp_path(out_path)
            self._size_search.temp_paths.append(temp_path)
            self._status.setText(f"限制大小：目标 {target_mb} MB，尝试质量 {quality_max}…")
            self._start_ffmpeg_encode(out_path=temp_path, vf=vf, quality=quality_max)
            return

        self._status.setText(f"正在制作（质量 {quality}，{out_side}px，{fps}fps）…")
        self._start_ffmpeg_encode(out_path=out_path, vf=vf, quality=quality)

    def _cancel_make_badge(self) -> None:
        if not self._ffmpeg_process or self._ffmpeg_process.state() == QProcess.NotRunning:
            return

        self._job_was_canceled = True
        pending_out_path = self._pending_out_path
        final_out_path = self._final_out_path

        self._ffmpeg_finalized = True
        try:
            self._ffmpeg_process.kill()
        except Exception:
            try:
                self._ffmpeg_process.terminate()
            except Exception:
                pass

        self._cleanup_size_search_temp_files()
        self._size_search = None
        self._dispose_ffmpeg_process()

        for path in (pending_out_path, final_out_path):
            if not path:
                continue
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

        self._complete_job(ok=False, message="已取消")

    def _on_ffmpeg_output(self) -> None:
        sender = self.sender()
        if sender is not None and sender != self._ffmpeg_process:
            return
        if not self._ffmpeg_process:
            return
        chunk = bytes(self._ffmpeg_process.readAllStandardOutput()).decode(errors="replace")
        if chunk:
            self._ffmpeg_output.append(chunk)

    def _on_ffmpeg_error(self, error: QProcess.ProcessError) -> None:
        sender = self.sender()
        if sender is not None and sender != self._ffmpeg_process:
            return
        if not self._ffmpeg_process:
            return
        if self._ffmpeg_finalized:
            return
        self._on_ffmpeg_output()
        self._ffmpeg_finalized = True

        reason = {
            QProcess.FailedToStart: "ffmpeg 启动失败",
            QProcess.Crashed: "ffmpeg 异常退出",
            QProcess.Timedout: "ffmpeg 运行超时",
            QProcess.ReadError: "ffmpeg 读取输出失败",
            QProcess.WriteError: "ffmpeg 写入失败",
            QProcess.UnknownError: "ffmpeg 未知错误",
        }.get(error, "ffmpeg 出错")

        details = "".join(self._ffmpeg_output).strip()
        self._abort_job(message=details or reason)

    def _on_ffmpeg_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        sender = self.sender()
        if sender is not None and sender != self._ffmpeg_process:
            return
        if self._ffmpeg_finalized:
            return
        self._on_ffmpeg_output()
        self._ffmpeg_finalized = True
        ok = exit_status == QProcess.NormalExit and exit_code == 0
        details = "".join(self._ffmpeg_output).strip()
        if self._size_search is not None:
            self._handle_size_search_attempt(ok=ok, details=details)
            return

        if ok:
            self._finish_success_single()
            return
        self._abort_job(message=details or "ffmpeg 运行失败")

    def _dispose_ffmpeg_process(self) -> None:
        if self._ffmpeg_process:
            self._ffmpeg_process.deleteLater()
            self._ffmpeg_process = None
        self._pending_out_path = None
        self._current_quality = None
        self._ffmpeg_output = []

    def _abort_job(self, message: str) -> None:
        self._cleanup_size_search_temp_files()
        self._size_search = None
        self._complete_job(ok=False, message=message)

    def _finish_success_single(self) -> None:
        out_path = self._final_out_path or self._pending_out_path
        if not out_path:
            self._abort_job(message="输出路径未知")
            return
        try:
            size_mb = out_path.stat().st_size / (1024 * 1024)
        except Exception:
            size_mb = -1

        suffix = f"（{size_mb:.2f} MB）" if size_mb >= 0 else ""
        self._play_output_webp(out_path)
        self._complete_job(ok=True, message=f"已输出：\n{out_path}\n{suffix}")

    def _cleanup_size_search_temp_files(self) -> None:
        state = self._size_search
        if not state:
            return
        for path in list(state.temp_paths):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        state.temp_paths.clear()

    def _finalize_size_search(self, ok: bool, message: str) -> None:
        self._dispose_ffmpeg_process()
        self._complete_job(ok=ok, message=message)
        self._size_search = None

    def _handle_size_search_attempt(self, ok: bool, details: str) -> None:
        state = self._size_search
        temp_path = self._pending_out_path
        quality = self._current_quality

        self._dispose_ffmpeg_process()

        if not state or not temp_path or quality is None:
            self._finalize_size_search(ok=False, message=details or "内部状态错误")
            return

        if not ok:
            self._cleanup_size_search_temp_files()
            self._finalize_size_search(ok=False, message=details or "ffmpeg 运行失败")
            return

        try:
            size_bytes = int(temp_path.stat().st_size)
        except Exception:
            self._cleanup_size_search_temp_files()
            self._finalize_size_search(ok=False, message="无法读取输出文件大小")
            return

        target_mb = max(1, int(round(state.target_bytes / (1024 * 1024))))
        size_mb = size_bytes / (1024 * 1024)

        if state.phase == "test_max":
            if size_bytes <= state.target_bytes:
                state.best_quality = quality
                state.best_size_bytes = size_bytes
                state.best_temp_path = temp_path
                self._apply_best_size_search_result(note="已满足目标大小")
                return

            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

            state.phase = "test_min"
            q = state.quality_min
            new_temp = self._new_temp_webp_path(state.final_path)
            state.temp_paths.append(new_temp)
            self._status.setText(f"限制大小：目标 {target_mb} MB，尝试最低质量 {q}…")
            vf = self._build_vf(state.crop, state.out_side, state.fps)
            self._start_ffmpeg_encode(out_path=new_temp, vf=vf, quality=q)
            return

        if state.phase == "test_min":
            state.best_quality = quality
            state.best_size_bytes = size_bytes
            state.best_temp_path = temp_path

            if size_bytes > state.target_bytes:
                note = f"无法达到目标大小 {target_mb} MB，已使用最低质量 {quality} 输出（{size_mb:.2f} MB）。建议降低尺寸/帧率。"
                self._apply_best_size_search_result(note=note)
                return

            state.phase = "binary"
            state.low = state.quality_min + 1
            state.high = state.quality_max - 1
            if state.low > state.high:
                note = f"已满足目标大小 {target_mb} MB（{size_mb:.2f} MB）"
                self._apply_best_size_search_result(note=note)
                return

            self._start_next_binary_search_attempt()
            return

        if state.phase == "binary":
            if size_bytes <= state.target_bytes and (state.best_quality is None or quality > state.best_quality):
                if state.best_temp_path and state.best_temp_path != temp_path:
                    try:
                        state.best_temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                state.best_quality = quality
                state.best_size_bytes = size_bytes
                state.best_temp_path = temp_path
                state.low = quality + 1
            else:
                state.high = quality - 1
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

            if state.low > state.high:
                best_mb = (state.best_size_bytes or 0) / (1024 * 1024)
                self._apply_best_size_search_result(note=f"目标 {target_mb} MB，最佳结果 {best_mb:.2f} MB")
                return

            if state.attempt >= state.max_attempts:
                self._apply_best_size_search_result(note="已达到最大尝试次数，输出最佳结果")
                return

            self._start_next_binary_search_attempt()
            return

        self._cleanup_size_search_temp_files()
        self._finalize_size_search(ok=False, message="未知的限制大小阶段")

    def _start_next_binary_search_attempt(self) -> None:
        state = self._size_search
        if not state:
            return

        state.attempt += 1
        q = (state.low + state.high) // 2
        target_mb = max(1, int(round(state.target_bytes / (1024 * 1024))))
        new_temp = self._new_temp_webp_path(state.final_path)
        state.temp_paths.append(new_temp)
        self._status.setText(f"限制大小：目标 {target_mb} MB，第 {state.attempt} 次尝试，质量 {q}…")
        vf = self._build_vf(state.crop, state.out_side, state.fps)
        self._start_ffmpeg_encode(out_path=new_temp, vf=vf, quality=q)

    def _apply_best_size_search_result(self, note: str) -> None:
        state = self._size_search
        if not state or not state.best_temp_path or not state.best_quality:
            self._cleanup_size_search_temp_files()
            self._finalize_size_search(ok=False, message="未获得可用的输出结果")
            return

        final_path = state.final_path
        best_path = state.best_temp_path
        best_mb = (state.best_size_bytes or 0) / (1024 * 1024)

        try:
            best_path.replace(final_path)
        except Exception as exc:
            self._cleanup_size_search_temp_files()
            self._finalize_size_search(ok=False, message=f"保存最终文件失败：{exc}")
            return

        self._play_output_webp(final_path)

        for path in list(state.temp_paths):
            if path == best_path:
                continue
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

        self._cleanup_size_search_temp_files()
        self._finalize_size_search(
            ok=True,
            message=f"已输出：\n{final_path}\n质量 {state.best_quality}，{best_mb:.2f} MB\n{note}",
        )

    def _complete_job(self, ok: bool, message: str) -> None:
        self._progress.setRange(0, 1)
        self._progress.setValue(1 if ok else 0)
        self._btn_make.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_add_video.setEnabled(True)
        self._btn_output_dir.setEnabled(True)
        self._output_name_edit.setEnabled(True)
        self._quality_slider.setEnabled(True)
        self._size_combo.setEnabled(True)
        self._fps_combo.setEnabled(True)
        self._limit_size_check.setEnabled(True)
        self._limit_size_mb_spin.setEnabled(self._limit_size_check.isChecked())

        if self._ffmpeg_process:
            self._ffmpeg_process.deleteLater()
            self._ffmpeg_process = None

        if ok:
            out_path = self._final_out_path or self._pending_out_path
            self._status.setText(f"已输出：{out_path}")
            QMessageBox.information(self, "完成", message)
        else:
            if self._job_was_canceled:
                self._status.setText("已取消")
                QMessageBox.information(self, "已取消", message)
            else:
                self._status.setText("制作失败")
                QMessageBox.critical(self, "制作失败", message)

        self._pending_out_path = None
        self._final_out_path = None
        self._current_quality = None
        self._ffmpeg_output = []
        self._ffmpeg_finalized = False
        self._job_was_canceled = False


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(900, 680)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
