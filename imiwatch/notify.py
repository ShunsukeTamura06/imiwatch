"""Windows のトースト通知。winotify がなければ呼び出し側のフォールバックに任せる。"""
from __future__ import annotations

import sys


def send_toast(title: str, message: str, image_path: str | None = None) -> bool:
    """通知を出せたら True。Windows 以外や winotify がない場合は False。"""
    if sys.platform != "win32":
        print(f"[通知] {title}: {message}")
        return False
    try:
        from winotify import Notification, audio
    except ImportError:
        return False
    try:
        toast = Notification(app_id="imiwatch", title=title, msg=message, duration="short")
        if image_path:
            toast.icon = image_path
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True
    except Exception:  # noqa: BLE001 - 通知の失敗で見張りを止めない
        return False
