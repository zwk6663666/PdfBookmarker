"""PDF 目录书签助手 —— 图形界面。

运行：python app.py [可选：PDF 路径]
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import threading
import traceback
import subprocess
from collections import OrderedDict

# PyMuPDF 2.0 起会自动加载版面分析插件（pymupdf.layout），本程序用不到，
# 加载它既拖慢启动，打包成 exe 时还会因缺少模型文件而报错，这里直接屏蔽
import sys as _sys
_sys.modules.setdefault("pymupdf.layout", None)
import pymupdf
from PySide6.QtCore import (Qt, QObject, QThread, Signal, Slot, QTimer, QSize, QRectF,
                            QSettings, QPointF, QItemSelectionModel)
from PySide6.QtGui import (QImage, QPixmap, QPainter, QColor, QKeySequence, QShortcut, QFont,
                           QIcon, QPen, QIntValidator, QPainterPath, QAction)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QToolButton, QSpinBox,
    QCheckBox, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QStyledItemDelegate, QLineEdit, QAbstractItemView, QHeaderView, QScrollArea, QStackedWidget,
    QFileDialog, QMessageBox, QProgressBar, QDialog, QPlainTextEdit, QDialogButtonBox,
    QInputDialog, QSizePolicy, QMenu, QWidgetAction, QGridLayout, QComboBox)

import core
import llm

APP_NAME = "PDF 目录书签助手"
APP_VERSION = "1.0"
ROLE = Qt.UserRole + 1
C_TITLE, C_BOOK, C_PDF, C_STATE = range(4)

STATUS_STYLE = {
    "ok": ("已校验", "#15803d"),
    "fixed": ("已修正", "#b45309"),
    "guess": ("待确认", "#c2410c"),
    "manual": ("手动", "#1d4ed8"),
    "": ("", "#9ca3af"),
}

QSS = """
* { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", sans-serif; }
QMainWindow, #Root { background: #f3f4f6; }
#TopBar { background: #ffffff; border-bottom: 1px solid #e5e7eb; }
#Brand { font-size: 15px; font-weight: 600; color: #111827; padding-right: 8px; }
#Info { color: #6b7280; padding: 0 2px; }
#Panel { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
#PanelTitle { font-size: 14px; font-weight: 600; color: #111827; }
#Muted { color: #6b7280; }
#Sep { background: #e5e7eb; border: none; margin: 0 6px; }
QPushButton {
    background: #ffffff; color: #1f2937; border: 1px solid #d1d5db; border-radius: 6px;
    padding: 5px 12px; min-height: 20px;
}
QPushButton:hover { background: #f9fafb; border-color: #9ca3af; }
QPushButton:pressed { background: #f3f4f6; }
QPushButton:disabled { color: #9ca3af; background: #f9fafb; border-color: #e5e7eb; }
QPushButton#Primary { background: #2563eb; color: #ffffff; border: 1px solid #2563eb; font-weight: 600; }
QPushButton#Primary:hover { background: #1d4ed8; border-color: #1d4ed8; }
QPushButton#Primary:disabled { background: #93c5fd; border-color: #93c5fd; color: #eff6ff; }
QToolButton {
    background: transparent; color: #374151; border: 1px solid transparent; border-radius: 6px;
    padding: 4px 8px;
}
QToolButton:hover { background: #f3f4f6; border-color: #e5e7eb; }
QToolButton:pressed { background: #e5e7eb; }
QToolButton:disabled { color: #c0c4cc; }
QToolButton::menu-indicator { image: none; }
QSpinBox {
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px; padding: 3px 6px; min-height: 20px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }
QSpinBox:disabled { color: #9ca3af; background: #f3f4f6; border-color: #e5e7eb; }
QToolButton#Step { border: 1px solid #d1d5db; background: #ffffff; min-width: 22px; max-width: 22px; padding: 2px 0; font-weight: 600; }
QToolButton#Step:hover { background: #f3f4f6; }
QSpinBox:focus, QLineEdit:focus { border-color: #2563eb; }
QCheckBox { color: #374151; spacing: 6px; }
QTreeWidget {
    background: #ffffff; border: none; outline: 0; alternate-background-color: #fafafa;
    selection-background-color: #dbeafe; selection-color: #111827;
}
QTreeWidget::item { padding: 2px 0px; }
QTreeWidget::item:selected { background: #dbeafe; color: #111827; }
QTreeWidget::item:hover:!selected { background: #f3f4f6; }
QHeaderView::section {
    background: #f9fafb; color: #6b7280; border: none; border-bottom: 1px solid #e5e7eb;
    padding: 6px 8px; font-weight: 500;
}
QLineEdit { border: 1px solid #2563eb; border-radius: 4px; padding: 1px 4px; background: #ffffff; }
QScrollArea#Preview { background: #e5e7eb; border: none; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }
QScrollArea#Preview > QWidget > QWidget { background: #e5e7eb; }
QStatusBar { background: #ffffff; border-top: 1px solid #e5e7eb; color: #4b5563; }
QProgressBar { border: none; background: #e5e7eb; border-radius: 3px; max-height: 6px; min-width: 180px; }
QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
#DropZone { background: #ffffff; border: 2px dashed #cbd5e1; border-radius: 14px; }
#DropZone[hover="true"] { border-color: #2563eb; background: #eff6ff; }
#DropTitle { font-size: 22px; font-weight: 600; color: #111827; }
#DropSub { font-size: 13px; color: #6b7280; }
QSplitter::handle { background: transparent; }
QPlainTextEdit { border: 1px solid #d1d5db; border-radius: 6px; background: #ffffff; padding: 4px; }
QMenu { background: #ffffff; border: 1px solid #e5e7eb; padding: 6px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: #eff6ff; color: #1d4ed8; }
QComboBox {
    background: #ffffff; color: #111827; border: 1px solid #d1d5db; border-radius: 6px;
    padding: 4px 6px; min-height: 20px;
}
QComboBox:focus, QComboBox:on { border-color: #2563eb; }
QComboBox QAbstractItemView {
    background: #ffffff; color: #111827; border: 1px solid #e5e7eb; outline: 0; padding: 2px;
    selection-background-color: #eff6ff; selection-color: #1d4ed8;
}
QComboBox QAbstractItemView::item { min-height: 26px; padding: 0 8px; color: #111827; background: #ffffff; }
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
    background: #eff6ff; color: #1d4ed8;
}
QPushButton#Stop { background: #fef2f2; color: #b91c1c; border-color: #fca5a5; font-weight: 600; }
QPushButton#Stop:hover { background: #fee2e2; border-color: #f87171; }
"""


# ----------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------
def _paint_icon(size) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(17, 13, 30, 38, 4, 4)
    path = QPainterPath()
    path.moveTo(35, 13)
    path.lineTo(45, 13)
    path.lineTo(45, 32)
    path.lineTo(40, 27)
    path.lineTo(35, 32)
    path.closeSubpath()
    p.setBrush(QColor("#f59e0b"))
    p.drawPath(path)
    p.setPen(QPen(QColor("#93c5fd"), 2.2))
    for y in (37, 43):
        p.drawLine(22, y, 41, y)
    p.end()
    return pm


def make_app_icon() -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_paint_icon(sz))
    return icon


def stop_icon() -> QIcon:
    """“终止”按钮上的红色方块。直接画出来，不依赖字体里是否有 ■ 字符。"""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#b91c1c"))
    p.drawRoundedRect(QRectF(7, 7, 18, 18), 3, 3)
    p.end()
    return QIcon(pm)


def open_with_system(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        QMessageBox.warning(None, APP_NAME, f"无法打开：{e}")


def reveal_in_folder(path):
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as e:
        QMessageBox.warning(None, APP_NAME, f"无法打开文件夹：{e}")


def strip_numbering(title: str) -> str:
    t = re.sub(r"^(\d+(?:\.\d+)*|第\s*[一二三四五六七八九十百零〇\d]+\s*[章节部篇编卷]|"
               r"[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）])\s*", "", title.strip())
    return t.rstrip("*").strip() or title.strip()


# ----------------------------------------------------------------------------
# 后台任务
# ----------------------------------------------------------------------------
class Worker(QObject):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.cancel = threading.Event()

    @Slot()
    def run(self):
        try:
            res = self.fn(*self.args, progress=self._prog, cancel=self.cancel, **self.kw)
            self.done.emit(res)
        except core.Cancelled:
            self.failed.emit("已取消")
        except Exception as e:  # noqa
            traceback.print_exc()
            self.failed.emit(str(e) or type(e).__name__)

    def _prog(self, p, m):
        self.progress.emit(int(p), str(m))


# ----------------------------------------------------------------------------
# 书签树
# ----------------------------------------------------------------------------
class Delegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if index.column() == C_STATE:
            return None
        ed = QLineEdit(parent)
        if index.column() in (C_BOOK, C_PDF):
            ed.setValidator(QIntValidator(-99999, 99999, ed))
            ed.setAlignment(Qt.AlignCenter)
        return ed

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), 28))


class BookmarkTree(QTreeWidget):
    dropped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["标题", "书中页码", "PDF 页码", "状态"])
        self.setItemDelegate(Delegate(self))
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                             | QAbstractItemView.SelectedClicked)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setIndentation(18)
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(C_TITLE, QHeaderView.Stretch)
        for c, w in ((C_BOOK, 76), (C_PDF, 76), (C_STATE, 64)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.setColumnWidth(c, w)
        h.setDefaultAlignment(Qt.AlignCenter)

    def dropEvent(self, e):
        super().dropEvent(e)
        self.dropped.emit()


# ----------------------------------------------------------------------------
# 页面预览
# ----------------------------------------------------------------------------
class PageView(QScrollArea):
    pageChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Preview")
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.label.setContentsMargins(16, 16, 16, 16)
        self.setWidget(self.label)
        self.doc = None
        self.pno = 0
        self.fit = "width"     # width / page / None
        self.zoom = 1.0
        self.highlight = None  # 要高亮的文字
        self.cache = OrderedDict()
        self._timer = QTimer(self, singleShot=True, interval=60, timeout=self.render)

    def set_document(self, doc):
        self.doc = doc
        self.cache.clear()
        self.pno = 0
        self.render()

    def clear(self):
        self.doc = None
        self.cache.clear()
        self.label.clear()

    def page_count(self):
        return len(self.doc) if self.doc is not None else 0

    def show_page(self, pno, highlight=None, keep_scroll=False):
        if self.doc is None:
            return
        pno = max(0, min(pno, self.page_count() - 1))
        changed = pno != self.pno
        self.pno = pno
        self.highlight = highlight
        self.render()
        if not keep_scroll:
            self.verticalScrollBar().setValue(0)
        if changed:
            self.pageChanged.emit(pno)
        else:
            self.pageChanged.emit(pno)

    def set_fit(self, fit):
        self.fit = fit
        self.render()

    def zoom_by(self, f):
        self.zoom = max(0.2, min(6.0, self._current_zoom() * f))
        self.fit = None
        self.render()

    def _current_zoom(self):
        if self.doc is None:
            return 1.0
        r = self.doc[self.pno].rect
        sbw = self.verticalScrollBar().sizeHint().width()
        vw = max(100, self.width() - sbw - 40)
        vh = max(100, self.height() - 40)
        if self.fit == "width":
            return vw / r.width
        if self.fit == "page":
            return min(vw / r.width, vh / r.height)
        return self.zoom

    def render(self):
        if self.doc is None:
            return
        dpr = self.devicePixelRatioF()
        z = self._current_zoom()
        key = (self.pno, round(z * dpr, 3))
        pm = self.cache.get(key)
        if pm is None:
            if not core.FITZ_LOCK.acquire(blocking=False):
                self._timer.start()   # 后台正在使用 PDF，稍后再画
                return
            try:
                page = self.doc[self.pno]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(z * dpr, z * dpr), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            finally:
                core.FITZ_LOCK.release()
            pm = QPixmap.fromImage(img)
            self.cache[key] = pm
            while len(self.cache) > 10:
                self.cache.popitem(last=False)
        else:
            self.cache.move_to_end(key)
        out = pm
        rects = self._highlight_rects()
        if rects:
            out = QPixmap(pm)
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(245, 158, 11), 2 * dpr))
            p.setBrush(QColor(253, 224, 71, 90))
            s = z * dpr
            for r in rects:
                p.drawRoundedRect(QRectF(r.x0 * s - 3 * dpr, r.y0 * s - 2 * dpr,
                                         r.width * s + 6 * dpr, r.height * s + 4 * dpr), 3 * dpr, 3 * dpr)
            p.end()
        out = QPixmap(out)
        out.setDevicePixelRatio(dpr)
        self.label.setPixmap(out)

    def _highlight_rects(self):
        if not self.highlight or self.doc is None:
            return []
        if not core.FITZ_LOCK.acquire(blocking=False):
            return []
        try:
            page = self.doc[self.pno]
            key = strip_numbering(self.highlight)[:18]
            if len(key) < 2:
                return []
            rects = page.search_for(key, quads=False)
            if not rects and " " in key:
                rects = page.search_for(key.split(" ")[0], quads=False)
            if not rects:
                return []
            # 同一页里标题文字可能出现多次，取字号最大的一处（标题字号通常更大），并列时取最上面的
            best = max(rects, key=lambda r: (round(r.height, 1), -r.y0))
            return [best]
        except Exception:
            return []
        finally:
            core.FITZ_LOCK.release()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self._timer.start()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.12 if e.angleDelta().y() > 0 else 1 / 1.12)
            return
        sb = self.verticalScrollBar()
        dy = e.angleDelta().y()
        if dy < 0 and sb.value() >= sb.maximum() and self.pno < self.page_count() - 1:
            self.show_page(self.pno + 1)
            return
        if dy > 0 and sb.value() <= sb.minimum() and self.pno > 0:
            self.show_page(self.pno - 1)
            sb.setValue(sb.maximum())
            return
        super().wheelEvent(e)


# ----------------------------------------------------------------------------
# 空状态（拖入文件）
# ----------------------------------------------------------------------------
class DropZone(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(make_app_icon().pixmap(72, 72))
        icon.setAlignment(Qt.AlignCenter)
        t = QLabel("把 PDF 书籍拖到这里")
        t.setObjectName("DropTitle")
        t.setAlignment(Qt.AlignCenter)
        s = QLabel("自动识别目录页并按页码生成书签，文字版和扫描版都支持")
        s.setObjectName("DropSub")
        s.setAlignment(Qt.AlignCenter)
        b = QPushButton("选择 PDF 文件")
        b.setObjectName("Primary")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedWidth(160)
        b.clicked.connect(self.clicked)
        lay.addWidget(icon)
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addSpacing(8)
        lay.addWidget(b, 0, Qt.AlignCenter)

    def set_hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


# ----------------------------------------------------------------------------
# 批量文本编辑
# ----------------------------------------------------------------------------
class TextEditDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("文本方式编辑目录")
        self.resize(720, 640)
        lay = QVBoxLayout(self)
        tip = QLabel("每行一条书签。行首用 Tab（或 4 个空格）缩进表示层级，行末数字是书中页码；"
                     "写成 @数字 表示直接指定 PDF 页码。")
        tip.setWordWrap(True)
        tip.setObjectName("Muted")
        self.edit = QPlainTextEdit()
        self.edit.setPlainText(text)
        self.edit.setTabStopDistance(28)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setFamilies(["Consolas", "Microsoft YaHei UI", "Noto Sans CJK SC"])
        f.setPointSize(10)
        self.edit.setFont(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("应用")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(tip)
        lay.addWidget(self.edit, 1)
        lay.addWidget(bb)

    def text(self):
        return self.edit.toPlainText()


def make_combo():
    """下拉框默认的列表委托不认样式表里的 ::item 规则，鼠标悬停时会画成一片白色，
    换成 QStyledItemDelegate 后才能按样式表显示悬停/选中效果。"""
    cb = QComboBox()
    cb.setItemDelegate(QStyledItemDelegate(cb))
    return cb


def load_llm_cfg(settings, pid=None):
    """读取某个接口（默认当前选中的接口）保存的配置。"""
    pid = pid or settings.value("llm/provider", "openai")
    if pid not in llm.PROVIDER_MAP:
        pid = "openai"
    _, _, proto, base, models, _ = llm.PROVIDER_MAP[pid]
    g = f"llm/{pid}/"
    return dict(provider=pid,
                protocol=settings.value(g + "protocol", proto) if pid == "custom" else proto,
                base=settings.value(g + "base", base),
                key=settings.value(g + "key", ""),
                model=settings.value(g + "model", models[0] if models else ""))


def llm_cfg_problem(cfg):
    """配置不完整时返回原因，否则返回空字符串。"""
    if not (cfg.get("base") or "").strip():
        return "没有填写接口地址"
    if not (cfg.get("model") or "").strip():
        return "没有填写模型名称"
    if llm.PROVIDER_MAP[cfg["provider"]][5] and not (cfg.get("key") or "").strip():
        return "没有填写 API 密钥"
    return ""


class LLMSettingsDialog(QDialog):
    tested = Signal(bool, str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("大模型识别接口")
        self.setMinimumWidth(560)
        # 全局样式里的 QLineEdit 是给书签编辑框用的（常亮蓝框），这里改回普通输入框
        self.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 6px; padding: 4px 6px;"
            " background: #ffffff; min-height: 20px; }"
            "QLineEdit:focus { border-color: #2563eb; }"
            "QComboBox QLineEdit { border: none; padding: 0; min-height: 0; }")
        lay = QVBoxLayout(self)
        tip = QLabel("选择“大模型”识别引擎时，目录页图片会发送到下面的接口，由视觉大模型读出标题、页码和层级；"
                     "查找目录页、推算页码偏移仍在本机完成。调用失败时自动改用本地 OCR 结果。")
        tip.setWordWrap(True)
        tip.setObjectName("Muted")
        lay.addWidget(tip)

        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        self.cb_provider = make_combo()
        for pid, name, *_ in llm.PROVIDERS:
            self.cb_provider.addItem(name, pid)
        self.cb_protocol = make_combo()
        for proto, name in llm.PROTOCOLS:
            self.cb_protocol.addItem(name, proto)
        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText("例如 https://api.openai.com/v1")
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-…")
        self.cb_show = QCheckBox("显示")
        self.cb_show.toggled.connect(
            lambda on: self.ed_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.cb_model = make_combo()
        self.cb_model.setEditable(True)
        self.cb_model.lineEdit().setPlaceholderText("需支持图片输入的模型")
        key_row = QHBoxLayout()
        key_row.addWidget(self.ed_key, 1)
        key_row.addWidget(self.cb_show)
        rows = [("接口", self.cb_provider), ("协议", self.cb_protocol), ("接口地址", self.ed_base),
                ("API 密钥", key_row), ("模型", self.cb_model)]
        for r, (label, w) in enumerate(rows):
            g.addWidget(QLabel(label), r, 0)
            if isinstance(w, QHBoxLayout):
                g.addLayout(w, r, 1)
            else:
                g.addWidget(w, r, 1)
        g.setColumnStretch(1, 1)
        lay.addLayout(g)
        note = QLabel("密钥只保存在本机的程序设置中。模型名可从下拉中选择，也可以直接输入。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.run_test)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("保存")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_test)
        bottom.addWidget(self.lbl_test, 1)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

        # 切换接口时各自的配置分别保存，切回来不丢失
        self._cfgs = {}
        self._pid = None
        self.tested.connect(self._test_done)
        self.cb_provider.currentIndexChanged.connect(self._provider_changed)
        cur = load_llm_cfg(settings)["provider"]
        self.cb_provider.setCurrentIndex(max(0, self.cb_provider.findData(cur)))
        self._provider_changed()

    def _form_cfg(self):
        return dict(provider=self._pid, protocol=self.cb_protocol.currentData(),
                    base=self.ed_base.text().strip(), key=self.ed_key.text().strip(),
                    model=self.cb_model.currentText().strip())

    def _provider_changed(self):
        if self._pid is not None:
            self._cfgs[self._pid] = self._form_cfg()
        pid = self.cb_provider.currentData()
        self._pid = pid
        cfg = self._cfgs.get(pid) or load_llm_cfg(self.settings, pid)
        models = llm.PROVIDER_MAP[pid][4]
        self.cb_protocol.setCurrentIndex(max(0, self.cb_protocol.findData(cfg["protocol"])))
        self.cb_protocol.setEnabled(pid == "custom")
        self.ed_base.setText(cfg["base"])
        self.ed_key.setText(cfg["key"])
        self.ed_key.setPlaceholderText("sk-…" if llm.PROVIDER_MAP[pid][5] else "本机服务一般不需要密钥")
        self.cb_model.clear()
        self.cb_model.addItems(models)
        self.cb_model.setCurrentText(cfg["model"])
        self.lbl_test.clear()

    def run_test(self):
        cfg = self._form_cfg()
        why = llm_cfg_problem(cfg)
        if why:
            self.lbl_test.setText(f"<span style='color:#dc2626'>{why}</span>")
            return
        self.btn_test.setEnabled(False)
        self.lbl_test.setText("正在连接…")

        def work():
            try:
                self.tested.emit(True, llm.test(cfg))
            except Exception as e:  # noqa
                self.tested.emit(False, str(e))
        threading.Thread(target=work, daemon=True).start()

    @Slot(bool, str)
    def _test_done(self, ok, msg):
        self.btn_test.setEnabled(True)
        color = "#15803d" if ok else "#dc2626"
        text = f"连接成功，模型回复：{msg}" if ok else msg
        self.lbl_test.setText(f"<span style='color:{color}'>{html.escape(text)}</span>")

    def accept(self):
        self._cfgs[self._pid] = self._form_cfg()
        for pid, cfg in self._cfgs.items():
            g = f"llm/{pid}/"
            for k in ("base", "key", "model"):
                self.settings.setValue(g + k, cfg[k])
            if pid == "custom":
                self.settings.setValue(g + "protocol", cfg["protocol"])
        self.settings.setValue("llm/provider", self._pid)
        super().accept()


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_app_icon())
        self.setAcceptDrops(True)
        self.settings = QSettings("PdfBookmarker", "PdfBookmarker")

        self.path = None
        self.password = None
        self.doc = None
        self.mode = "text"
        self.toc_pages = []
        self.thread = None
        self.worker = None
        self._on_done = None
        self._task_fn = None
        self._updating = False
        self.undo_stack, self.redo_stack = [], []
        self._last_snap = []

        root = QWidget(objectName="Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_topbar())

        self.stack = QStackedWidget()
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(40, 32, 40, 40)
        self.drop = DropZone()
        self.drop.clicked.connect(self.choose_file)
        hl.addWidget(self.drop)
        self.stack.addWidget(holder)
        self.stack.addWidget(self._build_workspace())
        v.addWidget(self.stack, 1)
        self._build_statusbar()
        self._build_shortcuts()
        self._set_doc_loaded(False)

        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1360, 860)

    # ---------------------------------------------------------------- 构建界面
    def _build_topbar(self):
        bar = QFrame(objectName="TopBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(make_app_icon().pixmap(24, 24))
        brand = QLabel("书签助手", objectName="Brand")
        h.addWidget(logo)
        h.addWidget(brand)

        self.btn_open = QPushButton("打开 PDF")
        self.btn_open.clicked.connect(self.choose_file)
        self.btn_detect = QPushButton("重新识别")
        self.btn_detect.setToolTip("按右侧设置重新识别目录")
        self.btn_detect.clicked.connect(self._detect_clicked)

        # 识别设置
        self.btn_opts = QToolButton()
        self.btn_opts.setText("识别设置 ▾")
        self.btn_opts.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_opts)
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(10)
        self.cb_ocr = QCheckBox("强制使用 OCR（文字层是乱码时勾选）")
        g.addWidget(self.cb_ocr, 1, 0, 1, 4)
        g.addWidget(QLabel("识别引擎"), 2, 0)
        self.cb_engine = make_combo()
        self.cb_engine.addItem("本地识别（默认）", "local")
        self.cb_engine.addItem("大模型识别", "llm")
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData(self.settings.value("ocr/engine", "local"))))
        self.cb_engine.currentIndexChanged.connect(self._engine_changed)
        g.addWidget(self.cb_engine, 2, 1, 1, 2)
        btn_llm = QPushButton("接口设置…")
        btn_llm.clicked.connect(lambda: (menu.hide(), self.edit_llm_settings()))
        g.addWidget(btn_llm, 2, 3)
        self.lbl_engine = QLabel()
        self.lbl_engine.setObjectName("Muted")
        g.addWidget(self.lbl_engine, 3, 0, 1, 4)
        self._update_engine_label()
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(box)
        menu.addAction(wa)
        self.btn_opts.setMenu(menu)

        # 目录页范围：勾选“自动识别”时由程序查找，输入框不可编辑；取消勾选后按填写的范围识别
        toc = QWidget()
        th = QHBoxLayout(toc)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(6)
        th.addWidget(QLabel("目录页"))
        self.sp_toc_a = QSpinBox()
        self.sp_toc_b = QSpinBox()
        for sp in (self.sp_toc_a, self.sp_toc_b):
            sp.setRange(0, 99999)
            sp.setSpecialValueText("–")
            sp.setFixedWidth(64)
            sp.setAlignment(Qt.AlignCenter)
            sp.setToolTip("目录所在的 PDF 页码范围（取消勾选“自动识别”后可填写）")
            sp.setEnabled(False)
        self.cb_toc_auto = QCheckBox("自动识别")
        self.cb_toc_auto.setChecked(True)
        self.cb_toc_auto.setToolTip("勾选时由程序查找目录页；取消勾选后可手动填写目录页范围")
        self.cb_toc_auto.toggled.connect(self._toc_auto_toggled)
        th.addWidget(self.sp_toc_a)
        th.addWidget(QLabel("至"))
        th.addWidget(self.sp_toc_b)
        th.addWidget(self.cb_toc_auto)

        self.info = QLabel("", objectName="Info")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.btn_view = QPushButton("用阅读器预览")
        self.btn_view.setToolTip("生成临时文件，用系统默认的 PDF 阅读器打开，查看书签实际效果")
        self.btn_view.clicked.connect(self.preview_in_reader)
        self.btn_export = QPushButton("导出 PDF", objectName="Primary")
        self.btn_export.setToolTip("Ctrl+S")
        self.btn_export.clicked.connect(self.export)

        sep = QFrame(objectName="Sep")
        sep.setFixedSize(1, 22)
        h.addSpacing(6)
        h.addWidget(self.btn_open)
        h.addWidget(self.btn_detect)
        h.addWidget(self.btn_opts)
        h.addSpacing(6)
        h.addWidget(toc)
        h.addWidget(sep)
        h.addWidget(self.info, 1)
        h.addWidget(self.btn_view)
        h.addWidget(self.btn_export)
        return bar

    def _build_workspace(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(10)
        self.split.addWidget(self._build_left())
        self.split.addWidget(self._build_right())
        self.split.setStretchFactor(0, 5)
        self.split.setStretchFactor(1, 6)
        st = self.settings.value("splitter")
        if st is not None:
            self.split.restoreState(st)
        else:
            self.split.setSizes([620, 720])
        lay.addWidget(self.split)
        return w

    def _tool(self, text, tip, slot, shortcut=None):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip + (f"（{shortcut}）" if shortcut else ""))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _build_left(self):
        panel = QFrame(objectName="Panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(14, 10, 10, 8)
        self.lbl_count = QLabel("书签", objectName="PanelTitle")
        hh.addWidget(self.lbl_count)
        hh.addStretch(1)
        hh.addWidget(QLabel("页码偏移", objectName="Muted"))
        self.sp_offset = QSpinBox()
        self.sp_offset.setRange(-9999, 9999)
        self.sp_offset.setFixedWidth(60)
        self.sp_offset.setAlignment(Qt.AlignCenter)
        self.sp_offset.setToolTip("PDF 页码 = 书中页码 + 偏移\n修改后所有条目的 PDF 页码一起变化（可用滚轮或上下键调整）")
        self.sp_offset.valueChanged.connect(self._offset_changed)
        self.btn_off_minus = QToolButton(objectName="Step", text="−")
        self.btn_off_plus = QToolButton(objectName="Step", text="+")
        self.btn_off_minus.clicked.connect(self.sp_offset.stepDown)
        self.btn_off_plus.clicked.connect(self.sp_offset.stepUp)
        hh.addWidget(self.btn_off_minus)
        hh.addWidget(self.sp_offset)
        hh.addWidget(self.btn_off_plus)
        v.addWidget(head)

        tools = QWidget()
        th = QHBoxLayout(tools)
        th.setContentsMargins(8, 0, 8, 6)
        th.setSpacing(2)
        self.tool_buttons = [
            self._tool("＋ 新增", "在当前条目后新增一条", self.add_item, "Ctrl+N"),
            self._tool("删除", "删除选中的条目", self.delete_items, "Delete"),
            None,
            self._tool("↑", "上移", lambda: self.move_item(-1), "Ctrl+↑"),
            self._tool("↓", "下移", lambda: self.move_item(1), "Ctrl+↓"),
            self._tool("← 升级", "提升一级（可多选批量）", self.outdent, "Ctrl+←"),
            self._tool("降级 →", "降低一级，成为上一条的子项（可多选批量）", self.indent, "Ctrl+→"),
            None,
            self._tool("定位到当前页", "把选中条目的 PDF 页码设为右侧正在预览的页", self.set_to_current_page, "Ctrl+G"),
            self._tool("文本编辑", "以纯文本方式批量编辑整个目录", self.edit_as_text),
        ]
        for b in self.tool_buttons:
            if b is None:
                s = QFrame(objectName="Sep")
                s.setFixedSize(1, 18)
                th.addWidget(s)
            else:
                th.addWidget(b)
        th.addStretch(1)
        more = QToolButton()
        more.setText("更多 ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(more)
        m.addAction("撤销\tCtrl+Z", self.undo)
        m.addAction("重做\tCtrl+Y", self.redo)
        m.addSeparator()
        m.addAction("核对页码（到正文中查找标题）", self.start_verify)
        m.addSeparator()
        m.addAction("全部展开", lambda: self.tree.expandAll())
        m.addAction("只展开第一级", self.collapse_to_top)
        m.addSeparator()
        m.addAction("载入 PDF 自带书签", self.load_existing)
        m.addAction("清空全部书签", self.clear_all)
        more.setMenu(m)
        th.addWidget(more)
        self.tool_buttons.append(more)
        v.addWidget(tools)

        self.tree = BookmarkTree()
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(lambda cur, prev: self._preview_item(cur))
        self.tree.itemClicked.connect(lambda it, col: self._preview_item(it))
        self.tree.dropped.connect(self._after_drop)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#e5e7eb;")
        v.addWidget(line)
        v.addWidget(self.tree, 1)

        foot = QLabel("双击修改标题或页码 · 拖动调整顺序和层级 · 选中条目后右侧显示对应页面")
        foot.setObjectName("Muted")
        foot.setContentsMargins(14, 6, 14, 8)
        v.addWidget(foot)
        return panel

    def _build_right(self):
        panel = QFrame(objectName="Panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 8, 10, 8)
        h.setSpacing(4)
        t = QLabel("页面预览", objectName="PanelTitle")
        h.addWidget(t)
        h.addSpacing(10)
        self.lbl_target = QLabel("", objectName="Muted")
        self.lbl_target.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(self.lbl_target, 1)
        self.btn_prev = self._tool("◀", "上一页", lambda: self.view.show_page(self.view.pno - 1), "PageUp")
        self.btn_next = self._tool("▶", "下一页", lambda: self.view.show_page(self.view.pno + 1), "PageDown")
        self.sp_page = QSpinBox()
        self.sp_page.setRange(1, 1)
        self.sp_page.setFixedWidth(64)
        self.sp_page.setAlignment(Qt.AlignCenter)
        self.sp_page.setKeyboardTracking(False)
        self.sp_page.valueChanged.connect(lambda v: self.view.show_page(v - 1) if v - 1 != self.view.pno else None)
        self.lbl_total = QLabel("/ 0", objectName="Muted")
        self.btn_fitw = self._tool("适合宽度", "页面宽度适应窗口", lambda: self.view.set_fit("width"))
        self.btn_fitp = self._tool("整页", "显示整页", lambda: self.view.set_fit("page"))
        for w in (self.btn_prev, self.sp_page, self.lbl_total, self.btn_next, self.btn_fitw, self.btn_fitp):
            h.addWidget(w)
        v.addWidget(head)
        self.view = PageView()
        self.view.pageChanged.connect(self._page_changed)
        v.addWidget(self.view, 1)
        return panel

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setContentsMargins(8, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        self.btn_cancel = QToolButton()
        self.btn_cancel.setText("取消")
        self.btn_cancel.clicked.connect(self.cancel_task)
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.btn_cancel)
        self.progress.hide()
        self.btn_cancel.hide()

    def _build_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)
        sc("Ctrl+O", self.choose_file)
        sc("Ctrl+S", self.export)
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Ctrl+N", self.add_item)
        sc("Ctrl+G", self.set_to_current_page)
        sc("Ctrl+Up", lambda: self.move_item(-1))
        sc("Ctrl+Down", lambda: self.move_item(1))
        sc("Ctrl+Left", self.outdent)
        sc("Ctrl+Right", self.indent)
        sc("PgUp", lambda: self.view.show_page(self.view.pno - 1))
        sc("PgDown", lambda: self.view.show_page(self.view.pno + 1))
        d = QShortcut(QKeySequence(Qt.Key_Delete), self.tree)
        d.setContext(Qt.WidgetShortcut)
        d.activated.connect(self.delete_items)

    # ---------------------------------------------------------------- 状态
    def _set_doc_loaded(self, on):
        self.stack.setCurrentIndex(1 if on else 0)
        # “识别设置”不依赖已打开的文件，始终可用
        for w in (self.btn_detect, self.btn_view, self.btn_export):
            w.setEnabled(on)

    def _set_busy(self, busy, text=""):
        for w in [self.btn_open, self.btn_view, self.btn_export,
                  self.sp_offset, self.btn_off_minus, self.btn_off_plus, self.tree] + [b for b in self.tool_buttons if b is not None]:
            w.setEnabled(not busy)
        # 后台任务运行时，“重新识别”按钮变成“终止”按钮
        self.btn_detect.setText(("终止识别" if self._task_fn is core.recognize else "终止") if busy else "重新识别")
        self.btn_detect.setIcon(stop_icon() if busy else QIcon())
        self.btn_detect.setToolTip("终止当前任务" if busy else "按右侧设置重新识别目录")
        self.btn_detect.setObjectName("Stop" if busy else "")
        self.btn_detect.style().unpolish(self.btn_detect)
        self.btn_detect.style().polish(self.btn_detect)
        self.btn_detect.setEnabled(busy or self.doc is not None)
        self.progress.setVisible(busy)
        self.btn_cancel.setVisible(busy)
        if busy:
            self.progress.setValue(0)
            self.lbl_status.setText(text)
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()

    def status(self, text):
        self.lbl_status.setText(text)

    # ---------------------------------------------------------------- 后台任务
    def run_task(self, fn, *args, on_done=None, text="正在处理", **kw):
        if self.thread is not None:
            return False
        self._on_done = on_done
        self._task_fn = fn
        self.thread = QThread(self)
        self.worker = Worker(fn, *args, **kw)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._task_progress)
        self.worker.done.connect(self._task_done)
        self.worker.failed.connect(self._task_failed)
        self.thread.finished.connect(self._task_cleanup)
        self._set_busy(True, text)
        self.thread.start()
        return True

    @Slot(int, str)
    def _task_progress(self, p, m):
        self.progress.setValue(p)
        self.lbl_status.setText(m)

    @Slot(object)
    def _task_done(self, res):
        cb = self._on_done
        self.thread.quit()
        self._set_busy(False)
        if cb:
            cb(res)

    @Slot(str)
    def _task_failed(self, msg):
        self.thread.quit()
        self._set_busy(False)
        if msg == "已取消":
            self.status("已取消")
        else:
            self.status("出错了：" + msg)
            QMessageBox.warning(self, APP_NAME, msg)

    @Slot()
    def _task_cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None

    def _detect_clicked(self):
        if self.thread is not None:
            self.cancel_task()
        else:
            self.start_recognize()

    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel.set()
            self.status("正在取消…")

    # ---------------------------------------------------------------- 打开文件
    def choose_file(self):
        if self.thread is not None:
            return
        d = self.settings.value("lastdir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF 书籍", d, "PDF 文件 (*.pdf)")
        if path:
            self.open_pdf(path)

    def open_pdf(self, path):
        if self.thread is not None:
            return
        try:
            with core.FITZ_LOCK:
                doc = pymupdf.open(path)
                pw = None
                if doc.needs_pass:
                    while True:
                        pw, ok = QInputDialog.getText(self, APP_NAME, "该 PDF 已加密，请输入密码：", QLineEdit.Password)
                        if not ok:
                            doc.close()
                            return
                        if doc.authenticate(pw):
                            break
                if not doc.is_pdf:
                    raise RuntimeError("这不是 PDF 文件")
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"无法打开文件：\n{e}")
            return
        if self.doc is not None:
            with core.FITZ_LOCK:
                self.doc.close()
        self.doc, self.path, self.password = doc, path, pw
        self.settings.setValue("lastdir", os.path.dirname(path))
        self.setWindowTitle(f"{os.path.basename(path)} - {APP_NAME}")
        self._set_doc_loaded(True)
        self.view.set_document(doc)
        self.sp_page.setRange(1, len(doc))
        self.lbl_total.setText(f"/ {len(doc)}")
        self._page_changed(0)
        self._set_toc_range(0, 0)
        self.cb_toc_auto.setChecked(True)
        self.tree.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._last_snap = []
        self._update_count()
        self.info.setText(f"{os.path.basename(path)} · {len(doc)} 页")

        existing = core.existing_toc(doc)
        if existing:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText(f"这个 PDF 已经带有 {len(existing)} 条书签。")
            box.setInformativeText("可以在现有书签基础上修改，也可以重新识别目录。")
            b1 = box.addButton("载入现有书签", QMessageBox.AcceptRole)
            box.addButton("重新识别", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b1:
                self.load_existing()
                return
        self.start_recognize()

    # ---------------------------------------------------------------- 识别
    def start_recognize(self):
        if not self.path:
            return
        a, b = self.sp_toc_a.value(), self.sp_toc_b.value()
        rng = None
        if not self.cb_toc_auto.isChecked() and (a or b):
            a = a or b
            b = b or a
            rng = (min(a, b), max(a, b))
        llm_cfg = None
        if self.cb_engine.currentData() == "llm":
            llm_cfg = load_llm_cfg(self.settings)
            why = llm_cfg_problem(llm_cfg)
            if why:
                r = QMessageBox.question(self, APP_NAME, f"大模型接口尚未配置好（{why}）。\n\n"
                                         "是否现在设置？选“否”则本次使用本地识别。")
                if r == QMessageBox.Yes:
                    if not self.edit_llm_settings():
                        return
                    llm_cfg = load_llm_cfg(self.settings)
                if r != QMessageBox.Yes or llm_cfg_problem(llm_cfg):
                    llm_cfg = None
        self.run_task(core.recognize, self.path, force_ocr=self.cb_ocr.isChecked(), toc_range=rng,
                      password=self.password, llm_cfg=llm_cfg, on_done=self._recognized,
                      text="正在用大模型识别目录…" if llm_cfg else "正在识别目录…")

    def _set_toc_range(self, a, b):
        """程序回填识别到的目录页范围，不影响“自动识别”的勾选状态。"""
        for sp, v in ((self.sp_toc_a, a), (self.sp_toc_b, b)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    def _toc_auto_toggled(self, on):
        for sp in (self.sp_toc_a, self.sp_toc_b):
            sp.setEnabled(not on)
        if not on:
            self.sp_toc_a.setFocus()
            self.sp_toc_a.selectAll()

    def _engine_changed(self):
        self.settings.setValue("ocr/engine", self.cb_engine.currentData())
        self._update_engine_label()

    def _update_engine_label(self):
        if self.cb_engine.currentData() != "llm":
            self.lbl_engine.setText("使用本机 OCR / 文字层解析目录，无需联网。")
            return
        cfg = load_llm_cfg(self.settings)
        name = llm.PROVIDER_MAP[cfg["provider"]][1]
        why = llm_cfg_problem(cfg)
        self.lbl_engine.setText(f"{name} · {cfg['model'] or '未设置模型'}" + (f"（{why}）" if why else ""))

    def edit_llm_settings(self):
        dlg = LLMSettingsDialog(self.settings, self)
        if dlg.exec() != QDialog.Accepted:
            return False
        self._update_engine_label()
        self.status("已保存大模型接口设置")
        return True

    def _recognized(self, res: core.Result):
        self.mode = res.mode
        self.toc_pages = list(res.toc_pages)
        if not res.entries:
            msg = " ".join(res.messages) or "没有识别到目录。"
            self.status(msg)
            self.info.setText(f"{os.path.basename(self.path)} · {len(self.doc)} 页 · 未识别到目录")
            QMessageBox.information(self, APP_NAME, msg + "\n\n也可以手动添加：点“＋ 新增”，翻到对应页后点“定位到当前页”。")
            return
        rows = []
        for e in res.entries:
            rows.append((e.level, e.title, dict(book=e.book, delta=e.delta, abs=e.abs_page, status=e.status)))
        self._updating = True
        self.sp_offset.setValue(res.offset)
        self._updating = False
        self._load_rows(rows)
        self._commit(reset=True)
        mode = {"text": "文字版", "ocr": "扫描版（OCR）", "mixed": "文字与扫描混合"}[res.mode]
        tp = res.toc_pages
        pages = f"第 {tp[0] + 1} 页" if len(tp) == 1 else f"第 {tp[0] + 1}–{tp[-1] + 1} 页"
        self.info.setText(f"{os.path.basename(self.path)} · {len(self.doc)} 页 · {mode} · 目录在 {pages}"
                          f" · 偏移 {res.offset:+d}（依据{res.offset_method}）")
        self._set_toc_range(tp[0] + 1, tp[-1] + 1)
        extra = " ".join(res.messages)
        tip = "" if res.mode == "text" else "  扫描版可在“更多”里选择“核对页码”逐条确认。"
        self.status(f"识别到 {len(res.entries)} 条目录。{extra}{tip}")
        first = self.tree.topLevelItem(0)
        if first:
            self.tree.setCurrentItem(first)

    def start_verify(self):
        if not self.path or self.tree.topLevelItemCount() == 0:
            return
        items = list(self._iter_items())
        entries = []
        for it in items:
            d = it.data(0, ROLE)
            entries.append(core.Entry(it.text(C_TITLE), book=d["book"], delta=d["delta"],
                                      abs_page=d["abs"], status=d["status"]))
        if self.mode != "text":
            r = QMessageBox.question(self, APP_NAME, "扫描版需要对每个目标页做 OCR，可能要花几分钟。继续吗？")
            if r != QMessageBox.Yes:
                return

        def done(res):
            ok, fixed = res
            self._updating = True
            for it, e in zip(items, entries):
                d = dict(it.data(0, ROLE))
                d["delta"], d["status"] = e.delta, e.status
                it.setData(0, ROLE, d)
                self._refresh_item(it)
            self._updating = False
            self._commit()
            self.status(f"核对完成：{ok} 条位置正确，{fixed} 条已自动修正，"
                        f"{len(entries) - ok - fixed} 条没找到标题（请人工确认）。")
            self._preview_item(self.tree.currentItem())

        self.run_task(core.run_verify, self.path, entries, self.sp_offset.value(),
                      force_ocr=self.cb_ocr.isChecked(), password=self.password,
                      toc_pages=self.toc_pages, on_done=done, text="正在核对页码…")

    def load_existing(self):
        if self.doc is None:
            return
        toc = core.existing_toc(self.doc)
        if not toc:
            self.status("这个 PDF 没有自带书签。")
            return
        rows = [(lv, title, dict(book=None, delta=0, abs=pg if pg > 0 else None, status=""))
                for lv, title, pg in toc]
        self._load_rows(rows)
        self._commit()
        self.info.setText(f"{os.path.basename(self.path)} · {len(self.doc)} 页 · 已载入自带书签")
        self.status(f"已载入 {len(rows)} 条现有书签。")

    # ---------------------------------------------------------------- 树数据
    def _iter_items(self, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for i in range(parent.childCount()):
            it = parent.child(i)
            yield it
            yield from self._iter_items(it)

    def _depth(self, it):
        d = 1
        p = it.parent()
        while p is not None:
            d += 1
            p = p.parent()
        return d

    def _rows(self):
        rows = []
        for it in self._iter_items():
            rows.append((self._depth(it), it.text(C_TITLE), dict(it.data(0, ROLE)), it.isExpanded()))
        return rows

    def _make_item(self, title, data):
        it = QTreeWidgetItem([title, "", "", ""])
        it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        for c in (C_BOOK, C_PDF, C_STATE):
            it.setTextAlignment(c, Qt.AlignCenter)
        it.setData(0, ROLE, data)
        return it

    def _load_rows(self, rows):
        self._updating = True
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        stack = []  # (level, item)
        expanded = []
        for row in rows:
            lv, title, data = row[0], row[1], row[2]
            exp = row[3] if len(row) > 3 else None
            while stack and stack[-1][0] >= lv:
                stack.pop()
            it = self._make_item(title, data)
            if stack:
                stack[-1][1].addChild(it)
                lv = stack[-1][0] + 1
            else:
                self.tree.addTopLevelItem(it)
                lv = 1
            stack.append((lv, it))
            self._refresh_item(it)
            expanded.append((it, exp))
        for it, exp in expanded:
            if exp is None:
                it.setExpanded(self._depth(it) < 2)
            else:
                it.setExpanded(exp)
        self.tree.setUpdatesEnabled(True)
        self._updating = False
        self._update_count()

    def _pdf_page(self, d):
        if d.get("abs") is not None:
            return d["abs"]
        if d.get("book") is None:
            return None
        return d["book"] + self.sp_offset.value() + d.get("delta", 0)

    def _refresh_item(self, it):
        was = self._updating
        self._updating = True
        d = it.data(0, ROLE)
        it.setText(C_BOOK, "" if d["book"] is None else str(d["book"]))
        pg = self._pdf_page(d)
        it.setText(C_PDF, "" if pg is None else str(pg))
        n = len(self.doc) if self.doc is not None else 0
        bad = pg is None or pg < 1 or pg > n
        it.setForeground(C_PDF, QColor("#dc2626") if bad else QColor("#111827"))
        txt, color = STATUS_STYLE.get(d.get("status", ""), STATUS_STYLE[""])
        it.setText(C_STATE, txt)
        it.setForeground(C_STATE, QColor(color))
        self._updating = was

    def _refresh_all(self):
        self.tree.setUpdatesEnabled(False)
        for it in self._iter_items():
            self._refresh_item(it)
        self.tree.setUpdatesEnabled(True)

    def _update_count(self):
        n = sum(1 for _ in self._iter_items())
        self.lbl_count.setText(f"书签  {n}" if n else "书签")

    def _commit(self, reset=False):
        """记录一次修改，供撤销使用。"""
        snap = self._rows()
        if reset:
            self.undo_stack.clear()
        elif snap != self._last_snap:
            self.undo_stack.append(self._last_snap)
            del self.undo_stack[:-80]
        self.redo_stack.clear()
        self._last_snap = snap
        self._update_count()

    def undo(self):
        if not self.undo_stack or self.thread is not None:
            return
        self.redo_stack.append(self._last_snap)
        snap = self.undo_stack.pop()
        self._load_rows(snap)
        self._last_snap = snap
        self.status("已撤销")

    def redo(self):
        if not self.redo_stack or self.thread is not None:
            return
        self.undo_stack.append(self._last_snap)
        snap = self.redo_stack.pop()
        self._load_rows(snap)
        self._last_snap = snap
        self.status("已重做")

    # ---------------------------------------------------------------- 编辑
    def _item_changed(self, it, col):
        if self._updating:
            return
        d = dict(it.data(0, ROLE))
        if col == C_TITLE:
            t = it.text(C_TITLE).strip()
            if not t:
                self._updating = True
                it.setText(C_TITLE, "（无标题）")
                self._updating = False
        elif col == C_BOOK:
            t = it.text(C_BOOK).strip()
            d["book"] = int(t) if re.fullmatch(r"-?\d+", t) else None
            d["delta"], d["abs"], d["status"] = 0, None, "manual"
        elif col == C_PDF:
            t = it.text(C_PDF).strip()
            if re.fullmatch(r"-?\d+", t):
                v = int(t)
                if d["book"] is not None:
                    d["delta"] = v - (d["book"] + self.sp_offset.value())
                    d["abs"] = None
                else:
                    d["abs"] = v
                d["status"] = "manual"
        else:
            return
        self._updating = True
        it.setData(0, ROLE, d)
        self._updating = False
        self._refresh_item(it)
        self._commit()
        if col in (C_BOOK, C_PDF):
            self._preview_item(it)

    def _offset_changed(self, v):
        if self._updating:
            return
        self._refresh_all()
        self._preview_item(self.tree.currentItem())

    def _after_drop(self):
        self._commit()
        self.status("已调整顺序")

    def _selected(self):
        items = self.tree.selectedItems()
        if not items and self.tree.currentItem() is not None:
            items = [self.tree.currentItem()]
        return items

    def add_item(self):
        if self.doc is None or self.thread is not None:
            return
        cur = self.tree.currentItem()
        data = dict(book=None, delta=0, abs=self.view.pno + 1, status="manual")
        if cur is not None:
            cd = cur.data(0, ROLE)
            if cd["book"] is not None:
                data = dict(book=cd["book"], delta=cd["delta"], abs=None, status="manual")
        it = self._make_item("新书签", data)
        if cur is None:
            self.tree.addTopLevelItem(it)
        else:
            parent = cur.parent() or self.tree.invisibleRootItem()
            parent.insertChild(parent.indexOfChild(cur) + 1, it)
        self._refresh_item(it)
        self._commit()
        self.tree.setCurrentItem(it)
        self.tree.editItem(it, C_TITLE)

    def delete_items(self):
        items = self._selected()
        if not items or self.thread is not None:
            return
        sel = set(id(i) for i in items)

        def ancestor_selected(it):
            p = it.parent()
            while p is not None:
                if id(p) in sel:
                    return True
                p = p.parent()
            return False

        nxt = None
        for it in items:
            if ancestor_selected(it):
                continue
            parent = it.parent() or self.tree.invisibleRootItem()
            idx = parent.indexOfChild(it)
            parent.takeChild(idx)
            if parent.childCount():
                nxt = parent.child(min(idx, parent.childCount() - 1))
            elif parent is not self.tree.invisibleRootItem():
                nxt = parent
        self._commit()
        if nxt is not None:
            self.tree.setCurrentItem(nxt)
        self.status(f"已删除 {len(items)} 条（Ctrl+Z 可撤销）")

    def move_item(self, step):
        it = self.tree.currentItem()
        if it is None or self.thread is not None:
            return
        parent = it.parent() or self.tree.invisibleRootItem()
        i = parent.indexOfChild(it)
        j = i + step
        if not (0 <= j < parent.childCount()):
            return
        exp = it.isExpanded()
        parent.takeChild(i)
        parent.insertChild(j, it)
        it.setExpanded(exp)
        self.tree.setCurrentItem(it)
        self._commit()

    def indent(self):
        self._shift_level(1)

    def outdent(self):
        self._shift_level(-1)

    def _shift_level(self, step):
        """把选中的书签（连同其子项）整体升级(-1)或降级(+1)，支持多选。

        在“扁平的 (层级, 标题, 数据)”序列上调整层级再重建树，阅读顺序保持不变：
        降级时成为上一条的子项；升级时其后的兄弟条目变成它的子项。
        """
        items = self._selected()
        if not items or self.thread is not None:
            return
        sel = set(id(i) for i in items)
        cur = self.tree.currentItem()
        all_items = list(self._iter_items())
        rows = self._rows()

        new_levels = []
        shifted = set()          # 成功调整的块首（选中且无选中祖先的条目）
        moved = [False] * len(all_items)
        block_depth = None       # 当前正在处理的块首原始层级
        block_shift = 0
        for k, it in enumerate(all_items):
            lv = rows[k][0]
            if block_depth is not None and lv > block_depth:
                # 位于某个选中块内部，跟随块首一起移动
                new_levels.append(lv + block_shift)
                moved[k] = block_shift != 0
                continue
            block_depth = None
            if id(it) in sel:
                prev_lv = new_levels[-1] if new_levels else 0
                ok = lv > 1 if step < 0 else lv <= prev_lv
                block_depth, block_shift = lv, (step if ok else 0)
                if ok:
                    shifted.add(id(it))
                new_levels.append(lv + block_shift)
                moved[k] = ok
            else:
                new_levels.append(lv)

        if not shifted:
            return
        sel_idx = [k for k, it in enumerate(all_items) if id(it) in sel]
        cur_idx = all_items.index(cur) if cur in all_items else None
        self._load_rows([(new_levels[k],) + tuple(r[1:]) for k, r in enumerate(rows)])

        new_items = list(self._iter_items())
        self.tree.clearSelection()
        for k in sel_idx:
            it = new_items[k]
            it.setSelected(True)
            p = it.parent()
            while p is not None:
                p.setExpanded(True)
                p = p.parent()
            if step < 0 and it.childCount() and moved[k]:
                it.setExpanded(True)
        if cur_idx is not None:
            self.tree.setCurrentItem(new_items[cur_idx], 0, QItemSelectionModel.NoUpdate)
        self._commit()
        word = "升级" if step < 0 else "降级"
        skipped = len([1 for k in sel_idx if not moved[k]])
        msg = f"已{word} {len(shifted)} 条"
        if skipped and len(sel_idx) > 1:
            msg += f"（{skipped} 条无法{word}，已跳过）"
        self.status(msg)

    def set_to_current_page(self):
        items = self._selected()
        if not items or self.doc is None or self.thread is not None:
            return
        pg = self.view.pno + 1
        for it in items:
            d = dict(it.data(0, ROLE))
            if d["book"] is not None:
                d["delta"] = pg - (d["book"] + self.sp_offset.value())
                d["abs"] = None
            else:
                d["abs"] = pg
            d["status"] = "manual"
            self._updating = True
            it.setData(0, ROLE, d)
            self._updating = False
            self._refresh_item(it)
        self._commit()
        self.status(f"已把 {len(items)} 条书签定位到第 {pg} 页")

    def collapse_to_top(self):
        for it in self._iter_items():
            it.setExpanded(False)

    def clear_all(self):
        if self.tree.topLevelItemCount() == 0:
            return
        if QMessageBox.question(self, APP_NAME, "清空全部书签？（可用 Ctrl+Z 撤销）") == QMessageBox.Yes:
            self.tree.clear()
            self._commit()

    def edit_as_text(self):
        if self.doc is None or self.thread is not None:
            return
        lines = []
        old = {}
        for lv, title, d, _ in self._rows():
            if d["book"] is not None:
                tail = str(d["book"])
            elif d["abs"] is not None:
                tail = f"@{d['abs']}"
            else:
                tail = ""
            lines.append("\t" * (lv - 1) + title + ("\t" + tail if tail else ""))
            old[(title, d["book"])] = d
        dlg = TextEditDialog("\n".join(lines), self)
        if dlg.exec() != QDialog.Accepted:
            return
        rows, prev = [], 0
        for raw in dlg.text().splitlines():
            if not raw.strip():
                continue
            m = re.match(r"^([\t ]*)", raw)
            ind = m.group(1).replace("    ", "\t")
            lv = ind.count("\t") + ind.count(" ") // 2 + 1
            lv = max(1, min(lv, prev + 1))
            prev = lv
            body = raw.strip()
            mm = re.match(r"^(.*?)\s+(@?)(-?\d+)\s*$", body)
            book = absp = None
            if mm:
                title = mm.group(1).strip()
                if mm.group(2):
                    absp = int(mm.group(3))
                else:
                    book = int(mm.group(3))
            else:
                title = body
            od = old.get((title, book))
            if od is not None and absp is None:
                data = dict(od)
            else:
                data = dict(book=book, delta=0, abs=absp, status="manual" if (book or absp) else "")
            rows.append((lv, title, data))
        # 没写页码的条目沿用下一条的页码
        nxt = None
        for i in range(len(rows) - 1, -1, -1):
            d = rows[i][2]
            if d["book"] is None and d["abs"] is None:
                if nxt is not None:
                    d.update(nxt)
                    d["status"] = "guess"
            else:
                nxt = dict(book=d["book"], delta=d["delta"], abs=d["abs"])
        self._load_rows(rows)
        self._commit()
        self.status(f"已应用文本编辑，共 {len(rows)} 条")

    # ---------------------------------------------------------------- 预览
    def _preview_item(self, it):
        if it is None or self.doc is None:
            return
        d = it.data(0, ROLE)
        pg = self._pdf_page(d)
        if pg is None:
            self.lbl_target.setText(f"“{it.text(C_TITLE)}” 没有页码")
            return
        if not (1 <= pg <= len(self.doc)):
            self.lbl_target.setText(f"“{it.text(C_TITLE)}” 指向第 {pg} 页，超出范围")
            return
        self.view.show_page(pg - 1, highlight=it.text(C_TITLE))

    def _page_changed(self, pno):
        if self.sp_page.value() != pno + 1:
            self.sp_page.blockSignals(True)
            self.sp_page.setValue(pno + 1)
            self.sp_page.blockSignals(False)
        book = pno + 1 - self.sp_offset.value()
        cur = self.tree.currentItem()
        hint = f"书中第 {book} 页" if book >= 1 else "正文之前"
        if cur is not None and self._pdf_page(cur.data(0, ROLE)) == pno + 1:
            self.lbl_target.setText(f"{hint} · {cur.text(C_TITLE)}")
        else:
            self.lbl_target.setText(hint)

    # ---------------------------------------------------------------- 导出
    def _collect_toc(self):
        toc, missing = [], []
        for lv, title, d, _ in self._rows():
            pg = self._pdf_page(d)
            if pg is None:
                missing.append(title)
                continue
            toc.append([lv, title, pg])
        return toc, missing

    def _check_before_export(self):
        if self.doc is None:
            return None
        if self.tree.topLevelItemCount() == 0:
            QMessageBox.information(self, APP_NAME, "还没有书签可以导出。")
            return None
        toc, missing = self._collect_toc()
        n = len(self.doc)
        bad = [t for _, t, p in toc if p < 1 or p > n]
        if missing or bad:
            msg = []
            if missing:
                msg.append(f"{len(missing)} 条没有页码，将被跳过")
            if bad:
                msg.append(f"{len(bad)} 条页码超出 1–{n} 的范围，将被限制到首页或末页")
            r = QMessageBox.question(self, APP_NAME, "；".join(msg) + "。\n仍然继续吗？")
            if r != QMessageBox.Yes:
                return None
        return toc

    def preview_in_reader(self):
        toc = self._check_before_export()
        if toc is None:
            return
        tmp = os.path.join(tempfile.gettempdir(), "书签预览_" + os.path.basename(self.path))
        self.run_task(core.export_pdf, self.path, tmp, toc, password=self.password,
                      on_done=lambda p: (self.status("已在阅读器中打开预览文件（临时文件）"), open_with_system(p)),
                      text="正在生成预览文件…")

    def export(self):
        if self.thread is not None:
            return
        toc = self._check_before_export()
        if toc is None:
            return
        base, _ = os.path.splitext(os.path.basename(self.path))
        d = self.settings.value("savedir", os.path.dirname(self.path))
        dst, _ = QFileDialog.getSaveFileName(self, "导出带书签的 PDF", os.path.join(d, base + "_书签.pdf"),
                                             "PDF 文件 (*.pdf)")
        if not dst:
            return
        if not dst.lower().endswith(".pdf"):
            dst += ".pdf"
        self.settings.setValue("savedir", os.path.dirname(dst))

        def done(p):
            self.status(f"已导出：{p}")
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText(f"已导出 {len(toc)} 条书签。")
            box.setInformativeText(p)
            b_open = box.addButton("打开文件", QMessageBox.AcceptRole)
            b_dir = box.addButton("打开所在文件夹", QMessageBox.ActionRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_open:
                open_with_system(p)
            elif box.clickedButton() is b_dir:
                reveal_in_folder(p)

        same = os.path.abspath(dst) == os.path.abspath(self.path)
        if same:
            # 覆盖原文件前先释放预览占用
            with core.FITZ_LOCK:
                self.doc.close()
            self.view.clear()

            def done_same(p):
                self.doc = core.open_doc(p, self.password)
                self.view.set_document(self.doc)
                self._preview_item(self.tree.currentItem())
                done(p)
            self.run_task(core.export_pdf, self.path, dst, toc, password=self.password,
                          on_done=done_same, text="正在导出…")
        else:
            self.run_task(core.export_pdf, self.path, dst, toc, password=self.password,
                          on_done=done, text="正在导出…")

    # ---------------------------------------------------------------- 拖放 / 关闭
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop.set_hover(True)

    def dragLeaveEvent(self, e):
        self.drop.set_hover(False)

    def dropEvent(self, e):
        self.drop.set_hover(False)
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self.open_pdf(p)
                break

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.cancel.set()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.split.saveState())
        super().closeEvent(e)


def close_splash():
    """单文件版 exe 启动时会先显示启动画面，主窗口出现后关闭它。"""
    if "_PYI_SPLASH_IPC" not in os.environ:   # 没有启动画面（源码运行或未启用）
        return
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def main():
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC"])
    f.setPointSizeF(9.5 if sys.platform.startswith("win") else 10)
    app.setFont(f)
    app.setStyleSheet(QSS)
    app.setWindowIcon(make_app_icon())

    def excepthook(t, v, tb):
        traceback.print_exception(t, v, tb)
        QMessageBox.critical(None, APP_NAME, "".join(traceback.format_exception_only(t, v)))
    sys.excepthook = excepthook

    w = MainWindow()
    w.show()
    close_splash()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        QTimer.singleShot(100, lambda: w.open_pdf(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
