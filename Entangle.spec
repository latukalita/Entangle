# -*- mode: python ; coding: utf-8 -*-
# Slim build: only bundle the Qt modules Entangle actually uses
# (QtCore/Gui/Widgets/Svg + QtWebEngine), not the whole PySide6 suite.

# Qt Python modules we don't use. Their shared libs still get pulled in
# automatically if WebEngine needs them (e.g. libQt6Quick), but this stops
# PyInstaller from collecting the whole QML tree and its 100+ plugin libs.
EXCLUDES = [
    'tkinter', 'unittest', 'pydoc', 'test',
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtQuick3D', 'PySide6.QtQuickTest', 'PySide6.QtWebEngineQuick', 'PySide6.QtWebView',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    'PySide6.QtGraphs', 'PySide6.QtGraphsWidgets', 'PySide6.QtLocation', 'PySide6.QtPositioning',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtSpatialAudio',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtSql', 'PySide6.QtTest',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
    'PySide6.QtSerialBus', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
    'PySide6.QtTextToSpeech', 'PySide6.QtWebSockets', 'PySide6.QtHttpServer', 'PySide6.QtNetworkAuth',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtUiTools', 'PySide6.QtXml', 'PySide6.QtConcurrent',
    'PySide6.QtSvgWidgets', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtDBus',
]


def keep(dest):
    d = dest.replace('\\', '/')
    if '/qtwebengine_locales/' in d:              # browser UI translations: keep English only
        return d.endswith('/en-US.pak')
    if d.startswith('PySide6/Qt/translations/'):  # Qt widget translations (app UI is English)
        return False
    if d.endswith('qtwebengine_devtools_resources.pak'):  # Chrome DevTools, never opened
        return False
    if d.startswith('PySide6/Qt/qml/'):
        return False
    return True


a = Analysis(
    ['entangle.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = [x for x in a.binaries if keep(x[0])]
a.datas = [x for x in a.datas if keep(x[0])]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Entangle',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Entangle',
)
