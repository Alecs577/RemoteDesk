"""Tema chiaro della finestra: carta, celeste, laguna."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PAPER = "#ffffff"
FIELD = "#f4fbfe"
SKY = "#e5f4fb"
VELA = "#c9e7f6"
CELESTE = "#8ecae6"
ACTION = "#176184"
ACTION_DEEP = "#124f6a"
INK = "#143044"
MUTED = "#4d6574"
LINE = "#d3e8f2"
DANGER = "#8d2f38"


def group_desk_id(desk_id: str) -> str:
    raw = desk_id.strip()
    if len(raw) == 9 and raw.isdigit():
        return f"{raw[:3]} {raw[3:6]} {raw[6:]}"
    return raw


def apply_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(bg=FIELD)
    root.option_add("*Text.background", PAPER)
    root.option_add("*Text.foreground", INK)
    root.option_add("*Text.insertBackground", INK)
    root.option_add("*Text.font", "{Segoe UI} 10")
    root.option_add("*Listbox.background", PAPER)
    root.option_add("*Listbox.foreground", INK)
    root.option_add("*Listbox.selectBackground", ACTION)
    root.option_add("*Listbox.selectForeground", PAPER)
    root.option_add("*Canvas.background", SKY)

    style.configure(".", background=FIELD, foreground=INK, font=("Segoe UI", 10))
    style.configure("TFrame", background=FIELD)
    style.configure("TLabel", background=FIELD, foreground=INK)
    style.configure("Muted.TLabel", background=FIELD, foreground=MUTED)
    style.configure("Eyebrow.TLabel", background=FIELD, foreground=ACTION, font=("Segoe UI", 9, "bold"))
    style.configure("Title.TLabel", background=FIELD, foreground=INK, font=("Segoe UI", 20, "bold"))
    style.configure("Brand.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 16, "bold"))
    style.configure("Section.TLabel", background=FIELD, foreground=INK, font=("Segoe UI", 12, "bold"))
    style.configure("CardSection.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 12, "bold"))
    style.configure("FieldLabel.TLabel", background=PAPER, foreground=MUTED, font=("Segoe UI", 9, "bold"))
    style.configure("CardTitle.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 14, "bold"))
    style.configure("CardSubtitle.TLabel", background=PAPER, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Notice.TLabel", background=SKY, foreground=ACTION, font=("Segoe UI", 10, "bold"), padding=(9, 7))
    style.configure("Mono.TLabel", background=FIELD, foreground=INK, font=("Consolas", 9))

    style.configure("Card.TFrame", background=PAPER)
    style.configure("Plate.TFrame", background=PAPER, relief="solid", borderwidth=1, bordercolor=LINE)
    style.configure("Card.TLabel", background=PAPER, foreground=INK)
    style.configure("CardMuted.TLabel", background=PAPER, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("CardMono.TLabel", background=PAPER, foreground=INK, font=("Consolas", 9))
    style.configure("Stage.TFrame", background=PAPER)
    style.configure("Stage.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 11))
    style.configure("StageMuted.TLabel", background=PAPER, foreground=MUTED, font=("Segoe UI", 11))
    style.configure("Id.TLabel", background=PAPER, foreground=INK, font=("Segoe UI", 34, "bold"))
    style.configure("Quiet.TButton", padding=(8, 4))

    style.configure(
        "TButton",
        background=PAPER,
        foreground=INK,
        bordercolor=LINE,
        lightcolor=PAPER,
        darkcolor=LINE,
        padding=(12, 7),
        focusthickness=1,
        focuscolor=ACTION,
    )
    style.map(
        "TButton",
        background=[("pressed", VELA), ("active", SKY), ("disabled", "#eef5f8")],
        foreground=[("disabled", "#8aa0ad")],
        bordercolor=[("focus", ACTION), ("active", CELESTE)],
    )
    style.configure(
        "Primary.TButton",
        background=ACTION,
        foreground=PAPER,
        bordercolor=ACTION,
        lightcolor=ACTION,
        darkcolor=ACTION_DEEP,
        padding=(14, 7),
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", ACTION_DEEP), ("active", "#1d7398"), ("disabled", "#9bb8c6")],
        foreground=[("disabled", PAPER)],
        bordercolor=[("focus", ACTION_DEEP), ("active", ACTION)],
    )
    style.configure(
        "Danger.TButton",
        background=PAPER,
        foreground=DANGER,
        bordercolor="#e7c9cc",
        lightcolor=PAPER,
        darkcolor="#e7c9cc",
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", "#f6e4e6"), ("active", "#fdf7f7"), ("disabled", "#f4f7f8")],
        foreground=[("disabled", "#c9b0b3")],
    )

    style.configure(
        "TEntry",
        fieldbackground=PAPER,
        foreground=INK,
        bordercolor=LINE,
        lightcolor=PAPER,
        darkcolor=LINE,
        padding=6,
        insertcolor=INK,
    )
    style.map("TEntry", bordercolor=[("focus", ACTION)], lightcolor=[("focus", ACTION)])

    style.configure("TNotebook", background=FIELD, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure(
        "TNotebook.Tab",
        background=PAPER,
        foreground=MUTED,
        padding=(16, 8),
        borderwidth=0,
        focuscolor=FIELD,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SKY), ("active", SKY)],
        foreground=[("selected", ACTION), ("active", INK)],
    )

    style.configure("TCheckbutton", background=FIELD, foreground=INK)
    style.map("TCheckbutton", background=[("active", SKY)])
    style.configure("Card.TCheckbutton", background=PAPER, foreground=INK)
    style.map("Card.TCheckbutton", background=[("active", SKY)])

    style.configure(
        "Treeview",
        background=PAPER,
        fieldbackground=PAPER,
        foreground=INK,
        borderwidth=0,
        rowheight=30,
        font=("Segoe UI", 10),
    )
    style.map(
        "Treeview",
        background=[("selected", SKY)],
        foreground=[("selected", ACTION)],
    )
    style.configure("Treeview.Heading", background=FIELD, foreground=MUTED, font=("Segoe UI", 9, "bold"))
    style.configure("TLabelframe", background=PAPER, bordercolor=LINE, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=PAPER, foreground=ACTION, font=("Segoe UI", 9, "bold"))

    style.configure(
        "TCombobox",
        fieldbackground=PAPER,
        background=PAPER,
        foreground=INK,
        arrowcolor=ACTION,
        bordercolor=LINE,
    )
    style.map("TCombobox", fieldbackground=[("readonly", PAPER)], bordercolor=[("focus", ACTION)])

    style.configure(
        "Vertical.TScrollbar",
        background=VELA,
        troughcolor=FIELD,
        bordercolor=FIELD,
        arrowcolor=ACTION,
        lightcolor=VELA,
        darkcolor=VELA,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=VELA,
        troughcolor=FIELD,
        bordercolor=FIELD,
        arrowcolor=ACTION,
    )

    style.configure("Banner.TFrame", background=INK)
    style.configure("Banner.TLabel", background=INK, foreground=PAPER, font=("Segoe UI", 10))
    style.configure(
        "BannerStop.TButton",
        background=PAPER,
        foreground=DANGER,
        bordercolor=PAPER,
        lightcolor=PAPER,
        darkcolor=PAPER,
    )
    style.map(
        "BannerStop.TButton",
        background=[("pressed", SKY), ("active", SKY)],
        foreground=[("pressed", DANGER), ("active", DANGER)],
    )

    def _paint_toplevel(event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, tk.Toplevel):
            try:
                widget.configure(bg=FIELD)
            except tk.TclError:
                pass

    root.bind_class("Toplevel", "<Map>", _paint_toplevel, add="+")
    return style
