# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。由“打包为exe.bat”调用，一般不需要手动运行。

环境变量
    PDFBM_MODE    onedir（文件夹版，默认）或 onefile（单文件版）
    PDFBM_SPLASH  1 表示单文件版带启动画面（需要 Python 自带 Tcl/Tk）

为了控制体积，下面会把用不到的组件从打包结果里剔除。
"""
import os
import re
import sys

from PyInstaller.utils.hooks import collect_all

MODE = os.environ.get("PDFBM_MODE", "onedir").lower()
ONEFILE = MODE == "onefile"
SPLASH = ONEFILE and os.environ.get("PDFBM_SPLASH", "0") == "1"
WIN = sys.platform.startswith("win")
NAME = "PdfBookmarker-Portable" if ONEFILE else "PdfBookmarker"

# RapidOCR 按配置文件动态导入子模块，并且需要附带模型文件
ocr_datas, ocr_bins, ocr_hidden = collect_all("rapidocr_onnxruntime")

# 用不到的 Python 模块：不分析、不打包
EXCLUDES = [
    # PyMuPDF 2.0 的版面分析插件及其依赖
    "pymupdf.layout", "pymupdf_layout", "pymupdf4llm", "networkx",
    # 常被可选导入顺带拉进来的大型库
    "scipy", "pandas", "matplotlib", "sympy", "mpmath", "lxml", "fontTools",
    "IPython", "jedi", "tkinter", "_tkinter",
    # 界面只用到 QtCore / QtGui / QtWidgets
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSvg", "PySide6.QtPdf",
    # onnxruntime 的开发工具部分，推理不需要
    "onnxruntime.tools", "onnxruntime.transformers", "onnxruntime.quantization",
    "onnxruntime.training", "onnx",
    # Pillow 只被 RapidOCR 用来读图片文件，本程序直接传入图像数组，不需要各种格式插件
    "PIL.ImageQt", "PIL.ImageTk", "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp",
    "PIL.WebPImagePlugin", "PIL._imagingtk", "PIL._imagingcms", "PIL.ImageCms",
    # 标准库里不需要的部分
    "unittest", "pydoc", "pydoc_data", "sqlite3", "xmlrpc", "lib2to3", "test",
    "distutils", "setuptools", "pip", "pkg_resources",
]

# 打包结果中要删除的文件（按目标路径匹配，统一用 / 分隔）
DROP = [
    r"^PySide6/(Qt/)?translations/",           # Qt 多语言翻译文件
    r"^PySide6/(Qt/)?plugins/(imageformats|iconengines|styles|tls|networkinformation|generic|"
    r"multimedia|qmltooling|sqldrivers|designer|position|sensors|texttospeech|webview)/",
    r"(^|/)opengl32sw\.dll$",                  # 软件 OpenGL 渲染器，约 20MB
    r"(^|/)d3dcompiler_\d+\.dll$",             # 只有 Qt Quick 会用到
    r"(^|/)(lib)?Qt6(Pdf|Network|Svg|Qml\w*|Quick\w*|VirtualKeyboard|Multimedia\w*)(\.dll|\.so[.\d]*)$",
    r"^cv2/opencv_videoio_ffmpeg[^/]*\.dll$",  # OpenCV 的视频解码库，约 25MB
    r"^cv2/data/",                             # OpenCV 人脸检测模型
]
if WIN:
    # Windows 只需要 qwindows 这一个平台插件
    DROP.append(r"^PySide6/(Qt/)?plugins/platforms/(?!qwindows\.dll$)")
DROP_RE = [re.compile(p, re.I) for p in DROP]


def keep(entry):
    dest = entry[0].replace("\\", "/")
    return not any(r.search(dest) for r in DROP_RE)


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=ocr_bins,
    datas=ocr_datas,
    hiddenimports=ocr_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)
before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if keep(e)]
a.datas = [e for e in a.datas if keep(e)]
print(f"[PdfBookmarker] 已剔除 {before - len(a.binaries) - len(a.datas)} 个不需要的文件")

pyz = PYZ(a.pure)
icon = "icon.ico" if os.path.exists("icon.ico") else None

if ONEFILE:
    extra = []
    if SPLASH:
        splash = Splash("splash.png", binaries=a.binaries, datas=a.datas,
                        text_pos=None, always_on_top=False)
        extra = [splash, splash.binaries]
    exe = EXE(
        pyz, a.scripts, *extra, a.binaries, a.datas, [],
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        icon=icon,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
