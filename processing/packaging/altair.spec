# PyInstaller spec for altair.exe, the processing core as one file for the
# processing PC.
#   cd processing && uv run --with pyinstaller pyinstaller packaging/altair.spec
# Output: dist/altair(.exe). Built on a Windows runner by release-processing.yml.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

a = Analysis(
    ["altair_entry.py"],
    hiddenimports=(collect_submodules("altair") + collect_submodules("observatory_contracts")
                   + collect_submodules("keyring.backends")),
    # schema.sql ships inside the package; astropy needs its data files; keyring
    # finds its backends through package metadata.
    datas=(collect_data_files("altair") + collect_data_files("astropy") + copy_metadata("keyring")
           + copy_metadata("altair") + copy_metadata("astropy")),
    excludes=["tkinter", "matplotlib", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="altair", console=True, upx=False)
