"""Overlay con Tk para Linux y como respaldo. Siempre encima y sin bordes.

Limitaciones: Tk no puede hacer la ventana transparente a los clics ni excluirla de las capturas.
Se coloca en una esquina para molestar lo menos posible.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Any

from gamepal.overlay.model import ChatView, Rect, View

PADDING = 14
BACKGROUND = "#081a1e"
FIELD = "#123138"
TEXT = "#ebf5f0"
MINT = "#7ee0b9"
GOLD = "#e0c48e"
MUTED = "#8fb0aa"
RED = "#ef7d74"


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
        self._chat_queue: queue.Queue[ChatView | None] = queue.Queue()
        self._chat: Any = None
        self._chat_shown = False
        self._submit: Callable[[str], None] = lambda text: None
        self._close: Callable[[], None] = lambda: None
        self._root.after(100, self._poll)

    def render(self, view: View | None) -> None:
        self._queue.put(view)

    def _poll(self) -> None:
        latest: View | bool | None = False
        while not self._queue.empty():
            latest = self._queue.get_nowait()
        if latest is not False:
            self._apply(latest if isinstance(latest, View) else None)
        chat: ChatView | bool | None = False
        while not self._chat_queue.empty():
            chat = self._chat_queue.get_nowait()
        if chat is not False:
            self._apply_chat(chat if isinstance(chat, ChatView) else None)
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

    # Chat -----------------------------------------------------------------------------------

    def bind_chat(self, submit: Callable[[str], None], close: Callable[[], None]) -> None:
        self._submit, self._close = submit, close

    def render_chat(self, chat: ChatView | None) -> None:
        self._chat_queue.put(chat)

    def _build_chat(self) -> None:
        tk = self._tk
        # Una ventana normal (no overrideredirect): sin ella, el gestor de ventanas no le da el teclado.
        window = tk.Toplevel(self._root, bg=BACKGROUND, highlightbackground="#3f7a74", highlightthickness=1)
        window.withdraw()
        window.title("game-pal")
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", lambda: self._close())
        title = tk.Label(window, text="GAME-PAL", bg=BACKGROUND, fg=MINT, anchor="w")
        history = tk.Text(window, wrap="word", bg=BACKGROUND, fg=TEXT, relief="flat", highlightthickness=0, bd=0)
        status = tk.Label(window, bg=BACKGROUND, fg=GOLD, anchor="w")
        field = tk.Entry(window, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", highlightthickness=0)
        hint = tk.Label(window, bg=BACKGROUND, fg=MUTED, anchor="w")

        def send(_event: Any) -> str:
            text = field.get()
            field.delete(0, "end")
            if text.strip():
                self._submit(text)
            return "break"

        field.bind("<Return>", send)
        field.bind("<Escape>", lambda _event: self._close())
        window.bind("<Escape>", lambda _event: self._close())
        self._chat = {"window": window, "title": title, "history": history, "status": status, "field": field,
                      "hint": hint}  # fmt: skip

    def _apply_chat(self, chat: ChatView | None) -> None:
        if chat is None:
            if self._chat is not None:
                self._chat["window"].withdraw()
            self._chat_shown = False
            return
        if self._chat is None:
            self._build_chat()
        parts = self._chat
        scale = chat.font_scale
        history = parts["history"]
        history.configure(font=("Sans", round(11 * scale)), state="normal")
        history.tag_configure("question", foreground=MINT, font=("Sans", round(10 * scale), "bold"))
        history.tag_configure("proactive", foreground=GOLD, font=("Sans", round(10 * scale), "bold"))
        history.tag_configure("error", foreground=RED)
        history.delete("1.0", "end")
        if not chat.entries:
            history.insert("end", chat.empty)
        for entry in chat.entries:
            mark = "💡 " if entry.kind == "proactive" else "› "
            history.insert("end", f"{mark}{entry.question}\n", entry.kind if entry.kind == "proactive" else "question")
            answer = entry.answer or ("…" if not entry.done else "")
            history.insert("end", f"{answer}\n\n", "error" if entry.error else ())
        history.configure(state="disabled")
        history.see("end")
        parts["title"].configure(font=("Sans", round(9 * scale), "bold"))
        parts["status"].configure(text=chat.status, font=("Sans", round(10 * scale)))
        parts["field"].configure(font=("Sans", round(12 * scale)))
        parts["hint"].configure(text=f"{chat.placeholder} · {chat.hint}", font=("Sans", round(9 * scale)))
        for name in ("title", "history", "status", "field", "hint"):
            parts[name].pack_forget()
        parts["title"].pack(fill="x", padx=PADDING, pady=(PADDING, 4))
        parts["history"].pack(fill="both", expand=True, padx=PADDING)
        if chat.status:
            parts["status"].pack(fill="x", padx=PADDING, pady=(4, 0))
        parts["field"].pack(fill="x", padx=PADDING, pady=(6, 4), ipady=6)
        parts["hint"].pack(fill="x", padx=PADDING, pady=(0, PADDING))
        window = parts["window"]
        window.attributes("-alpha", chat.opacity)
        window.geometry(f"{chat.width}x{chat.height}+{chat.x}+{chat.y}")
        if not self._chat_shown:
            self._chat_shown = True
            window.deiconify()
            window.lift()
            window.focus_force()
            parts["field"].focus_set()

    def screen(self) -> Rect | None:
        return Rect(0, 0, int(self._root.winfo_screenwidth()), int(self._root.winfo_screenheight()))

    def run(self) -> None:
        self._root.mainloop()

    def quit(self) -> None:
        self._root.after(0, self._root.destroy)
