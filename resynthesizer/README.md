# Resynthesizer installation reference

Third-party Resynthesizer 3.0.1 community Windows x64 build, installed and tested with GIMP 3.2.6. This repository does not redistribute the community executable or claim authorship.

- [Upstream source](https://github.com/bootchk/resynthesizer), GIMP 3 branch `resynthesizer3`; upstream GPL-3.0 license applies.
- [Community Windows release](https://github.com/ravik453/resynthesizer-windows-build/releases/tag/v3.0.1-gimp3-windows).
- Archive: `resynthesizer-3.0.1-gimp3-win64.zip`.
- Expected SHA-256: `53807fa1e51c57c5867679acecab777a23f7de652d697df7755354c792381ca2`.

## Install

Close GIMP, download from the release above, and verify with PowerShell Get-FileHash. Extract the eight plug-in subfolders into `%APPDATA%/GIMP/3.2/plug-ins`, preserving each folder's scripts/executable and locale files. Restart GIMP. Do not put the executable loose in the root directory.

## Tool reference

| Tool | Function / usage |
|---|---|
| Heal Selection | Select the unwanted region, then Filters → Enhance → Heal Selection; set context width, source direction and filling order |
| Heal Transparency | Reconstruct transparent regions from surrounding content |
| Resynth Controls | Advanced synthesis-engine controls |
| Map Style | Transfer texture/style from a sample |
| Render Texture | Synthesize a texture from a sample |
| Uncrop | Reconstruct expanded borders |
| Resynth Fill Pattern | Synthesize a fill using a pattern source |
| Engine | Underlying procedure used by the scripts |

Start with small selections and suitable surrounding texture. Keep an original layer for comparison. Menu locations and detailed options are defined by the supplied upstream scripts and can vary by release.

Local validation: archive checksum matched; the engine and seven tools registered in GIMP 3.2.6; Heal Selection successfully processed a temporary image. Other tools were checked for registration, not individually evaluated for visual quality.
