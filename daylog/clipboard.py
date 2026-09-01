"""Clipboard copy via platform-native tools — no third-party dependency.
Windows ships `clip`; Linux needs xclip or xsel installed (there's no
clipboard on X11 without one). A missing tool is reported, never a crash.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Optional, Tuple


def copy_to_clipboard(text: str) -> Tuple[bool, Optional[str]]:
    system = platform.system()
    try:
        if system == "Windows":
            # UTF-16LE is what clip.exe reliably accepts for Unicode text.
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, timeout=5)
            return True, None
        if system == "Linux":
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
                    return True, None
            return False, "no clipboard tool found — install xclip or xsel"
        return False, f"clipboard copy is not supported on {system!r} yet"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
