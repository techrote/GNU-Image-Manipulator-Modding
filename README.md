# GNU Image Manipulator Modding

Extensions and installation references prepared for **GIMP 3.2.6 on Windows x64**.

- [NL Filter preview](nl-filter-preview/): automatic preview preserving zoom/pan, sliders, extended values and six extra filters.
- [Advanced scaling](advanced-scaling/): 13 algorithms, adjustable kernels and physical-core CPU limits.
- [Resynthesizer](resynthesizer/): third-party installation provenance and usage reference.

Each modification has its own folder and documentation. Custom tools are standalone source distributions with disjoint procedure registrations. Install pinned dependencies privately using the included instructions. Shared implementation code remains in each feature; only the relevant procedures register. The standalone split changes registration only; the combined implementation was tested in GIMP 3.2.6. See feature validation records for scope and limits.

Original GIMP commands remain available. These are custom extensions, not official GIMP releases. Resynthesizer is third-party software. No large numerical-library binaries are committed.
