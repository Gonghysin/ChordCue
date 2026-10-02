"""Native import review and per-musician assignment controls."""
from typing import cast
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from .play_plan import PlayPlan


VIEW_LABELS = {"staff": "五线谱", "tab": "源 TAB", "chords": "和弦谱",
               "numbers": "级数谱", "metronome": "节拍器"}


def available_views(part):
    result = ["staff", "metronome"]
    if part.chords:
        result += ["chords", "numbers"]
    if any(s.tuning and any(n.string is not None for e in s.events for n in e.notes)
           for s in part.staves):
        result.append("tab")
    return result


class ScorePreview(QDialog):
    def __init__(self, score, parent=None):
        super().__init__(parent)
        self.setWindowTitle("预览导入乐谱")
        self.resize(660, 470)
        layout = QVBoxLayout(self)
        title = QLabel(f"{score.title} · {len(score.measures)} 小节 · {len(score.parts)} 声部")
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        self.part = QComboBox()
        for part in score.parts:
            notes = sum(len(event.notes) for staff in part.staves for event in staff.events)
            self.part.addItem(f"{part.name} · {notes} 音符 · {len(part.chords)} 和弦标记", part.id)
        layout.addWidget(self.part)
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        plan = PlayPlan(score)
        meters = ", ".join(dict.fromkeys(f"{m.meter.numerator}/{m.meter.denominator}" for m in score.measures))
        summary = [f"拍号：{meters}", f"速度标记：{len(score.tempo_changes)}；调性标记：{len(score.key_changes)}",
                   f"播放路线：{len(plan.occurrences)} 次小节 · {plan.duration_seconds:.1f} 秒",
                   "和弦只来自源谱明确标记；音符或 TAB 不会自动推断和弦。"]
        summary.extend(f"{part.name}：无和弦标记" for part in score.parts if not part.chords)
        summary.extend(f"[{w.code}] {w.message}" for w in plan.warnings)
        detail.setPlainText("\n".join(summary))
        layout.addWidget(detail, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入所选声部")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DevicePanel(QTableWidget):
    def __init__(self, assign, report_error, parent=None):
        super().__init__(0, 8, parent)
        self._assign, self._report_error = assign, report_error
        self._ids = ()
        self._parts = ()
        self.setHorizontalHeaderLabels(["设备 / 音乐人", "连接 / 同步 / 应用", "声部", "视图",
                                        "网络 RTT", "时钟校准 / 测时抖动 / 探测年龄", "音频输出延迟估计", "最近更新 / 浏览器"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setMaximumHeight(210)
        self.setMinimumHeight(110)

    def update_devices(self, devices, parts):
        identities = tuple(d["clientId"] for d in devices)
        part_ids = tuple(p.id for p in parts)
        rebuild = identities != self._ids or part_ids != self._parts
        if rebuild:
            self._ids, self._parts = identities, part_ids
            self.setRowCount(len(devices))
            for row, device in enumerate(devices):
                label = QLineEdit(device["label"])
                part = QComboBox()
                if not parts:
                    part.addItem("当前和弦谱", None)
                for item in parts:
                    part.addItem(item.name, item.id)
                view = QComboBox()
                self.setCellWidget(row, 0, label)
                self.setCellWidget(row, 2, part)
                self.setCellWidget(row, 3, view)
                def apply(*_, client_id=device["clientId"], label=label, part=part, view=view):
                    try:
                        self._assign(client_id, part.currentData(), view.currentData(), label.text())
                    except (ValueError, PermissionError) as error:
                        self._report_error(str(error))
                label.editingFinished.connect(apply)
                view.currentIndexChanged.connect(apply)
                def part_changed(*_, part=part, view=view, apply=apply):
                    selected = next((p for p in parts if p.id == part.currentData()), None)
                    choices = available_views(selected) if selected else list(VIEW_LABELS)
                    block = QSignalBlocker(view)
                    view.clear()
                    for kind in choices:
                        view.addItem(VIEW_LABELS[kind], kind)
                    del block
                    apply()
                part.currentIndexChanged.connect(part_changed)
        for row, device in enumerate(devices):
            label = cast(QLineEdit, self.cellWidget(row, 0))
            part = cast(QComboBox, self.cellWidget(row, 2))
            view = cast(QComboBox, self.cellWidget(row, 3))
            if not label.hasFocus():
                label.setText(device["label"])
            block = QSignalBlocker(part)
            part.setCurrentIndex(max(0, part.findData(device["partId"])))
            del block
            selected = next((p for p in parts if p.id == device["partId"]), None)
            choices = available_views(selected) if selected else list(VIEW_LABELS)
            block = QSignalBlocker(view)
            view.clear()
            for kind in choices:
                view.addItem(VIEW_LABELS[kind], kind)
            view.setCurrentIndex(max(0, view.findData(device["view"])))
            del block
            sync = {"synchronized": "已同步", "calibrating": "校准中", "disabled": "未启声",
                    "stale": "数据过期", "disconnected": "断开"}.get(device["syncStatus"], device["syncStatus"])
            state = ("在线" if device["connected"] else "离线") + " / " + sync
            state += " / " + ("已应用" if device["applied"] else "待应用")
            values = {1: state, 4: self._metric(device["rttMs"]),
                      5: (f"{dict(valid='有效', calibrating='校准中', stale='过期', unavailable='未提供').get(device.get('clockStatus', 'unavailable'), '未提供')}"
                          f" / {self._metric(device.get('clockJitterMs'))} / {self._metric(device.get('clockProbeAgeMs'))}"),
                      6: ("未提供估计" if device["audioOutputDelayMs"] is None
                          else self._metric(device["audioOutputDelayMs"])),
                      7: f"{device['lastSeenSeconds']:.1f}s · {device['browser']}"}
            for column, text in values.items():
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 5:
                    item.setToolTip("原始时钟原点映射：" + self._metric(device.get("clockOffsetMs"))
                                    + "；测时抖动是探测一致性，不是可听同步误差。")
                if column == 6:
                    item.setToolTip("客户端开启声音或试听后，由浏览器提供的输出延迟估计；"
                                    "音频尚未创建、暂停或浏览器未提供有效估计时显示未提供。"
                                    "此值不包含手动额外补偿，也不是耳边同步误差。")
                self.setItem(row, column, item)

    @staticmethod
    def _metric(value):
        return "未测量" if value is None else f"{value:.1f} ms"
