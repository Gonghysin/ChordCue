"""Native Windows chart editor, independent transport and explicit opt-in outputs."""
from dataclasses import replace
from functools import partial
from pathlib import Path
import time

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QKeySequence, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
    QSpinBox, QSplitter, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .models import ChartDocument, KeySection, LoopRange, MusicalKey, document_with_score
from .parsing import format_manual, parse_manual
from .project import demo_document, load_project, save_project
from .render import ChartWidget, export_pdf, layout_chart
from .settings import Settings
from .score_ui import DevicePanel, ScorePreview, available_views
from .theory import analyze_sections, note_name, parse_key
from .transport import StandaloneTransport
from .timing import measure_quarters


STYLE = """
QMainWindow, QWidget { background: #faf9f5; color: #25364a; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QToolBar { background: #fffefa; border: 0; border-bottom: 1px solid #dce2e7; spacing: 8px; padding: 9px; }
QToolButton, QPushButton { background: #ffffff; border: 1px solid #cfd9e3; border-radius: 6px; padding: 6px 11px; }
QToolButton:hover, QPushButton:hover { background: #edf5ff; border-color: #80b0e0; }
QPushButton#primary { background: #327ac2; color: white; border-color: #327ac2; font-weight: 600; }
QPushButton:disabled { color: #9aa4af; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit { background: white; border: 1px solid #d4dce4; border-radius: 5px; padding: 5px; selection-background-color: #cce2fa; }
QGroupBox { border: 1px solid #dbe1e7; border-radius: 7px; margin-top: 13px; padding: 12px 8px 6px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTabWidget::pane { border: 0; }
QTabBar::tab { padding: 10px 24px; color: #718092; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: #2875bf; border-bottom: 3px solid #438bda; font-weight: 600; }
QLabel#muted { color: #768496; font-size: 12px; }
QLabel#error { color: #a63b32; font-size: 12px; }
QSlider::groove:horizontal { height: 5px; background: #dce5ee; border-radius: 2px; }
QSlider::handle:horizontal { width: 13px; margin: -4px 0; background: #438bda; border-radius: 6px; }
QStatusBar { border-top: 1px solid #dce2e7; color: #738398; }
"""


def parse_sections(text: str, bars: int) -> tuple[KeySection, ...]:
    result = []
    seen = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            position, key_text = raw.split(maxsplit=1)
            bar = int(position)
            key = parse_key(key_text.strip())
            if key is None or not 1 <= bar <= bars or bar in seen:
                raise ValueError("小节应在曲目内且不重复，调性例如 E♭ 或 Am")
            result.append(KeySection(bar, key))
            seen.add(bar)
        except ValueError as error:
            raise ValueError(f"转调第 {line_number} 行：{error}") from error
    return tuple(sorted(result, key=lambda section: section.first_bar))


