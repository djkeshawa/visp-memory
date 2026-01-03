# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for creating standalone executable of Visp Memory
with embedded dashboard.

Build with: pyinstaller visp-memory.spec
"""

import os
from pathlib import Path

# Get the root directory
root = Path(os.getcwd())
static_dir = root / "src" / "visp_memory" / "server" / "static"

# Collect all static files if they exist
datas = []
if static_dir.exists():
    for file in static_dir.rglob("*"):
        if file.is_file():
            rel_path = file.relative_to(static_dir.parent)
            datas.append((str(file), str(rel_path.parent)))

# Add other data files
datas.extend([
    ('README.md', '.'),
    ('LICENSE', '.'),
])

block_cipher = None

a = Analysis(
    ['src/visp_memory/interfaces/cli.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'visp_memory.core.memory',
        'visp_memory.core.storage',
        'visp_memory.core.neo4j_storage',
        'visp_memory.core.embeddings',
        'visp_memory.core.compression',
        'visp_memory.layers.episodic',
        'visp_memory.layers.semantic',
        'visp_memory.layers.intent',
        'visp_memory.server.app',
        'visp_memory.server.schemas',
        'visp_memory.interfaces.mcp',
        'chromadb',
        'sentence_transformers',
        'fastapi',
        'uvicorn',
        'typer',
        'rich',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='visp-memory',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
