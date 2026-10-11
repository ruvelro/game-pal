"""Overlay en macOS: un NSPanel sin foco, transparente a los clics e invisible para las capturas."""

from __future__ import annotations

from typing import Any

from gamepal.overlay.model import View

PADDING = 16
TITLE_SIZE = 11
BODY_SIZE = 15
STATUS_SIZE = 12


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

    def run(self) -> None:
        self._helper.runEventLoop(installInterrupt=True)

    def quit(self) -> None:
        self._helper.callAfter(self._helper.stopEventLoop)
