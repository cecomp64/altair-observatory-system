# PyInstaller spec for robs.exe, the rig agent as one file for the rig PCs.
#   cd rig-agent && uv run --with pyinstaller pyinstaller packaging/robs.spec
# Output: dist/robs(.exe). Built on a Windows runner by release-rig-agent.yml.
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

a = Analysis(
    ["robs_entry.py"],
    hiddenimports=collect_submodules("robs") + collect_submodules("observatory_contracts"),
    datas=copy_metadata("remote-observatory-worker"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="robs", console=True, upx=False)
