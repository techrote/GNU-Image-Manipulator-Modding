# NL Filter — automatic zoom preview

Menu: **Filters → Enhance → NL Filter — Zoom Preview…**

Select a single top-level RGB or grayscale raster layer. Adjust Alpha, Radius and Filter, then click **Open Render / Zoom Preview**. Rendering begins automatically. Settings refresh after a 300 ms pause, combining rapid changes while retaining zoom and scroll position. Use Fit, 100%, the 5–800% zoom field, scrollbars and Show original. Close the preview and choose OK to apply or Cancel to discard.

Alpha and Radius have sliders and numeric entry. Press Tab or Enter after typing. Alpha accepts 0–4; Radius accepts 1/3–8. Native modes preserve their normal behavior up to 1. Above 1, Alpha extrapolates the correction and Radius repeats radius=1 passes, blending a fractional final pass. This is a wrapper extension, not a change to the native geometric radius.

| Filter | Function |
|---|---|
| Native alpha-trimmed mean | Native nonlinear smoothing |
| Native optimal estimation | Native adaptive noise estimation/filtering |
| Native edge enhancement | Native sharpening mode |
| Gaussian | Smoothing; Radius is sigma in pixels |
| Bilateral | Edge-preserving smoothing; edge threshold controls colour tolerance |
| Median | Impulse removal using a square neighbourhood |
| Trimmed mean | Removes 20% at each end of valid neighbourhood samples |
| Unsharp | Sharpening; Radius is Gaussian sigma |
| Local contrast | Broader contrast enhancement; sigma is 3 × Radius |

Additional modes use Alpha as blend/strength and preserve source alpha. Median/trimmed mean round Radius up to neighbourhood half-width. The preview uses a duplicate and leaves source pixels unchanged; its display is 8-bit RGBA, shows the selected layer rather than the composite, and is limited to 40 megapixels. Full-resolution rendering is synchronous and may briefly occupy the dialog. GIMP's native `plug-in-nl-filter` must remain installed.

See [installation](INSTALL.md), [full reference](REFERENCE.txt), and [validation](VALIDATION.txt). The reference also describes the companion scaling feature; this folder registers only NL preview.