class MainWindow(QMainWindow):
    def __init__(self, *, settings=None, audio_factory=None, lan_factory=None, session_factory=None,
                 chart_factory=None, score_factory=None):
        super().__init__()
        if audio_factory is None:
            from .audio import AudioPanel
            audio_factory = AudioPanel
        if lan_factory is None:
            from .lan import LANServer
            lan_factory = LANServer
        if chart_factory is None:
            from .lan import chart_payload
            chart_factory = chart_payload
        if session_factory is None:
            from .platform_events import SessionGuard
            session_factory = SessionGuard
        self.settings = settings or Settings()
        self.transport = StandaloneTransport(demo_document())
        self._saved_document = self.document
        self.project_path = None
        self._editor_dirty = False
        self._syncing = False
        self._closed = False
        self._revision = 0
        self._lan_enabled = False
        self._lan = lan_factory()
        self._make_chart_payload = chart_factory
        self._sections: tuple[KeySection, ...] = ()
        self._chart_payload = None
        self._previous_bar = None
        self._score_factory = score_factory
        self.score_panel = None
        self.score_page = None
        self._previous_chart_row = None
        self._device_updated = 0.0
        self._session_factory = session_factory
        self.setStyleSheet(STYLE)
        self.setMinimumSize(960, 680)
        self.resize(1240, 830)
        self.setAcceptDrops(True)
        self._build_toolbar()
        center = QWidget()
        body = QVBoxLayout(center)
        body.setContentsMargins(20, 12, 20, 12)
        body.setSpacing(10)
        self._build_project_controls(body)
        self._build_transport_controls(body)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(self.splitter, 1)
        self.tabs = QTabWidget()
        self.chart_page = QWidget()
        chart_layout = QVBoxLayout(self.chart_page)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        display_row = QHBoxLayout()
        self.notation = QComboBox()
        self.notation.addItem("和弦谱", "chords")
        self.notation.addItem("级数谱", "numbers")
        self.display_key = QComboBox()
        for label, value in (("轨道原样", "track"), ("C 调", "c"), ("移调前原调", "original")):
            self.display_key.addItem(label, value)
        for root in range(12):
            self.display_key.addItem(note_name(root)+" 调", f"key:{root}")
        display_row.addWidget(self.notation)
        display_row.addWidget(QLabel("显示调"))
        display_row.addWidget(self.display_key)
        self.score_part = QComboBox()
        self.score_part.setAccessibleName("源谱声部")
        self.score_part.hide()
        self.score_kind = QComboBox()
        self.score_kind.addItem("五线谱", "staff")
        self.score_kind.addItem("源 TAB", "tab")
        self.score_kind.hide()
        self.score_follow = QCheckBox("跟随播放")
        self.score_follow.setChecked(True)
        display_row.addStretch()
        self.key_summary = QLabel()
        self.key_summary.setObjectName("muted")
        display_row.addWidget(self.key_summary)
        chart_layout.addLayout(display_row)
        self.chart = ChartWidget()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.chart)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        chart_layout.addWidget(self.scroll_area)
        self.tabs.addTab(self.chart_page, "和弦谱")
        self.audio = audio_factory(self)
        self.tabs.addTab(self.audio, "节拍器")
        self.splitter.addWidget(self.tabs)
        self.editor_panel = self._build_editor()
        self.splitter.addWidget(self.editor_panel)
        self.splitter.setSizes([850, 310])
        self.editor_panel.hide()
        self._build_lan_panel(body)
        self.setCentralWidget(center)
        self.chart.seek_requested.connect(self._seek)
        self.notation.currentIndexChanged.connect(self._display_changed)
        self.display_key.currentIndexChanged.connect(self._display_changed)
        self.score_part.currentIndexChanged.connect(self._part_changed)
        self.score_kind.currentIndexChanged.connect(self._display_changed)
        self.score_follow.toggled.connect(self._score_follow_changed)
        self.tabs.currentChanged.connect(self._score_visibility_changed)
        self.audio.enabled_changed.connect(self._audio_state)
        self._sync_widgets()
        self._refresh_chart()
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.session_guard = session_factory(self, self.suspend)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._set_combo(self.notation, self.settings.value("notation", "chords"))
        self._set_combo(self.display_key, self.settings.value("displayKey", "track"))
        self.top_action.setChecked(str(self.settings.value("alwaysOnTop", False)).lower() in ("true", "1"))
        self._tick()
        self.statusBar().showMessage("独立播放 · 点击谱面定位 · 本机声音与局域网投放默认关闭")

    @property
    def document(self):
        return self.transport.document

    @property
    def dirty(self):
        return self.document != self._saved_document or self._editor_dirty

    def _build_toolbar(self):
        toolbar = QToolBar("文件与视图")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label, shortcut, callback in (
            ("新建", QKeySequence.StandardKey.New, self.new_project),
            ("打开…", QKeySequence.StandardKey.Open, self.open_project),
            ("保存", QKeySequence.StandardKey.Save, self.save_project),
            ("另存为…", QKeySequence.StandardKey.SaveAs, lambda: self.save_project(save_as=True)),
        ):
            action = toolbar.addAction(label)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, callback=callback: callback())
        self.import_action = toolbar.addAction("导入乐谱…")
        self.import_action.triggered.connect(self.import_score)
        export = QToolButton()
        export.setText("导出 PDF ▾")
        export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(export)
        for notation, name in (("chords", "和弦谱"), ("numbers", "级数谱")):
            for mode, label in (("track", "轨道原样"), ("c", "C 调"), ("original", "移调前原调"), ("current", "当前显示调")):
                menu.addAction(f"{name} · {label}", partial(self._export_dialog, notation, mode))
            menu.addSeparator()
        export.setMenu(menu)
        toolbar.addWidget(export)
        spacer = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.editor_action = toolbar.addAction("编辑 / 调性")
        self.editor_action.setCheckable(True)
        self.editor_action.toggled.connect(lambda shown: self.editor_panel.setVisible(shown))
        self.lan_action = toolbar.addAction("局域网投放")
        self.lan_action.setCheckable(True)
        self.lan_action.toggled.connect(self._toggle_lan)
        self.top_action = toolbar.addAction("置顶")
        self.top_action.setCheckable(True)
        self.top_action.toggled.connect(self._toggle_top)

    def _build_project_controls(self, layout):
        row = QHBoxLayout()
        title = QLabel("ChordCue")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #2c639e;")
        row.addWidget(title)
        self.name_edit = QLineEdit()
        self.name_edit.setAccessibleName("曲目名称")
        self.name_edit.setMinimumWidth(160)
        row.addWidget(self.name_edit, 1)
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(20, 300)
        self.bpm.setDecimals(1)
        self.bpm.setSuffix(" BPM")
        self.meter = QComboBox()
        for beats in range(1, 13):
            self.meter.addItem(f"{beats}/4", beats)
        self.bars = QSpinBox()
        self.bars.setRange(1, 100000)
        self.bars.setSuffix(" 小节")
        row.addWidget(self.bpm)
        row.addWidget(self.meter)
        row.addWidget(self.bars)
        self.timing_status = QLabel()
        self.timing_status.setAccessibleName("当前 BPM 与拍号")
        self.timing_status.setToolTip("按乐谱或工程中已有的速度与拍号标记自动切换；BPM 以四分音符计")
        row.addWidget(self.timing_status)
        layout.addLayout(row)
        self.name_edit.editingFinished.connect(self._project_changed)
        self.bpm.valueChanged.connect(self._bpm_changed)
        self.meter.currentIndexChanged.connect(self._meter_changed)
        self.bars.editingFinished.connect(self._project_changed)

    def _build_transport_controls(self, layout):
        row = QHBoxLayout()
        self.play_button = QPushButton("播放")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self._play_pause)
        stop = QPushButton("停止")
        stop.clicked.connect(self.transport.stop)
        row.addWidget(self.play_button)
        row.addWidget(stop)
        self.position = QLabel("001 · 1")
        self.position.setMinimumWidth(88)
        self.position.setStyleSheet("font-size: 19px; font-weight: 600; color: #3475b9;")
        row.addWidget(self.position)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setAccessibleName("播放位置")
        self.seek_slider.sliderReleased.connect(self._slider_seek)
        row.addWidget(self.seek_slider, 1)
        self.loop_check = QCheckBox("整小节循环")
        self.loop_start, self.loop_end = QSpinBox(), QSpinBox()
        for spin in (self.loop_start, self.loop_end):
            spin.setRange(1, 100000)
            spin.setFixedWidth(68)
            spin.editingFinished.connect(self._loop_changed)
        self.loop_check.toggled.connect(self._loop_changed)
        row.addWidget(self.loop_check)
        row.addWidget(self.loop_start)
        row.addWidget(QLabel("至"))
        row.addWidget(self.loop_end)
        self.audio_check = QCheckBox("本机声音")
        self.audio_check.toggled.connect(lambda enabled: self.audio.set_enabled(enabled))
        row.addWidget(self.audio_check)
        layout.addLayout(row)

    def _key_combo(self, optional_label):
        combo = QComboBox()
        combo.addItem(optional_label, None)
        for root in range(12):
            for minor in (False, True):
                key = MusicalKey(root, minor)
                combo.addItem(key.selection_label, key)
        return combo

    def _build_editor(self):
        panel = QWidget()
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 0, 0, 0)
        keys = QGroupBox("调性设置")
        form = QFormLayout(keys)
        self.forced_key = self._key_combo("自动定调")
        self.original_key = self._key_combo("未指定")
        self.detect_changes = QCheckBox("自动检测转调")
        form.addRow("轨道定调", self.forced_key)
        form.addRow("移调前原调", self.original_key)
        form.addRow(self.detect_changes)
        self.sections_edit = QPlainTextEdit()
        self.sections_edit.setPlaceholderText("手动转调，每行“小节 调”\n例如：33 E♭\n49 Am")
        self.sections_edit.setMaximumHeight(90)
        self.sections_edit.setAccessibleName("手动转调点")
        form.addRow(self.sections_edit)
        layout.addWidget(keys)
        label = QLabel("和弦输入")
        label.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(label)
        self.chord_input_hint = QLabel("每行：小节[.拍[.分拍.tick]] 和弦\n例如：1 Cmaj7　2.3 G/B　3.1.2.120 Am")
        self.chord_input_hint.setWordWrap(True)
        self.chord_input_hint.setObjectName("muted")
        layout.addWidget(self.chord_input_hint)
        self.chords_edit = QPlainTextEdit()
        self.chords_edit.setAccessibleName("和弦输入编辑器")
        layout.addWidget(self.chords_edit, 1)
        self.input_error = QLabel()
        self.input_error.setObjectName("error")
        self.input_error.setWordWrap(True)
        layout.addWidget(self.input_error)
        buttons = QHBoxLayout()
        apply_button = QPushButton("应用编辑")
        apply_button.setObjectName("primary")
        apply_button.clicked.connect(self.apply_editor)
        revert_button = QPushButton("还原输入")
        revert_button.clicked.connect(self._sync_editor)
        buttons.addWidget(apply_button)
        buttons.addWidget(revert_button)
        layout.addLayout(buttons)
        self.import_text_button = QPushButton("导入和弦文本…")
        self.import_text_button.clicked.connect(self.import_text)
        layout.addWidget(self.import_text_button)
        for widget in (self.chords_edit, self.sections_edit):
            widget.textChanged.connect(self._mark_editor_dirty)
        for widget in (self.forced_key, self.original_key):
            widget.currentIndexChanged.connect(self._mark_editor_dirty)
        self.detect_changes.toggled.connect(self._mark_editor_dirty)
        return panel

    def _build_lan_panel(self, layout):
        self.lan_panel = QWidget()
        panel_layout = QVBoxLayout(self.lan_panel)
        row = QHBoxLayout()
        panel_layout.addLayout(row)
        row.setContentsMargins(8, 0, 8, 0)
        self.lan_links = QLabel()
        self.lan_links.setTextFormat(Qt.TextFormat.PlainText)
        self.lan_links.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.lan_links, 1)
        copy = QPushButton("复制链接")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.lan_links.text()))
        row.addWidget(copy)
        hint = QLabel("同一局域网打开；停止投放后链接失效。")
        hint.setObjectName("muted")
        row.addWidget(hint)
        self.devices = DevicePanel(self._assign_device, self._show_error, self.lan_panel)
        panel_layout.addWidget(self.devices)
        self.lan_panel.hide()
        layout.addWidget(self.lan_panel)

    def _set_combo(self, combo, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _sync_editor(self):
        self._syncing = True
        doc = self.document
        self.chords_edit.setPlainText(format_manual(doc.events))
        self.sections_edit.setPlainText("\n".join(
            f"{s.first_bar} {note_name(s.key.root)}{'m' if s.key.is_minor else ''}" for s in doc.manual_sections))
        self._set_combo(self.forced_key, doc.forced_key)
        self._set_combo(self.original_key, doc.original_key)
        self.detect_changes.setChecked(doc.detect_changes)
        self.input_error.clear()
        self._editor_dirty = False
        self._syncing = False
        self._title()

    def _sync_widgets(self, *, editor=True):
        self._syncing = True
        doc = self.document
        self.name_edit.setText(doc.name)
        self.bpm.setValue(doc.bpm)
        self._set_combo(self.meter, doc.meter)
        self.bars.setValue(doc.bars)
        imported = doc.score is not None
        self.bpm.setVisible(not imported and not doc.timing_changes)
        self.meter.setVisible(not imported and not doc.timing_changes)
        self.bars.setEnabled(not imported)
        self.meter.setEnabled(not imported and not doc.timing_changes)
        self.bpm.setEnabled(not imported and not doc.timing_changes)
        self.bpm.setToolTip("手工和弦谱的整首固定速度，BPM 以四分音符计")
        self.meter.setToolTip("手工和弦谱的整首固定拍号")
        self.chords_edit.setReadOnly(imported)
        self.sections_edit.setEnabled(not imported)
        self.detect_changes.setEnabled(not imported)
        self.import_text_button.setEnabled(not imported)
        self.forced_key.setItemText(0, "使用源调号" if imported else "自动定调")
        self.chord_input_hint.setText(
            "和弦与转调点来自源谱。轨道定调、移调前原调和显示调可调整呈现；修改源标记后请重新导入。"
            if imported else "每行：小节[.拍[.分拍.tick]] 和弦\n例如：1 Cmaj7　2.3 G/B　3.1.2.120 Am" +
            ("\n位置始终以四分音符计；6/8 的 2.3 表示第 2 小节第 3 个四分音符。"
             if doc.timing_changes else ""))
        self.score_part.setVisible(imported)
        self.score_kind.setVisible(imported)
        block = QSignalBlocker(self.score_part)
        self.score_part.clear()
        if imported:
            for part in doc.score.parts:
                self.score_part.addItem(part.name, part.id)
            self._set_combo(self.score_part, doc.selected_part_id or doc.score.parts[0].id)
            self._ensure_score_panel()
        if self.score_panel and self.score_page is not None:
            self.tabs.setTabVisible(self.tabs.indexOf(self.score_page), imported)
        del block
        self.seek_slider.setRange(0, min(2147483647, doc.bars*doc.meter*960))
        for spin in (self.loop_start, self.loop_end):
            spin.setMaximum(doc.bars)
        self.loop_start.setValue(doc.loop.start_bar if doc.loop else 1)
        self.loop_end.setValue(doc.loop.end_bar_exclusive-1 if doc.loop else doc.bars)
        self.loop_check.setChecked(doc.loop is not None)
        self._syncing = False
        if editor:
            self._sync_editor()
        self._title()

    def _title(self):
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{self.document.name} — ChordCue")

    def _refresh_chart(self):
        self._revision += 1
        self._sections = analyze_sections(self.document)
        self._chart_payload = self._make_chart_payload(self.document, self._sections, self._revision)
        if self.transport.plan is not None:
            self._chart_payload["route"] = self.transport.plan.to_dict()
        self.key_summary.setText(" · ".join(s.key.label for s in self._sections[:3]))
        if self.document.score and not self.document.score.key_changes and self.document.forced_key is None:
            self.key_summary.setText("源谱调性未知")
        self._display_changed()
        self._title()

    def _display_changed(self, *_args):
        try:
            self.chart.set_chart(layout_chart(self.document, self.notation.currentData(), self.display_key.currentData(),
                                              sections=self._sections), self.document.meter)
            if self.document.score and self.score_panel:
                from .render import display_shift
                part = next(p for p in self.document.score.parts
                            if p.id == self.score_part.currentData())
                tab_available = "tab" in available_views(part)
                kind_model = self.score_kind.model()
                if isinstance(kind_model, QStandardItemModel):
                    tab_item = kind_model.item(1)
                    if tab_item is not None:
                        tab_item.setEnabled(tab_available)
                self.score_kind.setToolTip("" if tab_available else "源声部缺少调弦或弦品资料")
                if not tab_available and self.score_kind.currentData() == "tab":
                    blocker = QSignalBlocker(self.score_kind)
                    self.score_kind.setCurrentIndex(0)
                    del blocker
                shift = display_shift(self.document, self._sections, self.display_key.currentData())
                if self.score_kind.currentData() == "tab":
                    shift = 0
                    self.score_kind.setToolTip("源 TAB 保留原调指法；五线谱与和弦谱可移调")
                self.score_panel.show_score(self.document.score, self.score_part.currentData(),
                                            self.score_kind.currentData(), shift)
        except ValueError as error:
            self.chart.set_chart((), self.document.meter)
            if self.score_panel:
                self.score_panel.clear_score(str(error))
            self._show_error(error)

    def _ensure_score_panel(self):
        if self.score_panel is None:
            factory = self._score_factory
            if factory is None:
                from .score_panel import ScorePanel
                factory = ScorePanel
            self.score_panel = factory(self)
            self.score_page = QWidget()
            layout = QVBoxLayout(self.score_page)
            layout.setContentsMargins(0, 0, 0, 0)
            toolbar = QHBoxLayout()
            toolbar.addWidget(QLabel("声部"))
            toolbar.addWidget(self.score_part)
            toolbar.addWidget(self.score_kind)
            toolbar.addWidget(self.score_follow)
            toolbar.addStretch()
            layout.addLayout(toolbar)
            layout.addWidget(self.score_panel, 1)
            if hasattr(self.score_panel, "seek_requested"):
                self.score_panel.seek_requested.connect(self._score_seek)
            self.tabs.addTab(self.score_page, "乐谱")
            self._score_follow_changed(self.score_follow.isChecked())
            self._score_visibility_changed()
        return self.score_panel

    def _score_follow_changed(self, enabled):
        if self.score_panel and hasattr(self.score_panel, "set_follow"):
            self.score_panel.set_follow(enabled)

    def _score_visibility_changed(self, *_args):
        if self.score_panel and hasattr(self.score_panel, "set_active"):
            self.score_panel.set_active(self.tabs.currentWidget() is self.score_page)

    def _score_seek(self, measure_id, offset):
        if self.document.score:
            for index, measure in enumerate(self.document.score.measures, 1):
                if measure.id == measure_id:
                    self._seek(index, offset+1)
                    return

    def _part_changed(self, *_args):
        if self._syncing or not self.document.score:
            return
        part_id = self.score_part.currentData()
        if part_id and part_id != self.document.selected_part_id:
            # Selection changes the saved presentation.
            self.transport.select_part(part_id)
            self._refresh_chart()

    def import_score(self, *_args):
        path, _ = QFileDialog.getOpenFileName(self, "导入 Guitar Pro / MusicXML", "",
                    "乐谱 (*.gp *.gpx *.gp5 *.gp4 *.gp3 *.musicxml *.xml *.mxl);;所有文件 (*)")
        if path:
            self.import_score_path(path)

    def import_score_path(self, path):
        self.import_action.setEnabled(False)
        self.statusBar().showMessage("正在读取源谱…")
        def failed(message):
            self.import_action.setEnabled(True)
            self._show_error(f"导入失败：{message}")
        def preview(score):
            self.import_action.setEnabled(True)
            try:
                dialog = ScorePreview(score, self)
            except ValueError as error:
                failed(error)
                return
            if dialog.exec() != ScorePreview.DialogCode.Accepted:
                return
            if not self._confirm_discard():
                return
            self.suspend()
            self.project_path = None
            self._replace_document(document_with_score(score, dialog.part.currentData()))
            self._ensure_score_panel()
            assert self.score_page is not None
            self.tabs.setCurrentWidget(self.score_page)
            self.statusBar().showMessage("源谱已导入；播放自动跟随文件中的 BPM 与拍号变化", 7000)
        self._ensure_score_panel().import_file(path, preview, failed)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in (
                ".gp", ".gpx", ".gp5", ".gp4", ".gp3", ".musicxml", ".xml", ".mxl", ".json"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self.open_project_path(path) if Path(path).suffix.lower() == ".json" else self.import_score_path(path)
        event.acceptProposedAction()

    def _assign_device(self, client_id, part_id, view, label):
        self._lan.assign_device(client_id, part_id, view, label)

    def _replace_document(self, document, *, reset_editor=True):
        self.transport.set_document(document)
        self._sync_widgets(editor=reset_editor)
        self._refresh_chart()

    def _show_error(self, message):
        self.statusBar().showMessage(str(message), 12000)
        self.input_error.setText(str(message))

    def _project_changed(self):
        if self._syncing:
            return
        try:
            document = replace(self.document, name=self.name_edit.text().strip(), bars=self.bars.value())
            if document != self.document:
                self._replace_document(document, reset_editor=False)
        except ValueError as error:
            self._show_error(error)
            self._sync_widgets(editor=False)

    def _bpm_changed(self, value):
        if not self._syncing:
            try:
                self.transport.set_bpm(value)
                self._refresh_chart()
            except ValueError as error:
                self._show_error(error)
                self._sync_widgets(editor=False)

    def _meter_changed(self, *_args):
        if self._syncing:
            return
        try:
            self.transport.set_meter(self.meter.currentData())
            self._sync_widgets(editor=False)
            self._refresh_chart()
        except ValueError as error:
            self._show_error(error)
            self._sync_widgets(editor=False)

    def _loop_changed(self, *_args):
        if self._syncing:
            return
        try:
            loop = LoopRange(self.loop_start.value(), self.loop_end.value()+1) if self.loop_check.isChecked() else None
            self.transport.set_loop(loop)
            self._refresh_chart()
        except ValueError as error:
            self._show_error(error)
            self._sync_widgets(editor=False)

    def _mark_editor_dirty(self, *_args):
        if not self._syncing:
            self._editor_dirty = True
            self._title()

    def apply_editor(self):
        try:
            events = self.document.events if self.document.score else parse_manual(
                self.chords_edit.toPlainText(), self.document.meter,
                bar_quarters=(lambda bar: measure_quarters(self.document, bar))
                if self.document.timing_changes else None)
            # Report the source line for positions beyond the declared chart length.
            for event in events:
                if event.bar > self.document.bars:
                    raise ValueError(f"Line {event.id+1}: 小节超出曲目长度 {self.document.bars}")
            document = replace(self.document, events=events,
                               manual_sections=(self.document.manual_sections if self.document.score else
                                                parse_sections(self.sections_edit.toPlainText(), self.document.bars)),
                               forced_key=self.forced_key.currentData(), original_key=self.original_key.currentData(),
                               detect_changes=(self.document.detect_changes if self.document.score else
                                               self.detect_changes.isChecked()))
            self._replace_document(document)
            self.statusBar().showMessage("已应用调性显示设置" if document.score else "已应用和弦与调性设置", 4000)
            return True
        except ValueError as error:
            self._show_error(error)
            return False

    def _play_pause(self):
        if self.transport.snapshot()["playing"]:
            self.transport.pause()
        else:
            self.transport.play()
        self._tick()

    def _seek(self, bar, beat=1.0):
        try:
            self.transport.seek(bar, beat)
            self._tick()
        except ValueError as error:
            self._show_error(error)

    def _slider_seek(self):
        position = self.seek_slider.value()/960
        if self.transport.plan is not None:
            self.transport.seek_quarter(position)
            self._tick()
            return
        bar = int(position//self.document.meter)+1
        self._seek(bar, position%self.document.meter+1)

    def _audio_state(self, enabled):
        blocker = QSignalBlocker(self.audio_check)
        self.audio_check.setChecked(enabled)
        del blocker

    def _tick(self):
        if self._closed:
            return
        payload = self.transport.snapshot(self._revision)
        self.position.setText(f"{payload['bar']:03d} · {payload['beat']}")
        if self.transport.plan is not None and payload.get("sourceMeasureId"):
            label = (next(m.number for m in self.document.score.measures if m.id == payload["sourceMeasureId"])
                     if self.document.score else f"{payload['bar']:03d}")
            self.position.setText(f"{label} · ♩+{payload['sourceOffsetQuarter']:.2f}")
            self.position.setToolTip(f"播放出现：{payload.get('occurrenceId', '')}")
        self.play_button.setText("暂停" if payload["playing"] else "播放")
        self.meter.setEnabled(not payload["playing"] and self.document.score is None and
                              not self.document.timing_changes)
        if self.transport.plan is not None:
            self.position.setToolTip(f"♩ = {payload['bpm']:g} · {payload['meter']} "
                                    f"· 播放出现：{payload.get('occurrenceId', '')}")
            self.timing_status.setText(f"♩ = {payload['bpm']:g} · {payload['meter']}")
            self.timing_status.setVisible(True)
        else:
            self.timing_status.setVisible(False)
        if not self.seek_slider.isSliderDown():
            if self.transport.plan is not None and "playQuarter" in payload:
                self.seek_slider.setMaximum(min(2147483647, round(payload["route"]["endQuarter"]*960)))
                ticks = round(payload["playQuarter"]*960)
            else:
                ticks = ((payload["bar"]-1)*self.document.meter+payload["beat"]-1)*960+(payload["division"]-1)*240+payload["tick"]
            self.seek_slider.setValue(ticks)
        self.chart.set_position(payload)
        row = (self.chart.width(), self.chart.bar_row_top(payload["bar"]),
               payload.get("discontinuity", 0))
        if payload["playing"] and row != self._previous_chart_row:
            self.chart.set_follow_space(self.scroll_area.viewport().height())
            self.scroll_area.verticalScrollBar().setValue(row[1])
        self._previous_chart_row = row
        self._previous_bar = payload["bar"]
        self.audio.update_transport(payload, self.document.name)
        if self.document.score and self.score_panel:
            self.score_panel.set_position(payload)
        if self._lan_enabled:
            try:
                publish = getattr(self._lan, "publish_prepared", self._lan.publish)
                publish(self._chart_payload, {key: value for key, value in payload.items() if key != "route"})
            except Exception as error:
                self._toggle_lan(False)
                self._show_error(f"局域网投放已停止：{error}")
            now = time.monotonic()
            if now-self._device_updated >= 1:
                self._device_updated = now
                self.devices.update_devices(getattr(self._lan, "devices", []),
                    self.document.score.parts if self.document.score else ())

    def suspend(self):
        self.transport.pause()
        self.audio.set_enabled(False)
        self._audio_state(False)
        self._tick()

    def _toggle_top(self, enabled):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()
        # Qt may replace the HWND when a window flag changes.
        if hasattr(self, "session_guard"):
            self.session_guard.close()
            self.session_guard = self._session_factory(self, self.suspend)

    def _toggle_lan(self, enabled):
        try:
            if enabled:
                links = self._lan.start()
                self._lan_enabled = True
                self.lan_links.setText("\n".join(links))
                self.lan_panel.show()
                self._tick()
            else:
                self._lan_enabled = False
                self._lan.stop()
                self.lan_panel.hide()
        except Exception as error:
            self._lan_enabled = False
            self._show_error(f"无法启动局域网投放：{error}")
        blocker = QSignalBlocker(self.lan_action)
        self.lan_action.setChecked(self._lan_enabled)
        del blocker

    def _confirm_discard(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "保存更改", "当前曲目有未保存的更改。是否保存？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self.save_project() if answer == QMessageBox.StandardButton.Save else True

    def new_project(self):
        if not self._confirm_discard():
            return False
        self.suspend()
        self.project_path = None
        self._saved_document = ChartDocument()
        self._replace_document(self._saved_document)
        return True

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 ChordCue 曲目", "", "ChordCue / 乐谱 (*.json *.gp *.gpx *.gp5 *.gp4 *.gp3 *.musicxml *.xml *.mxl);;所有文件 (*)")
        if not path:
            return False
        return self.open_project_path(path) if Path(path).suffix.lower() == ".json" else self.import_score_path(path)

    def open_project_path(self, path):
        try:
            document = load_project(path)
        except (ValueError, OSError, TypeError) as error:
            self._show_error(f"打开失败：{error}")
            return False
        if not self._confirm_discard():
            return False
        # Saving the current document may have changed the file being opened,
        # including when the chooser returned an alias of its path.
        try:
            document = load_project(path)
        except (ValueError, OSError, TypeError) as error:
            self._show_error(f"打开失败：{error}")
            return False
        self.suspend()
        self.project_path = Path(path)
        self._saved_document = document
        self._replace_document(document)
        return True

    def save_project(self, *, save_as=False):
        if self._editor_dirty and not self.apply_editor():
            self.editor_action.setChecked(True)
            return False
        path = self.project_path
        if path is None or save_as:
            chosen, _ = QFileDialog.getSaveFileName(self, "保存 ChordCue 曲目", str(path or (self.document.name+".chordcue.json")),
                                                  "ChordCue 项目 (*.chordcue.json)")
            if not chosen:
                return False
            path = Path(chosen if chosen.lower().endswith(".json") else chosen+".chordcue.json")
        try:
            save_project(self.document, path)
            self.project_path = path
            self._saved_document = self.document
            self._title()
            self.statusBar().showMessage("曲目已保存", 4000)
            return True
        except (OSError, ValueError) as error:
            self._show_error(f"保存失败：{error}")
            return False

    def import_text(self):
        if self.document.score:
            self._show_error("源谱和弦来自原文件；修改源文件后请重新导入。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入和弦文本", "", "手动和弦文本 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            events = parse_manual(text, self.document.meter,
                bar_quarters=(lambda bar: measure_quarters(self.document, bar))
                if self.document.timing_changes else None)
            if not events:
                raise ValueError("文本中没有可导入的和弦。")
            if any(event.bar > self.document.bars for event in events):
                raise ValueError("导入和弦超出曲目长度；请先增加小节数。")
            self.chords_edit.setPlainText(format_manual(events))
            self.editor_action.setChecked(True)
            self.statusBar().showMessage("已载入编辑器；点击“应用编辑”更新谱面", 5000)
        except (OSError, ValueError, UnicodeError) as error:
            self._show_error(f"导入失败：{error}")

    def export_chart_pdf(self, path, notation="chords", mode="track"):
        export_pdf(self.document, path, notation, mode, current_mode=self.display_key.currentData(), sections=self._sections)

    def _export_dialog(self, notation, mode):
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", self.document.name+".pdf", "PDF 文件 (*.pdf)")
        if path:
            try:
                self.export_chart_pdf(path, notation, mode)
                self.statusBar().showMessage("PDF 已导出", 5000)
            except (OSError, ValueError) as error:
                self._show_error(error)

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self._closed = True
        self.timer.stop()
        self.settings.set_value("geometry", self.saveGeometry())
        self.settings.set_value("notation", self.notation.currentData())
        self.settings.set_value("displayKey", self.display_key.currentData())
        self.settings.set_value("alwaysOnTop", self.top_action.isChecked())
        self.settings.sync()
        self.session_guard.close()
        self.transport.close()
        self.audio.shutdown()
        if self.score_panel:
            self.score_panel.shutdown()
        try:
            self._lan.stop()
        finally:
            event.accept()
