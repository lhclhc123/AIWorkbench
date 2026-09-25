# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = []
hiddenimports = ['PyQt6.sip', 'markdown.extensions.tables', 'markdown.extensions.nl2br', 'docx', 'openpyxl', 'pdfminer', 'pdfminer.high_level', 'bs4', 'lxml', 'requests', 'pptx', 'reportlab', 'reportlab.pdfbase._cidfontdata', 'reportlab.pdfbase.cidfonts', 'reportlab.platypus', 'reportlab.pdfbase.ttfonts', 'psutil', 'pyperclip', 'mss', 'PIL', 'PIL.Image', 'PIL.ImageGrab', 'pyaudio', 'edge_tts', 'edge_tts.communicate', 'edge_tts.voices', 'dingtalk_stream', 'dingtalk_stream.chatbot', 'dingtalk_stream.client', 'websocket', 'win32com', 'win32com.client', 'win32com.client.dynamic', 'pythoncom', 'pywintypes', 'win32api', 'win32con', 'win32timezone']
datas += collect_data_files('docx')
datas += collect_data_files('pptx')
datas += collect_data_files('reportlab')
datas += collect_data_files('pdfminer')
datas += collect_data_files('lxml')
hiddenimports += collect_submodules('pdfminer')
hiddenimports += collect_submodules('reportlab')
hiddenimports += collect_submodules('pptx')
hiddenimports += collect_submodules('docx')
hiddenimports += collect_submodules('openpyxl')
hiddenimports += collect_submodules('edge_tts')
hiddenimports += collect_submodules('dingtalk_stream')
hiddenimports += collect_submodules('win32com')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'matplotlib', 'cv2', 'tkinter', 'moviepy', 'imageio', 'pandas', 'scipy', 'IPython', 'notebook', 'PyQt6.QtWebEngineCore', 'PyQt6.QtQuick', 'PyQt6.QtMultimedia', 'PyQt5', 'PyQt5.sip', 'PySide2', 'PySide6', 'qtpy', 'pygame', 'pygame.locals', 'Pythonwin', 'pythonwin', 'pywin32_testutil', 'win32ui', 'win32com.test', 'win32com.test.util'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AIWorkbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench\\build_v95_version.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AIWorkbench',
)
