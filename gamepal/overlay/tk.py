"""Overlay con Tk para Linux y como respaldo. Siempre encima y sin bordes.

Limitaciones: Tk no puede hacer la ventana transparente a los clics ni excluirla de las capturas.
Se coloca en una esquina para molestar lo menos posible.
"""

from __future__ import annotations

import queue
from typing import Any

from gamepal.overlay.model import Rect, View

PADDING = 14


class TkOverlay:
    def __init__(self) -> None:
        import tkinter as tk

        self._tk: Any = tk
        self._root: Any = tk.Tk()
        self._root.withdraw()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg="#081a1e")
        self._frame: Any = tk.Frame(self._root, bg="#081a1e", highlightbackground="#3f7a74", highlightthickness=1)
        self._frame.pack(fill="both", expand=True)
        self._title: Any = tk.Label(self._frame, bg="#081a1e", fg="#7ee0b9", anchor="w", justify="left")
        self._body: Any = tk.Label(self._frame, bg="#081a1e", fg="#ebf5f0", anchor="w", justify="left")
        self._status: Any = tk.Label(self._frame, bg="#081a1e", fg="#e0c48e", anchor="w", justify="left")
        self._queue: queue.Queue[View | None] = queue.Queue()
        self._root.after(100, self._poll)

    def render(self, view: View | None) -> None:
        self._queue.put(view)

    def _poll(self) -> None:
        latest: View | bool | None = False
        while not self._queue.empty():
            latest = self._queue.get_nowait()
        if latest is not False:
            self._apply(latest if isinstance(latest, View) else None)
        self._root.after(100, self._poll)

    def _apply(self, view: View | None) -> None:
        if view is None:
            self._root.withdraw()
            return
        inner = view.width - 2 * PADDING
        for widget in (self._title, self._body, self._status):
            widget.pack_forget()
        for widget, text, size, bold in (
            (self._title, view.title.upper(), 9, True),
            (self._body, view.body, 12, False),
            (self._status, view.status, 10, False),
        ):
            if text:
                weight = "bold" if bold else "normal"
                widget.configure(text=text, wraplength=inner, font=("Sans", round(size * view.font_scale), weight))
                widget.pack(fill="x", padx=PADDING, pady=(PADDING // 2, 0))
        self._root.attributes("-alpha", view.opacity)
        self._root.update_idletasks()
        height = self._frame.winfo_reqheight() + PADDING // 2
        top = view.y - height if view.anchor_bottom else view.y
        self._root.geometry(f"{view.width}x{height}+{view.x}+{top}")
        self._root.deiconify()
        self._root.lift()

    def screen(self) -> Rect | None:
        return Rect(0, 0, int(self._root.winfo_screenwidth()), int(self._root.winfo_screenheight()))

    def run(self) -> None:
        self._root.mainloop()

    def quit(self) -> None:
        self._root.after(0, self._root.destroy)
