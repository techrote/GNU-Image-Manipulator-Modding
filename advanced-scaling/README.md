# Advanced scaling

Menus: **Image → Advanced Scale Image…** and **Layer → Advanced Scale Layer…**

Select one RGB raster layer, open the relevant command, enter target dimensions and choose an algorithm. Dimensions start at the current size. Keep aspect ratio links width/height. Image scaling resizes the canvas and raster layers together; layer scaling retains its offset. Changes form an undo group.

| Algorithm | Parameters / purpose |
|---|---|
| Lanczos 3 | Fixed windowed sinc |
| Staged Box + Lanczos | Repeated 2× reductions followed by Lanczos |
| Linear-light Lanczos | Lanczos in linear light |
| Hamming | Compact reconstruction kernel |
| Box / area | Averaging reduction |
| Adjustable Lanczos | Sinc lobes 2–8 |
| Mitchell–Netravali | B and C, each 0–1; standard values 1/3 |
| Catmull–Rom | Fixed cubic B=0, C=0.5 |
| Cubic B-spline | Smoother fixed cubic B=1, C=0 |
| Kaiser sinc | Lobes 2–8 and beta 0–16 |
| Gaussian | Sigma 0.2–2 in kernel sample units |
| Bicubic | Fixed cubic |
| Nearest neighbour | Discrete / pixel-art scaling |

Antialias custom kernels widens support during reductions. Linear-light processing can apply to any method. Output sharpening has amount 0–2 and radius sigma 0.2–5 output pixels. Irrelevant controls are dimmed. Float premultiplied-alpha processing reduces transparent-edge colour bleed. These are conventional resamplers, not neural detail generation.

CPU budget: **max(1, physical cores minus 2)**. On the tested 6-core/12-thread Ryzen, the maximum is four. The helper sets numerical thread limits and affinity to one logical processor per selected physical core. Temporary matching affinity also covers native GIMP geometry/mask resizing; original affinity is restored after success/error. During scaling this affects the owning GIMP process. Box and Hamming receive the same cap; algorithms are not forced to keep four threads busy.

RGB raster layers only. Groups, text and live filters are rejected. Masks, channels, paths, offsets and geometry use native NoHalo scaling; custom kernels replace raster layer pixels only. Selections do not restrict resizing. Source/output limit: 40 million aggregate layer-pixels. Existing image storage precision is retained.

See [installation](INSTALL.md), [full reference](REFERENCE.txt), and [validation](VALIDATION.txt). The reference also describes the companion NL feature; this folder registers only scaling.
