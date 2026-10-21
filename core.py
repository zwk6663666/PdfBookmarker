"""PDF 目录识别与书签写入的核心逻辑（与界面无关，可单独调用）。

处理流程
1. 逐页读取文字。页面有可用文字层就直接读取，否则用 RapidOCR 识别。
2. 在书的前部（找不到时再看末尾）定位目录页。
3. 把目录行解析成条目：标题、书中印刷页码、层级。
4. 推算页码偏移，也就是 PDF 实际页码与书中印刷页码的差值。
5. 逐条到正文中核对标题，对个别错位的条目单独修正。
"""
from __future__ import annotations

import re
import threading
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
# PyMuPDF 2.0 起会自动加载版面分析插件（pymupdf.layout），本程序用不到，
# 加载它既拖慢启动，打包成 exe 时还会因缺少模型文件而报错，这里直接屏蔽
import sys as _sys
_sys.modules.setdefault("pymupdf.layout", None)
import pymupdf

import llm

# MuPDF 不是线程安全的，所有 pymupdf 调用都要经过这把锁
FITZ_LOCK = threading.RLock()


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class Frag:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float = 1.0

    @property
    def h(self):
        return max(1e-3, self.y1 - self.y0)

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


@dataclass
class Line:
    page: int
    frags: list = field(default_factory=list)
    text: str = ""

    @property
    def x0(self):
        return self.frags[0].x0

    @property
    def x1(self):
        return self.frags[-1].x1

    @property
    def y0(self):
        return statistics.median(f.y0 for f in self.frags)

    @property
    def y1(self):
        return statistics.median(f.y1 for f in self.frags)

    @property
    def h(self):
        return statistics.median(f.h for f in self.frags)


@dataclass
class Entry:
    title: str
    level: int = 1
    book: Optional[int] = None      # 书中印刷页码
    delta: int = 0                  # 相对全局偏移的单条修正
    abs_page: Optional[int] = None  # 直接指定的 PDF 页码（从 1 开始）
    status: str = ""                # ok / fixed / guess / manual / ""
    # 以下仅供识别过程使用
    x0: float = 0.0
    src_page: int = -1
    rank: Optional[int] = None
    roman: bool = False

    def pdf_page(self, offset: int) -> Optional[int]:
        if self.abs_page is not None:
            return self.abs_page
        if self.book is None:
            return None
        return self.book + offset + self.delta


@dataclass
class Result:
    entries: list
    offset: int
    toc_pages: list
    mode: str                 # text / ocr / mixed
    offset_method: str
    messages: list = field(default_factory=list)


# ----------------------------------------------------------------------------
# OCR 引擎（懒加载，兼容 rapidocr_onnxruntime 1.2 ~ 1.4）
# ----------------------------------------------------------------------------
class OCREngine:
    _inst = None
    _inst_lock = threading.Lock()

    @classmethod
    def get(cls) -> "OCREngine":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "没有安装 OCR 组件，无法识别扫描版 PDF。\n"
                "请运行: pip install rapidocr_onnxruntime") from e
        self.eng = RapidOCR()
        self.rec_model = getattr(self.eng, "text_rec", None) or getattr(
            self.eng, "text_recognizer", None)
        self.run_lock = threading.Lock()

    def detect(self, img: np.ndarray):
        """返回 [(x0, y0, x1, y1, text, score)]，坐标为图像像素。"""
        with self.run_lock:
            res = self.eng(img)
        if isinstance(res, tuple):
            res = res[0]
        out = []
        for r in res or []:
            try:
                box, txt, sc = r[0], str(r[1]), float(r[2])
            except Exception:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((min(xs), min(ys), max(xs), max(ys), txt, sc))
        return out

    def boxes_only(self, img: np.ndarray):
        """只做文字检测，返回 [(x0, y0, x1, y1)]。比完整 OCR 快很多，用于筛选目录页。"""
        det = getattr(self.eng, "text_det", None) or getattr(self.eng, "text_detector", None)
        if det is None:
            return [(b[0], b[1], b[2], b[3]) for b in self.detect(img)]
        with self.run_lock:
            r = det(img)
        boxes = r[0] if isinstance(r, tuple) else r
        if boxes is None:
            return []
        out = []
        for b in np.asarray(boxes):
            out.append((float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())))
        return out

    def recognize(self, imgs: list) -> list:
        """只做文字识别（不做检测），返回 [(text, score)]。"""
        if not imgs:
            return []
        if self.rec_model is not None:
            try:
                with self.run_lock:
                    res = self.rec_model(imgs)
                if isinstance(res, tuple):
                    res = res[0]
                return [(str(r[0]), float(r[1])) for r in res]
            except Exception:
                pass
        out = []
        for im in imgs:
            try:
                with self.run_lock:
                    r = self.eng(im, use_det=False, use_cls=False, use_rec=True)
                if isinstance(r, tuple):
                    r = r[0]
                txt = _first_str(r) or ""
                out.append((txt, 0.5))
            except Exception:
                out.append(("", 0.0))
        return out


def _first_str(obj):
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        for o in obj:
            s = _first_str(o)
            if s:
                return s
    return None


