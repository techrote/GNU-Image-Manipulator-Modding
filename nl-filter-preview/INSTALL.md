# Standalone installation

Windows x64, GIMP 3.2.6 and standard Python 3.13 are required. Python must be installed at `%LOCALAPPDATA%/Programs/Python/Python313/python.exe`.

1. Close GIMP. If replacing the combined `gimp-render-tools` bundle, move that old folder outside GIMP's plug-ins directory as a backup. Do not install duplicate procedure registrations.
2. Copy this entire feature folder into `%APPDATA%/GIMP/3.2/plug-ins`.
3. From the copied folder, use standard Python 3.13 to run `python -m pip install --only-binary=:all: --target ./vendor -r requirements.txt`.
4. Restart GIMP.

Keep `scale-worker.py` and `vendor` beside the entry-point script. Standard Windows wheels run in the external local Python helper because GIMP's bundled MinGW Python uses a different build. No image data is uploaded.

The standalone feature folders register disjoint procedures and may be installed together. Their shared implementation code is retained internally. Source is GPL-3.0-or-later; see COPYING. Dependencies retain their respective licenses. Move the feature folder outside plug-ins to uninstall.
