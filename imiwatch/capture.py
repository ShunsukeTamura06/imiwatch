"""画面範囲の取得、縮小、差分の計算。"""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageChops, ImageStat


def _mss():
    import mss  # 画面のない環境でもインポートだけはできるよう、ここで読み込む

    factory = getattr(mss, "MSS", None) or mss.mss  # 新しい mss では MSS が正式名
    return factory()


def grab(region: dict) -> Image.Image:
    """画面の範囲（物理ピクセル）を撮影する。"""
    box = {k: int(region[k]) for k in ("left", "top", "width", "height")}
    with _mss() as sct:
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def virtual_screen() -> dict:
    """全モニターをまとめた仮想画面の範囲。"""
    with _mss() as sct:
        m = sct.monitors[0]
        return {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}


def shrink(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale >= 1.0:
        return img.copy()
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def to_data_url(img: Image.Image, quality: int = 85) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=int(quality))
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def fingerprint(img: Image.Image) -> Image.Image:
    """差分比較用の小さな白黒画像。"""
    return img.convert("L").resize((64, 64), Image.BILINEAR)


def diff_score(a: Image.Image | None, b: Image.Image | None) -> float:
    """2枚の指紋画像の平均画素差（0〜255）。片方がなければ無限大。"""
    if a is None or b is None or a.size != b.size:
        return float("inf")
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