def render_np(page, zoom: float, clip=None) -> np.ndarray:
    with FITZ_LOCK:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip,
                              colorspace=pymupdf.csRGB, alpha=False)
        buf = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.stride)
        arr = buf[:, : pix.w * 3].reshape(pix.h, pix.w, 3).copy()
    return arr


# ----------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------
LEADER_CLASS = r"\.·•…‥⋯_\-—–─,，。．:："
LEADER_SET = set(" .·•…‥⋯_-—–─,，。．:：　")
FULLWIDTH = str.maketrans("０１２３４５６７８９．", "0123456789.")

TAIL_NUM = re.compile(rf"^(?P<title>.*?\S)(?P<sep>[\s{LEADER_CLASS}]+)(?P<num>\d{{1,4}})$")
TAIL_ROMAN = re.compile(
    rf"^(?P<title>.*?\S)(?P<sep>\s*[{LEADER_CLASS}]{{2,}}\s*|\s{{2,}})(?P<num>[ivxlcdmIVXLCDM]{{1,7}})$")
TOC_HEADINGS = {"目录", "目次", "目錄", "contents", "tableofcontents", "content", "目录contents"}

CN = r"[一二三四五六七八九十百千零〇两\d]+"
RANK_RULES = [
    (re.compile(rf"^第\s*{CN}\s*[部篇编卷]"), 0),
    (re.compile(r"^(?:part|PART|Part)\s+[\dIVXivx]+"), 0),
    (re.compile(rf"^第\s*{CN}\s*章"), 10),
    (re.compile(r"^(?:chapter|CHAPTER|Chapter)\s*\d+"), 10),
    (re.compile(r"^(?:附录|附錄|Appendix|APPENDIX)\s*[A-Z\d一二三四五六七八九十]*"), 10),
    (re.compile(rf"^第\s*{CN}\s*节"), 20),
    (re.compile(r"^§\s*\d+"), 20),
    (re.compile(r"^[一二三四五六七八九十]+\s*[、\.．]"), 20),
    (re.compile(r"^[（(]\s*[一二三四五六七八九十]+\s*[)）]"), 30),
    (re.compile(r"^\d+\s*[、]"), 10),
    (re.compile(r"^\d+\s+\D"), 10),
]
DOTTED = re.compile(r"^(\d+)((?:\.\d+)+)(?=\s|[^\d.]|$)")
TOP_KEYWORDS = re.compile(
    r"^(前言|序言?|自序|再版序|绪论|引言|导论|目录|参考文献|主要参考文献|索引|名词索引|后记|跋|致谢|附录|"
    r"习题答案|部分习题答案|符号说明|符号表|preface|foreword|introduction|contents|index|"
    r"bibliography|references|appendix|acknowledg|epilogue|glossary)",
    re.I)


def norm_key(s: str) -> str:
    """用于比对的归一化：只保留字母、数字、汉字，并统一大小写。"""
    s = s.translate(FULLWIDTH)
    return "".join(c for c in s if c.isalnum()).lower()


def clean_title(t: str) -> str:
    t = t.translate(FULLWIDTH)
    t = t.strip().rstrip("".join(LEADER_SET)).strip()
    t = re.sub(r"\s+", " ", t)
    # 编号与标题之间统一留一个空格：“1.1.3有限” → “1.1.3 有限”
    t = re.sub(r"^(\d+(?:\.\d+)+)[\s\.、，,:：·]*(?=[^\d.\s])", r"\1 ", t)
    t = re.sub(rf"^(第\s*{CN}\s*[章节部篇编卷])\s*", r"\1 ", t)
    # OCR 常把半角括号认成全角，括号不成对时统一成半角
    if t.count("（") != t.count("）"):
        t = t.replace("（", "(").replace("）", ")")
    return t.strip()


def has_word(s: str) -> bool:
    return re.search(r"[A-Za-z一-鿿]", s) is not None


