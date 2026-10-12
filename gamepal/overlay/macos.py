"""Overlay en macOS: un NSPanel sin foco, transparente a los clics e invisible para las capturas.

El chat es otro panel que sí recibe el teclado, pero sin activar game-pal: el juego sigue siendo la app
activa (no cambia de escritorio ni pierde la pantalla completa) y, al cerrarlo, el teclado vuelve a él.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gamepal.overlay.model import ChatView, Rect, View

PADDING = 16
TITLE_SIZE = 11
BODY_SIZE = 15
STATUS_SIZE = 12
CHAT_PADDING = 14
CHAT_INPUT_HEIGHT = 30

_CLASSES: dict[str, Any] = {}


def _chat_classes() -> dict[str, Any]:
    """Subclases de Cocoa para el chat. Se definen una sola vez por proceso (Objective-C no admite repetir)."""
    if _CLASSES:
        return _CLASSES
    import AppKit
    import objc

    class GamePalChatPanel(AppKit.NSPanel):  # type: ignore[misc]
        # Un panel sin bordes no recibe el teclado salvo que lo diga.
        def canBecomeKeyWindow(self) -> bool:
            return True

        def canBecomeMainWindow(self) -> bool:
            return False

    class GamePalChatHandler(AppKit.NSObject):  # type: ignore[misc]
        def initWithSubmit_close_(self, submit: Any, close: Any) -> Any:
            self = objc.super(GamePalChatHandler, self).init()
            if self is None:
                return None
            self.on_submit = submit
            self.on_close = close
            return self

        def submit_(self, sender: Any) -> None:
            text = str(sender.stringValue() or "")
            sender.setStringValue_("")
            if text.strip():
                self.on_submit(text)

        def control_textView_doCommandBySelector_(self, control: Any, view: Any, selector: Any) -> bool:
            name = selector.decode() if isinstance(selector, bytes) else str(selector)
            if name == "cancelOperation:":  # Esc
                self.on_close()
                return True
            return False

    _CLASSES.update(panel=GamePalChatPanel, handler=GamePalChatHandler)
    return _CLASSES


class MacOverlay:
    def __init__(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        self._ak: Any = AppKit
        self._helper: Any = AppHelper
        app = AppKit.NSApplication.sharedApplication()
        # Accesorio: sin icono en el Dock ni menú, nunca roba el foco al juego.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._panel: Any = None
        self._title: Any = None
        self._body: Any = None
        self._status: Any = None
        self._chat: Any = None
        self._chat_parts: dict[str, Any] = {}
        self._chat_shown = False
        self._submit: Callable[[str], None] = lambda text: None
        self._close: Callable[[], None] = lambda: None

    def _build(self) -> None:
        ak = self._ak
        style = ak.NSWindowStyleMaskBorderless | ak.NSWindowStyleMaskNonactivatingPanel
        panel = ak.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (400, 100)), style, ak.NSBackingStoreBuffered, False
        )
        panel.setLevel_(ak.NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            ak.NSWindowCollectionBehaviorCanJoinAllSpaces
            | ak.NSWindowCollectionBehaviorFullScreenAuxiliary
            | ak.NSWindowCollectionBehaviorStationary
            | ak.NSWindowCollectionBehaviorIgnoresCycle
        )
        panel.setIgnoresMouseEvents_(True)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setBackgroundColor_(ak.NSColor.clearColor())
        panel.setHidesOnDeactivate_(False)
        # No aparece en capturas ni grabaciones de pantalla, tampoco en las de game-pal.
        panel.setSharingType_(ak.NSWindowSharingNone)
        content = panel.contentView()
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(14)
        layer.setBorderWidth_(1)
        layer.setBorderColor_(ak.NSColor.colorWithSRGBRed_green_blue_alpha_(0.49, 0.88, 0.72, 0.45).CGColor())
        layer.setBackgroundColor_(ak.NSColor.colorWithSRGBRed_green_blue_alpha_(0.03, 0.10, 0.12, 0.94).CGColor())

        def label(color: Any) -> Any:
            field = ak.NSTextField.wrappingLabelWithString_("")
            field.setTextColor_(color)
            field.setDrawsBackground_(False)
            field.setBezeled_(False)
            field.setSelectable_(False)
            content.addSubview_(field)
            return field

        self._title = label(ak.NSColor.colorWithSRGBRed_green_blue_alpha_(0.49, 0.88, 0.72, 1))
        self._body = label(ak.NSColor.colorWithSRGBRed_green_blue_alpha_(0.92, 0.96, 0.94, 1))
        self._status = label(ak.NSColor.colorWithSRGBRed_green_blue_alpha_(0.88, 0.77, 0.56, 1))
        self._panel = panel

    def render(self, view: View | None) -> None:
        self._helper.callAfter(self._apply, view)

    def _apply(self, view: View | None) -> None:
        ak = self._ak
        if self._panel is None:
            self._build()
        panel = self._panel
        if view is None:
            panel.orderOut_(None)
            return
        scale = view.font_scale
        inner = view.width - 2 * PADDING
        y = PADDING
        blocks: list[tuple[Any, str, float, bool]] = [
            (self._status, view.status, STATUS_SIZE * scale, False),
            (self._body, view.body, BODY_SIZE * scale, False),
            (self._title, view.title.upper(), TITLE_SIZE * scale, True),
        ]
        # Se apilan de abajo arriba, como cuenta Cocoa.
        for field, text, size, bold in blocks:
            field.setHidden_(not text)
            if not text:
                continue
            font = ak.NSFont.boldSystemFontOfSize_(size) if bold else ak.NSFont.systemFontOfSize_(size)
            field.setFont_(font)
            field.setStringValue_(text)
            field.setPreferredMaxLayoutWidth_(inner)
            height = field.sizeThatFits_((inner, 10_000)).height
            field.setFrame_(((PADDING, y), (inner, height)))
            y += height + 6
        height = y - 6 + PADDING
        screen_height = ak.NSScreen.screens()[0].frame().size.height
        top = view.y - height if view.anchor_bottom else view.y
        panel.setAlphaValue_(view.opacity)
        panel.setFrame_display_(((view.x, screen_height - top - height), (view.width, height)), True)
        panel.orderFrontRegardless()

    # Chat -----------------------------------------------------------------------------------

    def bind_chat(self, submit: Callable[[str], None], close: Callable[[], None]) -> None:
        self._submit, self._close = submit, close

    def render_chat(self, chat: ChatView | None) -> None:
        self._helper.callAfter(self._apply_chat, chat)

    def _color(self, r: float, g: float, b: float, a: float = 1) -> Any:
        return self._ak.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

    def _build_chat(self) -> None:
        ak = self._ak
        classes = _chat_classes()
        style = ak.NSWindowStyleMaskBorderless | ak.NSWindowStyleMaskNonactivatingPanel
        panel = (
            classes["panel"]
            .alloc()
            .initWithContentRect_styleMask_backing_defer_(
                ((0, 0), (CHAT_INPUT_HEIGHT * 16, 400)), style, ak.NSBackingStoreBuffered, False
            )
        )
        panel.setLevel_(ak.NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            ak.NSWindowCollectionBehaviorCanJoinAllSpaces
            | ak.NSWindowCollectionBehaviorFullScreenAuxiliary
            | ak.NSWindowCollectionBehaviorIgnoresCycle
        )
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setBackgroundColor_(ak.NSColor.clearColor())
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(False)
        panel.setSharingType_(ak.NSWindowSharingNone)
        panel.setAppearance_(ak.NSAppearance.appearanceNamed_(ak.NSAppearanceNameDarkAqua))
        content = panel.contentView()
        # Un fondo opaco de verdad: encima de un juego, el texto debe leerse sin lo que hay detrás.
        background = ak.NSBox.alloc().initWithFrame_(content.bounds())
        background.setBoxType_(ak.NSBoxCustom)
        background.setTitlePosition_(ak.NSNoTitle)
        background.setCornerRadius_(14)
        background.setBorderWidth_(1)
        background.setBorderColor_(self._color(0.49, 0.88, 0.72, 0.45))
        background.setFillColor_(self._color(0.03, 0.10, 0.12, 1))
        background.setAutoresizingMask_(ak.NSViewWidthSizable | ak.NSViewHeightSizable)
        content.addSubview_(background)

        def label(color: Any) -> Any:
            field = ak.NSTextField.labelWithString_("")
            field.setTextColor_(color)
            content.addSubview_(field)
            return field

        scroll = ak.NSScrollView.alloc().initWithFrame_(((0, 0), (100, 100)))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        history = ak.NSTextView.alloc().initWithFrame_(((0, 0), (100, 100)))
        history.setEditable_(False)
        history.setSelectable_(True)
        history.setDrawsBackground_(False)
        history.setTextContainerInset_((0, 4))
        history.setAutoresizingMask_(ak.NSViewWidthSizable)
        scroll.setDocumentView_(history)
        content.addSubview_(scroll)

        field = ak.NSTextField.alloc().initWithFrame_(((0, 0), (100, CHAT_INPUT_HEIGHT)))
        field.setBezeled_(True)
        field.setBezelStyle_(ak.NSTextFieldRoundedBezel)
        field.setFocusRingType_(ak.NSFocusRingTypeNone)
        field.setTextColor_(self._color(0.92, 0.96, 0.94))
        handler = classes["handler"].alloc().initWithSubmit_close_(self._submit, self._close)
        field.setTarget_(handler)
        field.setAction_("submit:")
        field.setDelegate_(handler)
        content.addSubview_(field)

        self._chat = panel
        self._chat_parts = {
            "title": label(self._color(0.49, 0.88, 0.72)),
            "status": label(self._color(0.88, 0.77, 0.56)),
            "hint": label(self._color(0.56, 0.69, 0.67)),
            "scroll": scroll,
            "history": history,
            "input": field,
            "handler": handler,  # Cocoa no retiene el delegado: se guarda aquí
        }

    def _history_text(self, chat: ChatView) -> Any:
        ak = self._ak
        scale = chat.font_scale
        text = ak.NSMutableAttributedString.alloc().init()

        def add(value: str, size: float, color: Any, bold: bool = False) -> None:
            font = ak.NSFont.boldSystemFontOfSize_(size) if bold else ak.NSFont.systemFontOfSize_(size)
            attrs = {ak.NSFontAttributeName: font, ak.NSForegroundColorAttributeName: color}
            text.appendAttributedString_(ak.NSAttributedString.alloc().initWithString_attributes_(value, attrs))

        if not chat.entries:
            add(chat.empty, 14 * scale, self._color(0.56, 0.69, 0.67))
        for entry in chat.entries:
            if entry.kind == "proactive":
                add(f"💡 {entry.question}\n", 13 * scale, self._color(0.88, 0.77, 0.56), bold=True)
            else:
                add(f"› {entry.question}\n", 13 * scale, self._color(0.49, 0.88, 0.72), bold=True)
            answer = entry.answer or ("…" if not entry.done else "")
            color = self._color(0.94, 0.49, 0.45) if entry.error else self._color(0.92, 0.96, 0.94)
            add(f"{answer}\n\n", 14.5 * scale, color)
        return text

    def _apply_chat(self, chat: ChatView | None) -> None:
        if chat is None:
            if self._chat is not None and self._chat_shown:
                self._chat.orderOut_(None)
            self._chat_shown = False
            return
        if self._chat is None:
            self._build_chat()
        ak, panel, parts = self._ak, self._chat, self._chat_parts
        width, height, pad = chat.width, chat.height, CHAT_PADDING
        scale = chat.font_scale
        inner = width - 2 * pad
        parts["title"].setFont_(ak.NSFont.boldSystemFontOfSize_(TITLE_SIZE * scale))
        parts["title"].setStringValue_("GAME-PAL")
        parts["title"].setFrame_(((pad, height - pad - 16 * scale), (inner, 16 * scale)))
        parts["hint"].setFont_(ak.NSFont.systemFontOfSize_(11 * scale))
        parts["hint"].setStringValue_(chat.hint)
        parts["hint"].setFrame_(((pad, 8), (inner, 16 * scale)))
        field_height = CHAT_INPUT_HEIGHT * scale
        field_y = 12 + 16 * scale
        parts["input"].setFont_(ak.NSFont.systemFontOfSize_(14 * scale))
        parts["input"].setPlaceholderString_(chat.placeholder)
        parts["input"].setFrame_(((pad, field_y), (inner, field_height)))
        status_y = field_y + field_height + 6
        parts["status"].setFont_(ak.NSFont.systemFontOfSize_(STATUS_SIZE * scale))
        parts["status"].setStringValue_(chat.status)
        parts["status"].setHidden_(not chat.status)
        parts["status"].setFrame_(((pad, status_y), (inner, 16 * scale)))
        history_y = status_y + (20 * scale if chat.status else 0)
        history_top = height - pad - 22 * scale
        parts["scroll"].setFrame_(((pad, history_y), (inner, max(40, history_top - history_y))))
        history = parts["history"]
        history.setFrame_(((0, 0), (inner, max(40, history_top - history_y))))
        history.textStorage().setAttributedString_(self._history_text(chat))
        history.scrollToEndOfDocument_(None)
        screen_height = ak.NSScreen.screens()[0].frame().size.height
        panel.setAlphaValue_(chat.opacity)
        panel.setFrame_display_(((chat.x, screen_height - chat.y - height), (width, height)), True)
        if not self._chat_shown:
            panel.orderFrontRegardless()
            panel.makeKeyWindow()
            panel.makeFirstResponder_(parts["input"])
            self._chat_shown = True

    def screen(self) -> Rect | None:
        ak = self._ak
        screens = ak.NSScreen.screens()
        if not screens:
            return None
        # Cocoa cuenta desde abajo y desde la pantalla principal; la vista, desde arriba.
        height = screens[0].frame().size.height
        area = (ak.NSScreen.mainScreen() or screens[0]).visibleFrame()
        top = height - (area.origin.y + area.size.height)
        return Rect(int(area.origin.x), int(top), int(area.size.width), int(area.size.height))

    def run(self) -> None:
        self._helper.runEventLoop(installInterrupt=True)

    def quit(self) -> None:
        self._helper.callAfter(self._helper.stopEventLoop)
