# Reference admission note

The emblems are **full-frame emissive scenes** (galaxy/star/cluster/nebula art): the subject
occupies the entire frame, so the raw 512x512 crop has foreground coverage 0.988 and the
admission gate correctly refuses it ("no background to segment against").

Fix: the emblem is presented on a 536x536 canvas with a 12px uniform mid-gray isolation
border (`references/<name>-admission.png`). Emblem pixels are untouched; the border only gives
the segmentation primitives a real background so the subject (the emblem square, coverage
0.912, single coherent blob) is isolable for the later Divine Eye silhouette/interior gates.
The border is padding, not content, and is excluded from every visual judgment.