def roman_to_int(s: str) -> Optional[int]:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    if not s or any(c not in vals for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = vals[c]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def parse_tail(text: str):
    """拆出行尾页码。返回 (title, page, is_roman, has_leader) 或 None。"""
    t = text.translate(FULLWIDTH).strip()
    m = TAIL_NUM.match(t)
    roman = False
    if not m:
        m = TAIL_ROMAN.match(t)
        roman = m is not None
    if not m:
        return None
    title = clean_title(m.group("title"))
    if not title or not has_word(title):
        return None
    num = roman_to_int(m.group("num")) if roman else int(m.group("num"))
    if num is None:
        return None
    sep = m.group("sep")
    leader = sum(1 for c in sep if c in "．.·•…‥⋯_") >= 2
    return title, num, roman, leader


def numbering_rank(title: str) -> Optional[int]:
    t = title.strip()
    m = DOTTED.match(t)
    if m:
        return 10 + 10 * m.group(2).count(".")
    for rx, rank in RANK_RULES:
        if rx.match(t):
            return rank
    return None


def _visible_chars(text: str) -> int:
    return sum(1 for c in text if not c.isspace() and c not in LEADER_SET)


def text_layer_ok(text: str) -> bool:
    """文字层是否可用。乱码通常表现为大量替换符或私用区字符。"""
    chars = [c for c in text if not c.isspace() and c not in LEADER_SET]
    if len(chars) < 12:
        return False
    bad = sum(1 for c in chars if ord(c) == 0xFFFD or ord(c) < 32)
    # 数学公式的大括号等会落在私用区，所以只有占比很高时才算乱码
    pua = sum(1 for c in chars if 0xE000 <= ord(c) <= 0xF8FF)
    good = sum(1 for c in chars if c.isalnum())
    n = len(chars)
    return bad / n < 0.05 and pua / n < 0.3 and good / n > 0.3


# ----------------------------------------------------------------------------
# 逐页读取（文字层 / OCR 统一成 Frag）
# ----------------------------------------------------------------------------
class PageReader:
    def __init__(self, doc, force_ocr=False, zoom=2.0, cancel=None):
        self.doc = doc
        self.force_ocr = force_ocr
        self.zoom = zoom
        self.cancel = cancel
        self._frags = {}
        self._mode = {}
        self._lines = {}
        self._norm = {}

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def page_mode(self, pno):
        if pno not in self._mode:
            if self.force_ocr:
                self._mode[pno] = "ocr"
            else:
                with FITZ_LOCK:
                    page = self.doc[pno]
                    raw = page.get_text("text")
                    ok = text_layer_ok(raw)
                    if not ok and _visible_chars(raw) < 12:
                        # 几乎没有文字：有图片才可能是扫描页，否则就是空白页
                        ok = not page.get_images(full=False)
                self._mode[pno] = "text" if ok else "ocr"
        return self._mode[pno]

    def frags(self, pno):
        if pno in self._frags:
            return self._frags[pno]
        self.check()
        page = self.doc[pno]
        out = []
        if self.page_mode(pno) == "text":
            with FITZ_LOCK:
                d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
            for b in d["blocks"]:
                for ln in b.get("lines", []):
                    for s in ln["spans"]:
                        if s["text"].strip():
                            x0, y0, x1, y1 = s["bbox"]
                            out.append(Frag(x0, y0, x1, y1, s["text"]))
        else:
            img = render_np(page, self.zoom)
            self.check()
            for x0, y0, x1, y1, txt, sc in OCREngine.get().detect(img):
                if sc < 0.3 or not txt.strip():
                    continue
                z = self.zoom
                out.append(Frag(x0 / z, y0 / z, x1 / z, y1 / z, txt, sc))
        self._frags[pno] = out
        return out

    def right_column_score(self, pno):
        """扫描页的快速判断：右侧对齐的短文字框（页码列）有多少个。"""
        img = render_np(self.doc[pno], self.zoom)
        self.check()
        bx = OCREngine.get().boxes_only(img)
        if not bx:
            return 0
        W = img.shape[1]
        hm = statistics.median(y1 - y0 for _, y0, _, y1 in bx)
        rights = sorted(x1 for x0, y0, x1, y1 in bx if (x1 - x0) < 3 * (y1 - y0) and x0 > 0.6 * W)
        best, j = 0, 0
        for i in range(len(rights)):
            while j < len(rights) and rights[j] - rights[i] <= hm:
                j += 1
            best = max(best, j - i)
        return best

    def lines(self, pno):
        if pno not in self._lines:
            self._lines[pno] = group_lines(self.frags(pno), pno)
        return self._lines[pno]

    def norm_text(self, pno):
        if pno not in self._norm:
            self._norm[pno] = norm_key("".join(f.text for f in self.frags(pno)))
        return self._norm[pno]


def group_lines(frags, pno):
    lines = []
    for f in sorted(frags, key=lambda f: f.cy):
        best, best_ov = None, 0.0
        for ln in lines[-4:]:
            ly0, ly1 = ln.y0, ln.y1
            ov = min(ly1, f.y1) - max(ly0, f.y0)
            r = ov / max(1e-3, min(f.h, ly1 - ly0))
            if r > best_ov:
                best, best_ov = ln, r
        if best is not None and best_ov > 0.45:
            best.frags.append(f)
        else:
            lines.append(Line(pno, [f]))
    for ln in lines:
        ln.frags.sort(key=lambda f: f.x0)
        h = ln.h
        parts = [ln.frags[0].text]
        for a, b in zip(ln.frags, ln.frags[1:]):
            gap = b.x0 - a.x1
            # 大间距用两个空格标记，便于区分“标题  页码”
            sep = "  " if gap > 1.5 * h else (" " if gap > 0.25 * h else "")
            parts.append(sep + b.text)
        txt = re.sub(r"[ \t\u3000]{3,}", "  ", "".join(parts))
        ln.text = re.sub(r"(?<! ) (?! )|[\r\n\t]+", " ", txt).strip()
    return lines


# ----------------------------------------------------------------------------
# 目录页判断
# ----------------------------------------------------------------------------
def is_heading(text: str) -> bool:
    k = norm_key(text)
    return k in TOC_HEADINGS or k.startswith("contents") or k.startswith("tableofcontents")


def page_stats(lines, page_h):
    body = [l for l in lines if 0.04 * page_h < l.y0 < 0.96 * page_h]
    parsed = [parse_tail(l.text) for l in body]
    nums = [p[1] for p in parsed if p and not p[2]]
    n_ent = sum(1 for p in parsed if p)
    leaders = sum(1 for p in parsed if p and p[3])
    mono = 1.0
    if len(nums) >= 2:
        ok = sum(1 for a, b in zip(nums, nums[1:]) if b >= a)
        mono = ok / (len(nums) - 1)
    heading = any(is_heading(l.text) for l in lines[:8])
    ratio = n_ent / max(1, len(body))
    return dict(n=n_ent, ratio=ratio, leaders=leaders, mono=mono,
                heading=heading, first=nums[0] if nums else None,
                last=nums[-1] if nums else None)


def looks_like_toc_start(s):
    if s["heading"] and s["n"] >= 2:
        return True
    if s["n"] >= 6 and s["ratio"] >= 0.5 and s["mono"] >= 0.8 and s["leaders"] >= 0.5 * s["n"]:
        return True
    return s["n"] >= 8 and s["ratio"] >= 0.6 and s["mono"] >= 0.85


def looks_like_toc_cont(s, prev_last):
    if s["n"] < 3 or s["ratio"] < 0.35 or s["mono"] < 0.75:
        return False
    if prev_last is not None and s["first"] is not None and s["first"] < prev_last - 3:
        return False
    return True


def find_toc_pages(doc, reader, progress, max_front=40, tail=12):
    n = len(doc)
    front = list(range(min(n, max_front)))
    back = [p for p in range(max(0, n - tail), n) if p not in front]

    empty = dict(n=0, ratio=0, leaders=0, mono=1, heading=False, first=None, last=None)

    def scan(pages, base_pct, span_pct, screen=True):
        found, prev_last = [], None
        for i, pno in enumerate(pages):
            progress(base_pct + int(span_pct * i / max(1, len(pages))),
                     f"正在查找目录页：第 {pno + 1} 页")
            if screen and reader.page_mode(pno) == "ocr" and reader.right_column_score(pno) < 3:
                s = empty
            else:
                s = page_stats(reader.lines(pno), doc[pno].rect.height)
            if not found:
                if looks_like_toc_start(s):
                    found.append(pno)
                    prev_last = s["last"]
            else:
                if pno == found[-1] + 1 and looks_like_toc_cont(s, prev_last):
                    found.append(pno)
                    prev_last = s["last"] if s["last"] is not None else prev_last
                else:
                    break
        return found

    found = scan(front, 2, 30)
    if not found and back:
        found = scan(back, 32, 6)
    if not found and any(reader.page_mode(p) == "ocr" for p in front):
        # 快速筛选没找到，退回逐页完整 OCR
        found = scan(front[:25], 38, 10, screen=False)
    return found


# ----------------------------------------------------------------------------
# OCR 精读（只对目录页做）
#   1. 每个文字框按更高分辨率重新识别一次，生僻字（如“鞅”）准确率明显提高
#   2. 小号的章节编号（如 2.1）常被检测模型漏掉，到行首左侧单独补读
#   3. 一位数页码也常被漏掉，到页码列用三种裁剪宽度补读后投票
# ----------------------------------------------------------------------------
LOOKALIKE = str.maketrans({"i": "1", "l": "1", "I": "1", "|": "1", "!": "1", "！": "1",
                           "O": "0", "o": "0", "〇": "0", "D": "0"})
NUMBERING_TOKEN = re.compile(rf"^(\d+(?:\.\d+)+|第\s*{CN}\s*[章节部篇编卷]|[一二三四五六七八九十]+、|\d+)[\s\.、，,:：]*$")


def _pad_rect(f, mx=0.15, my=0.12):
    h = f.y1 - f.y0
    return pymupdf.Rect(f.x0 - mx * h, f.y0 - my * h, f.x1 + mx * h, f.y1 + my * h)


def refine_ocr_page(doc, reader, pno, zoom=3.0):
    if reader.page_mode(pno) != "ocr":
        return
    page = doc[pno]
    eng = OCREngine.get()
    frags = reader.frags(pno)

    # 1. 高分辨率重识别：两种分辨率各读一次，分数接近时取字数多的（漏字比多字常见）
    if frags:
        cands = [[(f.text, f.score)] for f in frags]
        for z in (2.5, zoom):
            imgs = [render_np(page, z, _pad_rect(f)) for f in frags]
            for c, (txt, sc) in zip(cands, eng.recognize(imgs)):
                if txt.strip():
                    c.append((txt, sc))
        for f, c in zip(frags, cands):
            best = max(sc for _, sc in c)
            pool = [(t, sc) for t, sc in c if sc >= best - 0.08]
            t, sc = max(pool, key=lambda x: (len(norm_key(x[0])), x[1]))
            f.text, f.score = t, max(sc, f.score)
        frags[:] = [f for f in frags if f.score >= 0.4 or re.fullmatch(r"[\d.]+", f.text.strip())]
    lines = group_lines(frags, pno)

    # 2. 补读行首编号
    num_x = [l.x0 for l in lines if numbering_rank(clean_title(l.text)) is not None]
    if len(num_x) >= 3:
        cols = sorted(num_x)
        targets = []
        for l in lines:
            if numbering_rank(clean_title(l.text)) is not None or not has_word(l.text):
                continue
            left = [x for x in cols if x < l.x0 - 0.8 * l.h]
            if not left:
                continue
            x_start = max(left)
            clip = pymupdf.Rect(x_start - 0.4 * l.h, l.y0 - 0.15 * l.h, l.x0 - 0.05 * l.h, l.y1 + 0.15 * l.h)
            if clip.width > 0.6 * l.h:
                targets.append((l, clip))
        if targets:
            res = eng.recognize([render_np(page, zoom, c) for _, c in targets])
            for (l, clip), (txt, sc) in zip(targets, res):
                t = txt.strip().rstrip(".、，,:： ")
                if t and NUMBERING_TOKEN.match(t) and sc >= 0.3:
                    nf = Frag(clip.x0 + 0.4 * l.h, l.y0, clip.x1, l.y1, t, sc)
                    l.frags.insert(0, nf)
                    frags.append(nf)
                    l.text = f"{t} {l.text}"

    # 3. 补读页码
    rights = [l.x1 for l in lines if parse_tail(l.text)]
    if len(rights) >= 3:
        col_right = statistics.median(rights)
        targets = []
        for l in lines:
            if parse_tail(l.text) or is_heading(l.text) or not has_word(l.text):
                continue
            if l.x1 > col_right - 0.3 * l.h:
                continue
            targets.append(l)
        if targets:
            imgs = []
            for l in targets:
                h = l.h
                for w in (2.0, 1.6, 1.2):
                    imgs.append(render_np(page, zoom, pymupdf.Rect(
                        col_right - w * h, l.y0 - 0.15 * h, col_right + 0.6 * h, l.y1 + 0.15 * h)))
            res = eng.recognize(imgs)
            for i, l in enumerate(targets):
                votes = Counter()
                for txt, sc in res[3 * i: 3 * i + 3]:
                    t = txt.translate(FULLWIDTH).strip().translate(LOOKALIKE)
                    m = re.search(r"(\d{1,4})\s*$", t)
                    if m:
                        votes[m.group(1)] += 1
                if votes:
                    num, cnt = votes.most_common(1)[0]
                    if cnt >= 2 or len(votes) == 1:
                        nf = Frag(col_right - 0.6 * l.h, l.y0, col_right, l.y1, num, 0.5)
                        l.frags.append(nf)
                        frags.append(nf)
                        l.text = f"{l.text} {num}"
    reader._lines[pno] = lines
    reader._norm.pop(pno, None)


# ----------------------------------------------------------------------------
# 目录条目解析
# ----------------------------------------------------------------------------
def parse_entries(doc, reader, toc_pages):
    entries = []
    for pno in toc_pages:
        page_h = doc[pno].rect.height
        pending = None
        for l in reader.lines(pno):
            text = l.text
            if is_heading(text):
                continue
            in_margin = l.y1 < 0.06 * page_h or l.y0 > 0.95 * page_h
            p = parse_tail(text)
            if p is None:
                if in_margin or not has_word(text) or re.fullmatch(r"[\divxlcdmIVXLCDM\s\-—]+", text):
                    continue
                if pending is not None:
                    # 连续两行都没有页码：上一行单独成为“无页码”条目（例如篇名）
                    if numbering_rank(pending.title) is not None or numbering_rank(clean_title(text)) is not None:
                        entries.append(pending)
                        pending = Entry(clean_title(text), x0=l.x0, src_page=pno)
                    else:
                        pending.title = f"{pending.title} {clean_title(text)}".strip()
                else:
                    pending = Entry(clean_title(text), x0=l.x0, src_page=pno)
                continue
            title, num, roman, _ = p
            if pending is not None:
                if numbering_rank(title) is None and not TOP_KEYWORDS.match(title):
                    # 标题折行：把上一行接到前面
                    sep = "" if re.search(r"[一-鿿]$", pending.title) and re.match(r"[一-鿿]", title) else " "
                    title = pending.title + sep + title
                    x0 = pending.x0
                    pending = None
                    entries.append(Entry(title, book=num, roman=roman, x0=x0, src_page=pno))
                    continue
                entries.append(pending)
                pending = None
            entries.append(Entry(title, book=num, roman=roman, x0=l.x0, src_page=pno))
        if pending is not None:
            entries.append(pending)
    # 没有页码的条目，沿用后面第一个有页码条目的页码
    nxt = None
    for e in reversed(entries):
        if e.book is not None:
            nxt = e.book
        elif nxt is not None:
            e.book = nxt
            e.status = "guess"
    entries = [e for e in entries if e.book is not None or e.abs_page is not None]
    assign_levels(entries)
    return entries


def assign_levels(entries):
    if not entries:
        return
    for e in entries:
        e.rank = numbering_rank(e.title)
    numbered = [e for e in entries if e.rank is not None]
    pages = sorted({e.src_page for e in entries})

    if numbered:
        # 各页左边距可能不同（奇偶页），先求每页相对参照页的水平偏移
        by_page = {}
        for e in numbered:
            by_page.setdefault(e.src_page, {}).setdefault(e.rank, []).append(e.x0)
        ref_page = max(by_page, key=lambda p: sum(len(v) for v in by_page[p].values()))
        ref = {r: statistics.median(v) for r, v in by_page[ref_page].items()}
        shift = {}
        for p in pages:
            pm = by_page.get(p, {})
            diffs = [statistics.median(v) - ref[r] for r, v in pm.items() if r in ref]
            shift[p] = statistics.median(diffs) if diffs else 0.0
        xs = {}
        for e in numbered:
            xs.setdefault(e.rank, []).append(e.x0 - shift[e.src_page])
        ref_x = {r: statistics.median(v) for r, v in xs.items()}
        top_rank = min(ref_x)
        for e in entries:
            if e.rank is not None:
                continue
            if TOP_KEYWORDS.match(e.title):
                e.rank = top_rank
                continue
            x = e.x0 - shift.get(e.src_page, 0.0)
            e.rank = min(ref_x, key=lambda r: abs(ref_x[r] - x))
    else:
        # 完全没有编号：按缩进聚类
        xs = sorted(e.x0 for e in entries)
        clusters = []
        for x in xs:
            if clusters and x - clusters[-1][-1] <= 6:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        centers = [statistics.median(c) for c in clusters]
        for e in entries:
            e.rank = min(range(len(centers)), key=lambda i: abs(centers[i] - e.x0)) * 10

    ranks = sorted({e.rank for e in entries})
    level_of = {r: i + 1 for i, r in enumerate(ranks)}
    prev = 0
    for e in entries:
        lv = level_of[e.rank]
        lv = min(lv, prev + 1)
        e.level = max(1, lv)
        prev = e.level


# ----------------------------------------------------------------------------
# 页码偏移
# ----------------------------------------------------------------------------
PAGE_NUM_FULL = re.compile(r"^[\s\-—–·•\[\(（]*(?:第\s*)?(\d{1,4})(?:\s*页)?[\s\-—–·•\]\)）]*$")
PAGE_NUM_EDGE = re.compile(r"^(\d{1,4})\s+\D|\D\s+(\d{1,4})$")


def edge_lines(doc, reader, pno):
    """取页面最上和最下的文字行（页眉、页脚所在位置）。"""
    page = doc[pno]
    H, W = page.rect.height, page.rect.width
    if reader.page_mode(pno) == "text":
        lines = reader.lines(pno)
    else:
        frags = []
        eng = OCREngine.get()
        for clip in (pymupdf.Rect(0, 0, W, 0.14 * H), pymupdf.Rect(0, 0.86 * H, W, H)):
            z = 2.5
            img = render_np(page, z, clip)
            for x0, y0, x1, y1, txt, sc in eng.detect(img):
                if sc >= 0.5:
                    frags.append(Frag(clip.x0 + x0 / z, clip.y0 + y0 / z,
                                      clip.x0 + x1 / z, clip.y0 + y1 / z, txt))
        lines = group_lines(frags, pno)
    if not lines:
        return []
    out = []
    top, bottom = lines[0], lines[-1]
    if top.y0 < 0.2 * H:
        out.append(top)
    if bottom is not top and bottom.y1 > 0.8 * H:
        out.append(bottom)
    return out


def offset_from_page_numbers(doc, reader, start, progress, base, span):
    n = len(doc)
    if reader.page_mode(min(start, n - 1)) == "text":
        steps = [2, 4, 6, 8, 10, 12, 15, 18, 22, 26, 30, 35, 40, 50, 60, 75, 90, 110, 130, 150]
    else:
        steps = [10, 13, 17, 22, 28, 35, 45, 60]
    pages = sorted({start + k for k in steps if start + k < n})
    votes = Counter()
    pages_with_num = 0
    for i, pno in enumerate(pages):
        reader.check()
        progress(base + int(span * i / max(1, len(pages))), f"正在读取页眉页脚页码：第 {pno + 1} 页")
        cands = set()
        texts = []
        for ln in edge_lines(doc, reader, pno):
            texts.append(ln.text)
            texts.extend(f.text for f in (ln.frags[0], ln.frags[-1]))
        for t in texts:
            t = t.translate(FULLWIDTH).strip()
            m = PAGE_NUM_FULL.match(t)
            if m:
                cands.add(((pno + 1) - int(m.group(1)), 1.0))
                continue
            m = PAGE_NUM_EDGE.search(t)
            if m:
                num = int(m.group(1) or m.group(2))
                cands.add(((pno + 1) - num, 0.5))
        if cands:
            pages_with_num += 1
        for off, w in cands:
            votes[off] += w
        top = votes.most_common(2)
        if top and top[0][1] >= 3 and (len(top) == 1 or top[0][1] >= 2 * top[1][1]):
            break  # 已有足够多页面给出一致结果
    if not votes:
        return None
    off, score = votes.most_common(1)[0]
    if score >= 2 and score >= 0.3 * pages_with_num:
        return off
    return None


ROMAN_FULL = re.compile(r"^[\s\-—–·•\[\(（]*([ivxlcdmIVXLCDM]{1,7})[\s\-—–·•\]\)）]*$")


def roman_offset(doc, reader, front_end):
    """前言等部分常用罗马数字页码，单独推算它们的偏移。"""
    votes = Counter()
    for pno in range(0, min(front_end, len(doc), 40)):
        reader.check()
        for ln in edge_lines(doc, reader, pno):
            for t in {ln.text, ln.frags[0].text, ln.frags[-1].text}:
                m = ROMAN_FULL.match(t.strip())
                if m:
                    v = roman_to_int(m.group(1))
                    if v:
                        votes[(pno + 1) - v] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def offset_from_titles(doc, reader, entries, start, progress, base, span):
    n = len(doc)
    votes = Counter()
    cands = [e for e in entries if e.book is not None and not e.roman and len(norm_key(e.title)) >= 3][:10]
    for i, e in enumerate(cands):
        progress(base + int(span * i / max(1, len(cands))), "正在根据章节标题推算偏移")
        key = norm_key(e.title)[:14]
        lo, hi = start, min(n, start + e.book + 60)
        for pno in range(lo, hi):
            if reader.page_mode(pno) != "text":
                break
            if key in reader.norm_text(pno):
                votes[(pno + 1) - e.book] += 1
                break
    if not votes:
        return None
    off, cnt = votes.most_common(1)[0]
    return off if cnt >= 2 or len(cands) == 1 else None


def verify_entries(doc, reader, entries, offset, progress, base=0, span=100, radius=2, ocr_zoom=1.6,
                   skip_pages=()):
    """逐条到正文核对标题位置。返回 (已校验数, 已修正数)。skip_pages 为目录页，不参与匹配。"""
    n = len(doc)
    skip = set(skip_pages)
    ok = fixed = 0
    old_zoom = reader.zoom
    targets = [e for e in entries if e.book is not None and e.abs_page is None and e.status != "manual"]
    try:
        for i, e in enumerate(targets):
            reader.check()
            progress(base + int(span * i / max(1, len(targets))), f"正在核对：{e.title[:20]}")
            key = norm_key(e.title)
            if len(key) < 2:
                continue
            key = key[:16]
            p = e.book + offset - 1
            hit = None
            for d in (0, 1, -1, 2, -2)[: 1 + 2 * radius]:
                q = p + d
                if not (0 <= q < n) or q in skip:
                    continue
                if reader.page_mode(q) == "ocr":
                    reader.zoom = ocr_zoom
                if key in reader.norm_text(q):
                    hit = d
                    break
            if hit == 0:
                e.delta, e.status = 0, "ok"
                ok += 1
            elif hit is not None:
                e.delta, e.status = hit, "fixed"
                fixed += 1
            elif e.status != "guess":
                e.status = ""
    finally:
        reader.zoom = old_zoom
    return ok, fixed


# ----------------------------------------------------------------------------
# 总入口
# ----------------------------------------------------------------------------
def add_toc_entry(entries, reader, toc_pages, offset):
    """给目录页本身加一条书签，按页码顺序插到合适的位置。"""
    if not toc_pages:
        return
    toc_page = toc_pages[0] + 1
    # 有些书的目录里本身就列了“目录”这一条，此时只修正它的页码
    for e in entries:
        if is_heading(e.title):
            e.abs_page, e.book, e.status = toc_page, None, "ok"
            return
    title = "目录"
    for ln in reader.lines(toc_pages[0])[:8]:
        if is_heading(ln.text):
            t = ln.text.strip()
            # “目 录” 这类中间有空格的写法去掉空格，英文保留原样
            title = re.sub(r"\s+", "", t) if re.search(r"[一-鿿]", t) else t
            break
    new = Entry(title, level=1, abs_page=toc_page, status="ok", src_page=toc_pages[0])
    pos = 0
    for i, e in enumerate(entries):
        pg = e.pdf_page(offset)
        if e.level == 1 and pg is not None and pg > toc_page:
            pos = i
            break
        pos = i + 1
    entries.insert(pos, new)


def render_jpeg(page, max_side=2000):
    """把页面渲染成 JPEG，长边不超过 max_side 像素，用于发送给大模型。"""
    with FITZ_LOCK:
        r = page.rect
        zoom = min(2.5, max_side / max(r.width, r.height, 1))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
        return pix.tobytes("jpeg", jpg_quality=85)


def llm_entries(doc, reader, toc_pages, cfg, progress):
    """用视觉大模型读取目录页，返回与 parse_entries 相同格式的条目列表。"""
    imgs = [(p, render_jpeg(doc[p])) for p in toc_pages]
    items = llm.read_toc(imgs, cfg, check=reader.check,
                         progress=lambda i, n: progress(50 + int(10 * i / n), "正在用大模型读取目录"))
    entries = []
    prev = 0
    for pno, title, page, level in items:
        title = clean_title(title)
        if not title or is_heading(title):
            continue
        page = page.translate(FULLWIDTH).strip()
        book, roman = None, False
        if re.fullmatch(r"\d{1,4}", page):
            book = int(page)
        elif roman_to_int(page) is not None:
            book, roman = roman_to_int(page), True
        level = max(1, min(level, prev + 1))
        prev = level
        entries.append(Entry(title, level=level, book=book, roman=roman, src_page=pno))
    # 没有页码的条目，沿用后面第一个有页码条目的页码（与 parse_entries 一致）
    nxt = None
    for e in reversed(entries):
        if e.book is not None:
            nxt = e.book
        elif nxt is not None:
            e.book = nxt
            e.status = "guess"
    return [e for e in entries if e.book is not None]


def open_doc(path, password=None):
    with FITZ_LOCK:
        doc = pymupdf.open(path)
        if doc.needs_pass and not doc.authenticate(password or ""):
            doc.close()
            raise RuntimeError("PDF 已加密，密码不正确。")
    return doc


def recognize(path, force_ocr=False, toc_range=None, verify=True,
              progress: Callable[[int, str], None] = None, cancel=None, doc=None,
              password=None, llm_cfg=None) -> Result:
    """llm_cfg 为大模型接口配置（protocol/base/key/model）；为 None 时完全使用本地识别。"""
    progress = progress or (lambda *a: None)
    own = doc is None
    if own:
        doc = open_doc(path, password)
    reader = PageReader(doc, force_ocr=force_ocr, cancel=cancel)
    msgs = []
    try:
        n = len(doc)
        if toc_range:
            a, b = toc_range
            toc_pages = list(range(max(0, a - 1), min(n, b)))
        else:
            toc_pages = find_toc_pages(doc, reader, progress)
        if not toc_pages:
            return Result([], 0, [], "text", "", ["没有找到目录页。可以在“目录页”里手动指定页码范围后重新识别。"])

        modes = {reader.page_mode(p) for p in toc_pages}
        mode = "ocr" if modes == {"ocr"} else ("text" if modes == {"text"} else "mixed")
        entries = None
        if llm_cfg:
            try:
                entries = llm_entries(doc, reader, toc_pages, llm_cfg, progress) or None
                if entries:
                    msgs.append("目录内容由大模型识别。")
                else:
                    msgs.append("大模型没有识别出目录条目，已改用本地识别结果。")
            except Cancelled:
                raise
            except Exception as e:
                msgs.append(f"大模型识别失败，已改用本地识别结果（{e}）")
        if entries is None:
            for i, p in enumerate(toc_pages):
                progress(50 + int(10 * i / len(toc_pages)), f"正在解析目录第 {p + 1} 页")
                refine_ocr_page(doc, reader, p)
            entries = parse_entries(doc, reader, toc_pages)
        if not entries:
            return Result([], 0, toc_pages, mode, "", ["目录页上没有解析出条目。可尝试勾选“强制 OCR”后重新识别。"])

        start = toc_pages[-1] + 1
        offset = offset_from_page_numbers(doc, reader, start, progress, 60, 15)
        method = "页眉页脚页码"
        if offset is None:
            offset = offset_from_titles(doc, reader, entries, start, progress, 75, 5)
            method = "章节标题位置"
        if offset is None:
            first = min(e.book for e in entries if e.book is not None and not e.roman)
            offset = (start + 1) - first
            method = "估算（目录后第一页为正文起点）"
            msgs.append("没能可靠地推算页码偏移，已按“目录之后紧接正文”估算，请在预览中核对。")

        # 罗马数字页码（前言部分）不能套用正文偏移，单独推算
        romans = [e for e in entries if e.roman and e.book is not None]
        if romans:
            progress(78, "正在推算前言部分的页码")
            roff = roman_offset(doc, reader, max(1, offset))
            for e in romans:
                e.abs_page = max(1, min(n, e.book + (roff or 0)))
                e.status = "ok" if roff is not None else "guess"
                e.book = None

        add_toc_entry(entries, reader, toc_pages, offset)

        if verify and mode == "text":
            ok, fixed = verify_entries(doc, reader, entries, offset, progress, 80, 19, skip_pages=toc_pages)
            msgs.append(f"已在正文中核对 {ok} 条，修正 {fixed} 条。")
        progress(100, "识别完成")
        return Result(entries, offset, toc_pages, mode, method, msgs)
    finally:
        if own:
            with FITZ_LOCK:
                doc.close()


def run_verify(path, entries, offset, force_ocr=False, progress=None, cancel=None, password=None,
               toc_pages=()):
    progress = progress or (lambda *a: None)
    doc = open_doc(path, password)
    try:
        reader = PageReader(doc, force_ocr=force_ocr, cancel=cancel)
        ok, fixed = verify_entries(doc, reader, entries, offset, progress, 0, 100, skip_pages=toc_pages)
        progress(100, "核对完成")
        return ok, fixed
    finally:
        with FITZ_LOCK:
            doc.close()


# ----------------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------------
def existing_toc(doc):
    with FITZ_LOCK:
        return doc.get_toc(simple=True)


def export_pdf(src, dst, toc, progress=None, cancel=None, password=None):
    """toc: [[level, title, page(1-based)], ...]"""
    import os
    import tempfile
    progress = progress or (lambda *a: None)
    progress(10, "正在写入书签")
    doc = open_doc(src, password)
    with FITZ_LOCK:
        try:
            n = len(doc)
            clean, prev = [], 0
            for lv, title, pg in toc:
                lv = max(1, min(int(lv), prev + 1))
                pg = max(1, min(int(pg), n))
                clean.append([lv, title, pg])
                prev = lv
            doc.set_toc(clean, collapse=1)
            try:
                doc.set_pagemode("UseOutlines")
            except Exception:
                pass
            progress(40, "正在保存 PDF")
            same = os.path.abspath(src) == os.path.abspath(dst)
            target = dst
            if same:
                fd, target = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(dst)))
                os.close(fd)
            doc.save(target, garbage=1, deflate=True)
        finally:
            doc.close()
    if same:
        os.replace(target, dst)
    progress(100, "导出完成")
    return dst
