"""把 PNG 裁到 alpha 内容的最小外接矩形。

用法：
    python scripts/crop_alpha.py a.png [b.png ...]           # 各自独立裁切
    python scripts/crop_alpha.py --union a.png b.png [...]   # 共用并集包围盒

同一组贴图（例如垃圾桶的合盖/开盖）必须用 --union，否则两张图的内容位置会错开，
切换状态时画面会跳。
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ALPHA_MIN = 8


def bbox(alpha):
    ys, xs = np.nonzero(alpha >= ALPHA_MIN)
    if not len(ys):
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


def main(argv):
    union = "--union" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1

    images = [np.array(Image.open(p).convert("RGBA")) for p in paths]
    boxes = [bbox(im[..., 3]) for im in images]
    if any(b is None for b in boxes):
        print("空图或全透明：", [p for p, b in zip(paths, boxes) if b is None])
        return 1

    if union:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        boxes = [(x0, y0, x1, y1)] * len(paths)

    for path, im, (x0, y0, x1, y1) in zip(paths, images, boxes):
        out = im[y0:y1 + 1, x0:x1 + 1]
        Image.fromarray(out, "RGBA").save(path)
        print(f"{path}  {im.shape[1]}x{im.shape[0]} -> {out.shape[1]}x{out.shape[0]}  "
              f"aspect={round(out.shape[0] / out.shape[1], 4)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
