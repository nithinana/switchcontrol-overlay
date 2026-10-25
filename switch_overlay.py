#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Switch Touch Overlay
"""
import sys
import os
import math
import argparse
import traceback
import atexit
import json
import time

from PIL import Image, ImageDraw, ImageFont

# =============================================================================
#  SETTINGS YOU MAY WANT TO CHANGE
# =============================================================================
OPACITY = 0.85      # 0.2 = very faint ... 1.0 = solid
SCALE = 1.0         # button size: 0.7 = smaller ... 1.15 = biggest that still fits
MAX_SCALE = 1.15

# Thumbsticks follow your thumb (like phone games): touch anywhere near a stick and it
# centres under your finger, follows if you drift, and snaps home when you let go.
# You can also switch this on/off with the FLOAT button in edit mode.
FLOATING_STICKS = True
FLOAT_ZONE = 1.75        # how far from a stick (x its radius) a touch still grabs it
K_MIN, K_MAX = 0.5, 1.8  # smallest / biggest size you can resize a control to
EDIT_VEIL = 95           # darkness of the screen tint while editing (0-255)

# Which keyboard key each on-screen control presses.
# These match Ryujinx's default keyboard profile as far as I know - if your
# Ryujinx shows different keys under Settings > Input, either change them in
# Ryujinx or change the values here. Allowed values: A-Z, 0-9, Up, Down, Left,
# Right, Plus (the = key), Minus (the - key), Enter, Space, Esc.
KEYS = {
    # left stick (digital, 8 directions)
    "lstick_up": "W", "lstick_down": "S", "lstick_left": "A", "lstick_right": "D",
    # right stick (digital, 8 directions)
    "rstick_up": "I", "rstick_down": "K", "rstick_left": "J", "rstick_right": "L",
    # d-pad
    "dpad_up": "Up", "dpad_down": "Down", "dpad_left": "Left", "dpad_right": "Right",
    # face buttons
    "btn_A": "Z", "btn_B": "X", "btn_X": "C", "btn_Y": "V",
    # shoulders / triggers
    "btn_L": "E", "btn_ZL": "Q", "btn_R": "U", "btn_ZR": "O",
    # centre buttons
    "btn_plus": "Plus", "btn_minus": "Minus",
}

# Colours (R, G, B). Opacity is applied on top of these.
BODY = (20, 22, 28)       # button fill
WHITE = (255, 255, 255)   # ring, labels, pressed fill
HALO_ALPHA = 4            # nearly invisible catch-area around buttons (keep > 0)

# =============================================================================
#  KEY CODES (hardware scancodes, so games see "real" key presses)
# =============================================================================
SCANCODES = {}


def _row(chars, start):
    for i, c in enumerate(chars):
        SCANCODES[c] = (start + i, False)


_row("QWERTYUIOP", 0x10)
_row("ASDFGHJKL", 0x1E)
_row("ZXCVBNM", 0x2C)
_row("1234567890", 0x02)
SCANCODES.update({
    "Minus": (0x0C, False), "Plus": (0x0D, False),
    "Enter": (0x1C, False), "Space": (0x39, False), "Esc": (0x01, False),
    "Up": (0x48, True), "Down": (0x50, True), "Left": (0x4B, True), "Right": (0x4D, True),
})

# =============================================================================
#  DRAWING HELPERS  (3x supersampled for smooth, clean edges)
# =============================================================================
SS = 3
_FONTS = {}
_FONT_CANDIDATES = [
    "seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf",
]


def get_font(px):
    px = max(8, int(px))
    f = _FONTS.get(px)
    if f is not None:
        return f
    for name in _FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(name, px)
            break
        except Exception:
            f = None
    if f is None:
        try:
            f = ImageFont.load_default(size=px)
        except TypeError:
            f = ImageFont.load_default()
    _FONTS[px] = f
    return f


def C(rgb, alpha):
    """Colour with alpha, scaled by the global OPACITY."""
    return (rgb[0], rgb[1], rgb[2], int(max(0, min(255, alpha * OPACITY))))


class Sprite:
    """A small transparent picture we draw shapes on (coordinates in real pixels)."""

    def __init__(self, w, h, ss=None):
        self.w, self.h = int(w), int(h)
        self.ss = ss or SS
        self.im = Image.new("RGBA", (self.w * self.ss, self.h * self.ss), (0, 0, 0, 0))

    def _layer(self, fn):
        layer = Image.new("RGBA", self.im.size, (0, 0, 0, 0))
        fn(ImageDraw.Draw(layer))
        self.im = Image.alpha_composite(self.im, layer)

    def circle(self, cx, cy, r, fill, ring=None, ring_w=2.0):
        def f(d):
            box = [(cx - r) * self.ss, (cy - r) * self.ss, (cx + r) * self.ss, (cy + r) * self.ss]
            if ring is not None:
                d.ellipse(box, fill=fill, outline=ring, width=max(1, int(ring_w * self.ss)))
            else:
                d.ellipse(box, fill=fill)
        self._layer(f)

    def pill(self, cx, cy, w, h, fill, ring=None, ring_w=2.0):
        def f(d):
            box = [(cx - w / 2) * self.ss, (cy - h / 2) * self.ss, (cx + w / 2) * self.ss, (cy + h / 2) * self.ss]
            if ring is not None:
                d.rounded_rectangle(box, radius=h / 2 * self.ss, fill=fill, outline=ring,
                                    width=max(1, int(ring_w * self.ss)))
            else:
                d.rounded_rectangle(box, radius=h / 2 * self.ss, fill=fill)
        self._layer(f)

    def poly(self, pts, fill):
        self._layer(lambda d: d.polygon([(x * self.ss, y * self.ss) for x, y in pts], fill=fill))

    def line(self, x0, y0, x1, y1, width, fill):
        def f(d):
            d.line([(x0 * self.ss, y0 * self.ss), (x1 * self.ss, y1 * self.ss)], fill=fill, width=max(1, int(width * self.ss)))
            r = width * self.ss / 2.0           # round caps
            for (x, y) in ((x0, y0), (x1, y1)):
                d.ellipse([x * self.ss - r, y * self.ss - r, x * self.ss + r, y * self.ss + r], fill=fill)
        self._layer(f)

    def text(self, cx, cy, txt, size, fill):
        def f(d):
            font = get_font(size * self.ss)
            try:
                d.text((cx * self.ss, cy * self.ss), txt, font=font, fill=fill, anchor="mm")
            except Exception:
                d.text((cx * self.ss - size * len(txt) * 0.3 * self.ss, cy * self.ss - size * 0.5 * self.ss),
                       txt, font=font, fill=fill)
        self._layer(f)

    def out(self):
        return self.im.resize((self.w, self.h), Image.LANCZOS)


def draw_label(sp, cx, cy, r, label, fg):
    """Letters are text; plus / minus / x are drawn as crisp lines."""
    if label == "plus":
        sp.line(cx - 0.42 * r, cy, cx + 0.42 * r, cy, r * 0.17, fg)
        sp.line(cx, cy - 0.42 * r, cx, cy + 0.42 * r, r * 0.17, fg)
    elif label == "minus":
        sp.line(cx - 0.42 * r, cy, cx + 0.42 * r, cy, r * 0.17, fg)
    elif label == "x":
        sp.line(cx - 0.32 * r, cy - 0.32 * r, cx + 0.32 * r, cy + 0.32 * r, r * 0.15, fg)
        sp.line(cx - 0.32 * r, cy + 0.32 * r, cx + 0.32 * r, cy - 0.32 * r, r * 0.15, fg)
    else:
        sp.text(cx, cy, label, r * 0.95, fg)


def draw_round_button(sp, cx, cy, r, label, pressed):
    rr = r * 0.95 if pressed else r
    fill = C(WHITE, 235) if pressed else C(BODY, 150)
    ring = C(WHITE, 255 if pressed else 205)
    sp.circle(cx, cy, rr, fill, ring, max(2.0, r * 0.045))
    fg = C(BODY, 255) if pressed else C(WHITE, 245)
    draw_label(sp, cx, cy, rr, label, fg)


# =============================================================================
#  WIDGETS  (each one is a touch area + the pictures for its states)
# =============================================================================
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Widget:
    slide = False        # a finger sliding in from elsewhere can press it
    sticky = False       # once touched, the finger stays attached (sticks)
    exclusive = False    # only one finger at a time
    group = "main"       # "main" = normal, "hidden" = only when hidden, "edit" = edit toolbar
    action = None        # "hide" / "show" / "quit" / "edit" ... for utility buttons
    idle = False         # state when nothing is touching it
    wid = None           # name used in the saved layout file
    custom = False       # True = can be moved / resized in edit mode
    floating = False

    def __init__(self):
        self._cache = {}
        self.x0 = self.y0 = self.w = self.h = 0
        self.dx = self.dy = 0            # drawing offset (used by floating sticks)
        self.size_k = 1.0                # size multiplier chosen in edit mode
        self.stale = False               # True while pictures are only stretched (live pinch)
        self._live = False
        self.hx0 = self.hy0 = 0.0        # half size of the visible part at size 1.0
        self.bounds = None               # (screen width, screen height)
        self.default = None              # (cx, cy, k) the factory layout

    def _box(self, cx, cy, hw, hh):
        self.x0 = int(round(cx - hw))
        self.y0 = int(round(cy - hh))
        self.w = int(round(2 * hw))
        self.h = int(round(2 * hh))

    def _set_size(self, k):
        if abs(k - self.size_k) > 1e-9:
            if self._live:
                self.stale = True        # keep the old pictures and just stretch them for now
            else:
                self._cache.clear()      # pictures have to be redrawn at the new size
                self.stale = False
        self.size_k = k

    def crisp(self):
        """Throw away stretched pictures so they get redrawn sharply at the real size."""
        if self.stale:
            self._cache.clear()
            self.stale = False

    # ---- placing / resizing (edit mode) ----
    def fit(self, cx, cy, k):
        """Nudge the centre so the visible part stays on screen."""
        if self.bounds:
            W, H = self.bounds
            hx, hy = self.hx0 * k, self.hy0 * k
            cx = _clamp(cx, hx, max(hx, W - hx))
            cy = _clamp(cy, hy, max(hy, H - hy))
        return cx, cy

    def place(self, cx, cy, k=None, live=False):
        k = _clamp(self.size_k if k is None else k, K_MIN, K_MAX)
        cx, cy = self.fit(cx, cy, k)
        self._live = live
        try:
            self.layout(cx, cy, k)
        finally:
            self._live = False

    # ---- geometry helpers ----
    def hit_box(self):
        """Normal touch area (x0, y0, x1, y1), without the extra floating zone."""
        return (self.x0, self.y0, self.x0 + self.w, self.y0 + self.h)

    def visual_box(self):
        """(x0, y0, x1, y1) of what is actually drawn, without the invisible catch-area."""
        return self.vbox

    def edit_hit(self, x, y, pad=12):
        v = self.vbox
        return v[0] - pad <= x <= v[2] + pad and v[1] - pad <= y <= v[3] + pad

    # ---- floating-stick hooks (only DirWidget does anything) ----
    def float_hit(self, x, y):
        return None

    def set_floating(self, flag):
        self.floating = flag

    def begin(self, x, y, floating):
        pass

    def follow(self, x, y, floating):
        pass

    def end(self):
        pass

    # ---- pictures ----
    def all_states(self):
        return [False, True]

    def sprite(self, state):
        im = self._cache.get(state)
        if im is None:
            im = self._cache[state] = self.render(state)
        return im

    def prerender(self):
        for st in self.all_states():
            self.sprite(st)


class CircleButton(Widget):
    def __init__(self, cx, cy, r, label, key=None, group="main", action=None, wid=None):
        super().__init__()
        self.base_r = r
        self.label, self.key, self.group, self.action = label, key, group, action
        self.slide = action is None
        self.wid, self.custom = wid, wid is not None
        self.hx0 = self.hy0 = r
        self.layout(cx, cy, 1.0)

    def layout(self, cx, cy, k=1.0):
        self._set_size(k)
        self.cx, self.cy = cx, cy
        self.r = self.base_r * k
        self.hr = self.r * 1.3
        self._box(cx, cy, self.hr, self.hr)
        self.vbox = (cx - self.r, cy - self.r, cx + self.r, cy + self.r)

    def hit(self, x, y):
        return 0 if math.hypot(x - self.cx, y - self.cy) <= self.hr else None

    def state_from(self, bound):
        return True

    def keys(self, state):
        return {self.key} if (state and self.key) else set()

    def render(self, pressed):
        sp = Sprite(self.w, self.h)
        lx, ly = self.cx - self.x0, self.cy - self.y0
        sp.circle(lx, ly, self.hr, (0, 0, 0, HALO_ALPHA))
        draw_round_button(sp, lx, ly, self.r, self.label, pressed)
        return sp.out()


class PillButton(Widget):
    def __init__(self, cx, cy, w, h, label, key=None, group="main", action=None, wid=None):
        super().__init__()
        self.base_w, self.base_h = w, h
        self.label, self.key, self.group, self.action = label, key, group, action
        self.slide = action is None
        self.wid, self.custom = wid, wid is not None
        self.hx0, self.hy0 = w / 2, h / 2
        self.layout(cx, cy, 1.0)

    def layout(self, cx, cy, k=1.0):
        self._set_size(k)
        self.cx, self.cy = cx, cy
        self.pw, self.ph = self.base_w * k, self.base_h * k
        self.m = self.ph * 0.3
        w, h = self.pw, self.ph
        self._box(cx, cy, w / 2 + self.m, h / 2 + self.m)
        self.vbox = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def set_label(self, label):
        if label != self.label:
            self.label = label
            self._cache.clear()

    def hit(self, x, y):
        inside = abs(x - self.cx) <= self.pw / 2 + self.m and abs(y - self.cy) <= self.ph / 2 + self.m
        return 0 if inside else None

    def state_from(self, bound):
        return True

    def keys(self, state):
        return {self.key} if (state and self.key) else set()

    def render(self, pressed):
        sp = Sprite(self.w, self.h)
        lx, ly = self.cx - self.x0, self.cy - self.y0
        sp.pill(lx, ly, self.pw + 2 * self.m, self.ph + 2 * self.m, (0, 0, 0, HALO_ALPHA))
        w, h = (self.pw * 0.96, self.ph * 0.92) if pressed else (self.pw, self.ph)
        fill = C(WHITE, 235) if pressed else C(BODY, 150)
        ring = C(WHITE, 255 if pressed else 205)
        sp.pill(lx, ly, w, h, fill, ring, max(2.0, self.ph * 0.045))
        fg = C(BODY, 255) if pressed else C(WHITE, 245)
        sp.text(lx, ly, self.label, self.ph * 0.46, fg)
        return sp.out()


class Cluster(Widget):
    """The four face buttons, laid out like a Switch: X top, A right, B bottom, Y left."""
    slide = True
    PARTS = (("X", 0, -1), ("A", 1, 0), ("B", 0, 1), ("Y", -1, 0))

    def __init__(self, cx, cy, br, spacing, wid=None):
        super().__init__()
        self.base_br, self.base_d = br, spacing
        self.wid, self.custom = wid, wid is not None
        self.hx0 = self.hy0 = spacing + br
        self.layout(cx, cy, 1.0)

    def layout(self, cx, cy, k=1.0):
        self._set_size(k)
        self.cx, self.cy = cx, cy
        self.br, self.d = self.base_br * k, self.base_d * k
        self.hr = self.br * 1.3
        ext = self.d + self.hr
        self._box(cx, cy, ext, ext)
        vext = self.d + self.br
        self.vbox = (cx - vext, cy - vext, cx + vext, cy + vext)
        self.centres = {n: (cx + dx * self.d, cy + dy * self.d) for n, dx, dy in self.PARTS}

    def hit(self, x, y):
        best, best_d = None, None
        for name, (px, py) in self.centres.items():
            dist = math.hypot(x - px, y - py)
            if dist <= self.hr and (best_d is None or dist < best_d):
                best, best_d = name, dist
        return best

    def state_from(self, bound):
        down = {part for part, _, _ in bound}
        return tuple(n in down for n, _, _ in self.PARTS)

    def keys(self, state):
        return {KEYS["btn_" + n] for (n, _, _), on in zip(self.PARTS, state) if on}

    def all_states(self):
        return [tuple(bool(i >> b & 1) for b in range(4)) for i in range(16)]

    @property
    def idle(self):
        return (False, False, False, False)

    def render(self, state):
        sp = Sprite(self.w, self.h)
        for n, dx, dy in self.PARTS:
            px, py = self.centres[n]
            sp.circle(px - self.x0, py - self.y0, self.hr, (0, 0, 0, HALO_ALPHA))
        for (n, dx, dy), on in zip(self.PARTS, state):
            px, py = self.centres[n]
            draw_round_button(sp, px - self.x0, py - self.y0, self.br, n, on)
        return sp.out()


class DirWidget(Widget):
    """A thumbstick or a d-pad. Both give 8 directions as key presses.

    Thumbsticks can be "floating": the stick re-centres wherever your thumb lands
    (anywhere in a generous zone around it), follows your thumb if you drift, and
    jumps back to its home position when you let go.
    """
    sticky = True
    exclusive = True

    def __init__(self, cx, cy, r, mode, keys, wid=None):
        super().__init__()
        self.base_r, self.mode = r, mode
        self.keymap = keys                  # dict up/down/left/right -> key name
        self.dead = 0.30 if mode == "stick" else 0.18
        self.wid, self.custom = wid, wid is not None
        self.hx0 = self.hy0 = r
        self.layout(cx, cy, 1.0)

    def layout(self, cx, cy, k=1.0):
        self._set_size(k)
        self.cx, self.cy = cx, cy
        self.r = self.base_r * k
        self.hr = self.r * 1.3
        self.zr = self.r * FLOAT_ZONE if (self.mode == "stick" and self.floating) else self.hr
        self._box(cx, cy, self.zr, self.zr)
        self.vbox = (cx - self.r, cy - self.r, cx + self.r, cy + self.r)
        self.ox, self.oy, self.dx, self.dy = cx, cy, 0, 0

    def set_floating(self, flag):
        if flag != self.floating:
            self.floating = flag
            self._cache.clear()            # the invisible catch-area changes size
            self.layout(self.cx, self.cy, self.size_k)

    @property
    def idle(self):
        return (0, 0)

    def hit_box(self):
        return (self.cx - self.hr, self.cy - self.hr, self.cx + self.hr, self.cy + self.hr)

    def hit(self, x, y):
        return 0 if math.hypot(x - self.cx, y - self.cy) <= self.hr else None

    def float_hit(self, x, y):
        if self.floating and self.mode == "stick" and math.hypot(x - self.cx, y - self.cy) <= self.zr:
            return 0
        return None

    # ---- floating behaviour ----
    def _set_origin(self, x, y):
        if self.bounds:                        # centre stays on screen; the ring may hang off the edge a bit
            W, H = self.bounds                 # so a thumb right at the edge still starts out neutral
            x = _clamp(x, 0, W)
            y = _clamp(y, 0, H)
        self.ox, self.oy = x, y
        self.dx, self.dy = int(round(x - self.cx)), int(round(y - self.cy))

    def begin(self, x, y, floating):
        if floating and self.mode == "stick":
            self._set_origin(x, y)             # centre the stick under the thumb
        else:
            self.end()

    def follow(self, x, y, floating):
        if not (floating and self.mode == "stick"):
            return
        dx, dy = x - self.ox, y - self.oy
        dist = math.hypot(dx, dy)
        if dist > self.r:                      # thumb drifted out: drag the stick along
            pull = (dist - self.r) / dist
            self._set_origin(self.ox + dx * pull, self.oy + dy * pull)

    def end(self):
        self.ox, self.oy, self.dx, self.dy = self.cx, self.cy, 0, 0

    def direction(self, x, y):
        dx, dy = x - self.ox, y - self.oy
        dist = math.hypot(dx, dy)
        if dist < self.dead * self.r:
            return (0, 0)
        nx, ny = dx / dist, dy / dist
        sx = 1 if nx > 0.38 else (-1 if nx < -0.38 else 0)
        sy = 1 if ny > 0.38 else (-1 if ny < -0.38 else 0)
        return (sx, sy)

    def state_from(self, bound):
        _, x, y = bound[0]
        return self.direction(x, y)

    def keys(self, state):
        sx, sy = state
        out = set()
        if sx < 0: out.add(self.keymap["left"])
        if sx > 0: out.add(self.keymap["right"])
        if sy < 0: out.add(self.keymap["up"])
        if sy > 0: out.add(self.keymap["down"])
        return out

    def all_states(self):
        return [(sx, sy) for sx in (-1, 0, 1) for sy in (-1, 0, 1)]

    def render(self, state):
        sp = Sprite(self.w, self.h)
        lx, ly = self.cx - self.x0, self.cy - self.y0
        r = self.r
        sx, sy = state
        active = state != (0, 0)
        sp.circle(lx, ly, self.zr, (0, 0, 0, HALO_ALPHA))
        sp.circle(lx, ly, r, C(BODY, 130), C(WHITE, 175), max(2.0, r * 0.03))
        if self.mode == "stick":
            norm = math.hypot(sx, sy) or 1.0
            kx, ky = lx + 0.50 * r * sx / norm, ly + 0.50 * r * sy / norm
            kr = r * 0.46
            if active:
                sp.circle(kx, ky, kr, C(WHITE, 215), C(WHITE, 255), max(2.0, r * 0.03))
            else:
                sp.circle(kx, ky, kr, C(WHITE, 60), C(WHITE, 205), max(2.0, r * 0.03))
        else:
            for name, (ux, uy) in (("up", (0, -1)), ("down", (0, 1)),
                                   ("left", (-1, 0)), ("right", (1, 0))):
                on = (ux and ux == sx) or (uy and uy == sy)
                tip, base, half = 0.74 * r, 0.30 * r, 0.25 * r
                px, py = -uy, ux            # perpendicular
                pts = [(lx + ux * tip, ly + uy * tip),
                       (lx + ux * base + px * half, ly + uy * base + py * half),
                       (lx + ux * base - px * half, ly + uy * base - py * half)]
                sp.poly(pts, C(WHITE, 255) if on else C(WHITE, 150))
            sp.circle(lx, ly, r * 0.07, C(WHITE, 130))
        return sp.out()


# =============================================================================
#  LAYOUT
# =============================================================================
def build_widgets(W, H, scale=1.0):
    scale = max(0.5, min(MAX_SCALE, scale))
    R = 0.125 * H * scale          # thumbstick radius
    Rd = 0.115 * H * scale         # d-pad radius
    br = 0.058 * H * scale         # face button radius
    D = 1.9 * br                   # face button spacing
    lx = max(0.03 * W + 1.1 * max(R, Rd), 1.35 * R, 3.25 * br)   # keep catch-areas on screen
    rx = W - lx
    top_y = 0.455 * H
    bot_y = min(0.815 * H, H - 1.31 * max(R, Rd))

    k = KEYS
    widgets = [
        DirWidget(lx, top_y, R, "stick", {"up": k["lstick_up"], "down": k["lstick_down"],
                                          "left": k["lstick_left"], "right": k["lstick_right"]}, wid="lstick"),
        DirWidget(lx, bot_y, Rd, "dpad", {"up": k["dpad_up"], "down": k["dpad_down"],
                                          "left": k["dpad_left"], "right": k["dpad_right"]}, wid="dpad"),
        Cluster(rx, top_y, br, D, wid="face"),
        DirWidget(rx, bot_y, R, "stick", {"up": k["rstick_up"], "down": k["rstick_down"],
                                          "left": k["rstick_left"], "right": k["rstick_right"]}, wid="rstick"),
    ]
    pw, ph = 2.1 * R, 0.07 * H * scale
    y_zl = 0.075 * H
    y_l = y_zl + ph * 1.75      # enough room that the two catch-areas don't touch
    widgets += [
        PillButton(lx, y_zl, pw, ph, "ZL", k["btn_ZL"], wid="zl"),
        PillButton(lx, y_l, pw, ph, "L", k["btn_L"], wid="l"),
        PillButton(rx, y_zl, pw, ph, "ZR", k["btn_ZR"], wid="zr"),
        PillButton(rx, y_l, pw, ph, "R", k["btn_R"], wid="r"),
    ]
    cr = 0.035 * H * scale
    widgets += [
        CircleButton(W / 2 - 0.085 * H, 0.925 * H, cr, "minus", k["btn_minus"], wid="minus"),
        CircleButton(W / 2 + 0.085 * H, 0.925 * H, cr, "plus", k["btn_plus"], wid="plus"),
    ]
    hw, hh = 0.19 * H, 0.045 * H
    widgets += [
        PillButton(W / 2, 0.045 * H, hw, hh, "HIDE", None, "main", "hide"),
        PillButton(W / 2 + 0.195 * H, 0.045 * H, 0.11 * H, hh, "EDIT", None, "main", "edit"),
        PillButton(W / 2, 0.045 * H, hw, hh, "SHOW", None, "hidden", "show"),
        CircleButton(W / 2 + hw / 2 + 0.07 * H, 0.045 * H, 0.03 * H, "x", None, "hidden", "quit"),
    ]
    # ---- the edit-mode toolbar:  [-] [+] [FLOAT] [RESET] [DONE]
    gap, tr, th = 0.035 * H, 0.03 * H, 0.045 * H
    items = [("c", "minus", "size_down", 2 * tr), ("c", "plus", "size_up", 2 * tr),
             ("p", "FLOAT ON", "float", 0.17 * H), ("p", "RESET", "reset", 0.12 * H),
             ("p", "DONE", "done", 0.12 * H)]
    x = W / 2 - (sum(i[3] for i in items) + gap * (len(items) - 1)) / 2
    for kind, label, act, wd in items:
        if kind == "c":
            widgets.append(CircleButton(x + wd / 2, 0.05 * H, tr, label, None, "edit", act))
        else:
            widgets.append(PillButton(x + wd / 2, 0.05 * H, wd, th, label, None, "edit", act))
        x += wd + gap
    for w in widgets:
        w.bounds = (W, H)
        w.default = (w.cx, w.cy, 1.0)
    return widgets


# =============================================================================
#  TOUCH LOGIC  (no Windows code in here, so it can be tested anywhere)
# =============================================================================
NICE_NAMES = {"lstick": "Left stick", "dpad": "D-pad", "face": "A B X Y buttons",
              "rstick": "Right stick", "zl": "ZL", "l": "L", "zr": "ZR", "r": "R",
              "minus": "Minus", "plus": "Plus"}


class Controller:
    def __init__(self, widgets, send_key, request_redraw, on_action=None, on_save=None,
                 floating=FLOATING_STICKS):
        self.widgets = widgets
        self.send_key = send_key
        self.request_redraw = request_redraw
        self.on_action = on_action or (lambda a: None)
        self.on_save = on_save or (lambda c: None)
        self.states = {w: w.idle for w in widgets}
        self.offs = {w: (0, 0) for w in widgets}
        self.down_keys = set()
        self.ptr = {}                  # pointer id -> [widget or None, part, x, y]
        self.mode = "main"             # "main" | "hidden" | "edit"
        self.floating = bool(floating)
        self.selected = None           # widget chosen in edit mode
        self.eptr = {}                 # edit mode: pointer id -> [widget, grab dx, grab dy, x, y]
        self.pinch = None              # (pid_a, pid_b, start distance, start size, widget)
        self.warm = []                 # pictures still to be drawn in idle time
        for w in widgets:
            w.set_floating(self.floating)
        self._sync_float_label()

    @property
    def hidden(self):
        return self.mode == "hidden"

    def visible(self, w):
        if self.mode == "edit":
            return w.group == "edit" or w.custom
        if w.group == "edit":
            return False
        return w.group == ("hidden" if self.mode == "hidden" else "main")

    def _hit(self, x, y, slide_only=False, extended=False):
        for w in self.widgets:
            if not self.visible(w) or (slide_only and not w.slide):
                continue
            part = w.hit(x, y)
            if part is not None:
                return w, part
        if extended and self.floating:        # the roomy floating-stick zone never steals from real buttons
            for w in self.widgets:
                if self.visible(w):
                    part = w.float_hit(x, y)
                    if part is not None:
                        return w, part
        return None

    # ------------------------------------------------------------ touches
    def down(self, pid, x, y):
        if self.mode == "edit":
            return self._edit_down(pid, x, y)
        found = self._hit(x, y, extended=True)
        if found is None:
            return False
        w, part = found
        if w.action:
            self._do_action(w.action)
            return True
        if w.exclusive and any(r[0] is w for r in self.ptr.values()):
            return True
        self.ptr[pid] = [w, part, x, y]
        w.begin(x, y, self.floating)
        self._refresh()
        return True

    def move(self, pid, x, y):
        if self.mode == "edit":
            return self._edit_move(pid, x, y)
        rec = self.ptr.get(pid)
        if rec is None:
            return
        if rec[0] is not None and rec[0].sticky:
            rec[2], rec[3] = x, y
            rec[0].follow(x, y, self.floating)
        else:
            found = self._hit(x, y, slide_only=True)
            rec[0], rec[1] = found if found else (None, None)
            rec[2], rec[3] = x, y
        self._refresh()

    def up(self, pid):
        if self.mode == "edit":
            return self._edit_up(pid)
        rec = self.ptr.pop(pid, None)
        if rec is not None:
            if rec[0] is not None:
                rec[0].end()
            self._refresh()

    def _end_all(self):
        self.ptr.clear()
        for w in self.widgets:
            w.end()

    def set_mode(self, mode):
        for w in self.widgets:
            w.crisp()
        self._end_all()
        self.mode = mode
        self.selected = None
        self.eptr.clear()
        self.pinch = None
        self._refresh(force_redraw=True)

    def set_hidden(self, hidden):
        self.set_mode("hidden" if hidden else "main")

    def release_all(self):
        self._end_all()
        self._refresh()

    def set_floating(self, flag):
        self.floating = bool(flag)
        for w in self.widgets:
            w.set_floating(self.floating)
        self._sync_float_label()
        self._refresh(force_redraw=True)

    def _sync_float_label(self):
        for w in self.widgets:
            if w.action == "float":
                w.set_label("FLOAT ON" if self.floating else "FLOAT OFF")

    def _refresh(self, force_redraw=False):
        bound = {}
        for w, part, x, y in self.ptr.values():
            if w is not None:
                bound.setdefault(w, []).append((part, x, y))
        changed = force_redraw
        keys = set()
        for w in self.widgets:
            b = bound.get(w)
            st = w.state_from(b) if b else w.idle
            if st != self.states[w]:
                self.states[w] = st
                changed = True
            off = (w.dx, w.dy)
            if off != self.offs[w]:
                self.offs[w] = off
                changed = True
            if self.visible(w):
                keys |= w.keys(st)
        for key in keys - self.down_keys:         # keys first, picture second
            self.send_key(key, True)
        for key in self.down_keys - keys:
            self.send_key(key, False)
        self.down_keys = keys
        if changed:
            self.request_redraw()

    # ------------------------------------------------------------ buttons that change modes
    def _do_action(self, a):
        if a == "edit":
            self.set_mode("edit")
        elif a == "done":
            self.finish_edit()
        elif a == "reset":
            self.reset_layout()
        elif a == "size_up":
            self.nudge_size(1)
        elif a == "size_down":
            self.nudge_size(-1)
        elif a == "float":
            self.set_floating(not self.floating)
        else:
            self.on_action(a)

    # ------------------------------------------------------------ edit mode
    def edit_pick(self, x, y):
        cands = [w for w in self.widgets if w.custom and w.edit_hit(x, y)]
        if not cands:
            return None
        if self.selected in cands:
            return self.selected
        return min(cands, key=lambda w: math.hypot(x - w.cx, y - w.cy))

    def _edit_down(self, pid, x, y):
        for w in self.widgets:                          # toolbar first
            if w.group == "edit" and w.action and w.hit(x, y) is not None:
                self._do_action(w.action)
                return True
        w = self.edit_pick(x, y)
        if w is None or pid in self.eptr:
            return True                                  # the dark veil swallows stray touches
        mate = next((p for p, r in self.eptr.items() if r[0] is w), None)
        if mate is not None and self.pinch is None:      # second finger on the same control = pinch
            r = self.eptr[mate]
            d0 = max(20.0, math.hypot(x - r[3], y - r[4]))
            self.eptr[pid] = [w, 0, 0, x, y]
            self.pinch = (mate, pid, d0, w.size_k, w)
            return True
        self.selected = w
        self.eptr[pid] = [w, w.cx - x, w.cy - y, x, y]
        self.request_redraw()
        return True

    def _edit_move(self, pid, x, y):
        r = self.eptr.get(pid)
        if r is None:
            return
        r[3], r[4] = x, y
        if self.pinch is not None and pid in self.pinch[:2]:
            a, b, d0, k0, w = self.pinch
            pa, pb = self.eptr.get(a), self.eptr.get(b)
            if pa and pb:
                d = math.hypot(pa[3] - pb[3], pa[4] - pb[4])
                k = _clamp(k0 * d / d0, K_MIN, K_MAX)
                if abs(k - w.size_k) > 0.004:
                    w.place(w.cx, w.cy, k, live=True)
                    self.request_redraw()
            return
        w = r[0]
        w.place(x + r[1], y + r[2])
        self.request_redraw()

    def _edit_up(self, pid):
        self.eptr.pop(pid, None)
        if self.pinch is not None and pid in self.pinch[:2]:
            other = self.pinch[1] if pid == self.pinch[0] else self.pinch[0]
            self.pinch[4].crisp()                        # redraw sharply at the final size
            self.pinch = None
            self.request_redraw()
            ro = self.eptr.get(other)
            if ro:                                       # the remaining finger carries on dragging
                ro[1], ro[2] = ro[0].cx - ro[3], ro[0].cy - ro[4]

    def nudge_size(self, sign):
        w = self.selected
        if w is None:
            return
        w.place(w.cx, w.cy, w.size_k * (1.1 ** sign))
        self.request_redraw()

    def reset_layout(self):
        for w in self.widgets:
            if w.custom and w.default:
                w.layout(*w.default)
        self.request_redraw()

    def finish_edit(self):
        self.set_mode("main")
        self.warm = [(w, s) for w in self.widgets if w.custom
                     for s in w.all_states() if s not in w._cache]
        self.on_save(self)

    def warm_step(self):
        """Draw one missing picture (called from a timer so nothing freezes)."""
        while self.warm:
            w, s = self.warm.pop(0)
            if s not in w._cache:
                w.sprite(s)
                return True
        return False

    # ------------------------------------------------------------ saving / loading the layout
    def export_layout(self, W, H):
        return {"version": 1, "floating": bool(self.floating),
                "widgets": {w.wid: {"x": round(w.cx / W, 5), "y": round(w.cy / H, 5),
                                    "k": round(w.size_k, 4)}
                            for w in self.widgets if w.custom}}

    def import_layout(self, data, W, H):
        if not isinstance(data, dict):
            return False
        if isinstance(data.get("floating"), bool):
            self.set_floating(data["floating"])
        items = data.get("widgets")
        if not isinstance(items, dict):
            return False
        by = {w.wid: w for w in self.widgets if w.custom}
        for wid, d in items.items():
            w = by.get(wid)
            if w is None:
                continue
            try:
                x, y, k = float(d["x"]), float(d["y"]), float(d.get("k", 1.0))
            except Exception:
                continue
            if not all(math.isfinite(v) for v in (x, y, k)):
                continue
            w.place(_clamp(x, 0.0, 1.0) * W, _clamp(y, 0.0, 1.0) * H, k)
        self._refresh(force_redraw=True)
        return True


def blit(canvas, sprite, x, y):
    W, H = canvas.size
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(sprite.width, W - x), min(sprite.height, H - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    canvas.alpha_composite(sprite.crop((sx0, sy0, sx1, sy1)), (x + sx0, y + sy0))


_RINGS = {}


def _ring(bw, bh, selected):
    key = (bw, bh, selected)
    im = _RINGS.get(key)
    if im is None:
        if len(_RINGS) > 80:
            _RINGS.clear()
        sp = Sprite(bw, bh, ss=2)
        if selected:
            sp.pill(bw / 2, bh / 2, bw - 4, bh - 4, (255, 255, 255, 26), (255, 255, 255, 235), 3.0)
        else:
            sp.pill(bw / 2, bh / 2, bw - 4, bh - 4, (0, 0, 0, 0), (255, 255, 255, 80), 1.5)
        im = _RINGS[key] = sp.out()
    return im


def draw_edit_overlay(canvas, ctrl, W, H):
    pad = 10
    for w in ctrl.widgets:
        if ctrl.visible(w) and w.custom:
            x0, y0, x1, y1 = w.vbox
            ring = _ring(int(round(x1 - x0 + 2 * pad)), int(round(y1 - y0 + 2 * pad)), w is ctrl.selected)
            blit(canvas, ring, int(round(x0 - pad)), int(round(y0 - pad)))
    d = ImageDraw.Draw(canvas, "RGBA")
    f = get_font(H * 0.026)
    sel = ctrl.selected
    lines = ["Drag a control to move it  |  pinch it, or use  -  /  +  to resize",
             ("Selected: %s   (size %d%%)" % (NICE_NAMES.get(sel.wid, sel.wid), round(sel.size_k * 100)))
             if sel is not None else "Touch a control to select it"]
    for i, text in enumerate(lines):
        try:
            d.text((W / 2, H * (0.125 + 0.04 * i)), text, font=f, fill=(255, 255, 255, 235), anchor="mm")
        except Exception:
            d.text((W / 2 - 200, H * (0.125 + 0.04 * i)), text, font=f, fill=(255, 255, 255, 235))


def compose(ctrl, W, H):
    editing = ctrl.mode == "edit"
    # in edit mode a faint veil covers the screen so stray touches can't reach the game
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, EDIT_VEIL if editing else 0))
    for w in ctrl.widgets:
        if ctrl.visible(w):
            im = w.sprite(ctrl.states[w])
            if w.stale and im.size != (w.w, w.h):          # mid-pinch: stretch the old picture
                im = im.resize((max(1, w.w), max(1, w.h)), Image.BILINEAR)
            blit(canvas, im, w.x0 + w.dx, w.y0 + w.dy)
    if editing:
        draw_edit_overlay(canvas, ctrl, W, H)
    return canvas


def to_premultiplied_bgra(canvas):
    """Windows layered windows want premultiplied BGRA bytes."""
    try:
        return canvas.convert("RGBa").tobytes("raw", "BGRa")
    except Exception:
        from PIL import ImageChops
        r, g, b, a = canvas.split()
        pm = Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                  ImageChops.multiply(b, a), a))
        return pm.tobytes("raw", "BGRA")


# =============================================================================
#  WINDOWS LAYER
# =============================================================================
_HERE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_HERE, "overlay_log.txt")      # next to the .exe (or the script)
LAYOUT_PATH = os.path.join(_HERE, "overlay_layout.json")   # your saved button positions / sizes


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def load_layout(path=None):
    """Saved layout as a dict, or None if there is none / it is unreadable."""
    try:
        with open(path or LAYOUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        log("could not read layout file:\n" + traceback.format_exc())
        return None


def save_layout(data, path=None):
    try:
        with open(path or LAYOUT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        log("could not save layout file:\n" + traceback.format_exc())
        return False


def run_overlay():
    import ctypes
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Pixel-exact coordinates (no Windows display scaling surprises)
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    LRESULT = ctypes.c_ssize_t
    ULONG_PTR = ctypes.c_size_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HANDLE), ("hIcon", wt.HANDLE),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    class POINTER_INFO(ctypes.Structure):
        _fields_ = [("pointerType", wt.DWORD), ("pointerId", wt.UINT), ("frameId", wt.UINT),
                    ("pointerFlags", wt.UINT), ("sourceDevice", wt.HANDLE), ("hwndTarget", wt.HWND),
                    ("ptPixelLocation", wt.POINT), ("ptHimetricLocation", wt.POINT),
                    ("ptPixelLocationRaw", wt.POINT), ("ptHimetricLocationRaw", wt.POINT),
                    ("dwTime", wt.DWORD), ("historyCount", wt.UINT), ("InputData", ctypes.c_int),
                    ("dwKeyStates", wt.DWORD), ("PerformanceCount", ctypes.c_uint64),
                    ("ButtonChangeType", wt.UINT)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]

    # ---- function signatures (needed so 64-bit handles don't get truncated)
    vp = ctypes.c_void_p
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HANDLE,
                                       wt.HANDLE, vp]
    user32.CreateWindowExW.restype = wt.HWND
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.GetPointerInfo.argtypes = [wt.UINT, ctypes.POINTER(POINTER_INFO)]
    user32.GetPointerInfo.restype = wt.BOOL
    user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, vp]
    user32.SetWindowPos.argtypes = [wt.HWND, vp, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wt.UINT]
    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HANDLE
    user32.LoadCursorW.argtypes = [wt.HANDLE, vp]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HANDLE, ctypes.POINTER(wt.POINT),
                                           ctypes.POINTER(wt.SIZE), wt.HANDLE,
                                           ctypes.POINTER(wt.POINT), wt.COLORREF,
                                           ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
    user32.UpdateLayeredWindow.restype = wt.BOOL
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wt.UINT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.SetWindowFeedbackSetting.argtypes = [wt.HWND, ctypes.c_int, wt.DWORD, wt.UINT, vp]
    user32.SetWindowFeedbackSetting.restype = wt.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wt.HANDLE]
    gdi32.CreateCompatibleDC.restype = wt.HANDLE
    gdi32.CreateDIBSection.argtypes = [wt.HANDLE, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                       ctypes.POINTER(vp), wt.HANDLE, wt.DWORD]
    gdi32.CreateDIBSection.restype = wt.HANDLE
    gdi32.SelectObject.argtypes = [wt.HANDLE, wt.HANDLE]
    gdi32.SelectObject.restype = wt.HANDLE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HANDLE

    # ---- constants
    WM_DESTROY, WM_MOUSEACTIVATE, WM_TIMER = 0x0002, 0x0021, 0x0113
    WM_POINTERUPDATE, WM_POINTERDOWN, WM_POINTERUP = 0x0245, 0x0246, 0x0247
    WM_POINTERACTIVATE, WM_POINTERCAPTURECHANGED = 0x024B, 0x024C
    WS_POPUP = 0x80000000
    WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x8, 0x80, 0x80000, 0x08000000
    SW_SHOWNOACTIVATE = 4
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8

    W = user32.GetSystemMetrics(0)
    H = user32.GetSystemMetrics(1)
    log("screen %dx%d" % (W, H))

    # ---- key sending
    def send_key(name, down):
        scan, ext = SCANCODES[name]
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0) | (0 if down else KEYEVENTF_KEYUP)
        inp = INPUT()
        inp.type = 1
        inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    # ---- window + drawing surface
    hwnd_holder = {}
    state = {}
    last = {"t": 0.0, "dirty": False, "ready": False}

    def draw():
        canvas = compose(ctrl, W, H)
        data = to_premultiplied_bgra(canvas)
        ctypes.memmove(state["bits"].value, data, len(data))
        user32.UpdateLayeredWindow(hwnd_holder["h"], state["screen_dc"], None, ctypes.byref(state["size"]),
                                   state["mem_dc"], ctypes.byref(state["origin"]), 0,
                                   ctypes.byref(state["blend"]), 2)
        last["t"] = time.perf_counter()
        last["dirty"] = False

    def request_redraw():
        """Redraw now, unless we just did (then a 16 ms timer catches up) - keeps dragging smooth."""
        if not last["ready"]:
            last["dirty"] = True
        elif time.perf_counter() - last["t"] >= 0.016:
            draw()
        else:
            last["dirty"] = True

    def on_action(action):
        if action == "hide":
            ctrl.set_hidden(True)
        elif action == "show":
            ctrl.set_hidden(False)
        elif action == "quit":
            user32.DestroyWindow(hwnd_holder["h"])

    widgets = build_widgets(W, H, SCALE)
    def on_save(c):
        save_layout(c.export_layout(W, H))

    ctrl = Controller(widgets, send_key, request_redraw, on_action, on_save)
    saved = load_layout()
    if saved:
        try:
            ctrl.import_layout(saved, W, H)
            log("loaded saved layout")
        except Exception:
            log("saved layout ignored:\n" + traceback.format_exc())

    def wndproc(hwnd, msg, wparam, lparam):
        try:
            if msg in (WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP, WM_POINTERCAPTURECHANGED):
                pid = wparam & 0xFFFF
                if msg in (WM_POINTERUP, WM_POINTERCAPTURECHANGED):
                    ctrl.up(pid)
                else:
                    info = POINTER_INFO()
                    if user32.GetPointerInfo(pid, ctypes.byref(info)):
                        x, y = info.ptPixelLocation.x, info.ptPixelLocation.y
                        if msg == WM_POINTERDOWN:
                            ctrl.down(pid, x, y)
                        else:
                            ctrl.move(pid, x, y)
                return 0
            if msg == WM_MOUSEACTIVATE:
                return 3                      # MA_NOACTIVATE: never steal focus from the game
            if msg == WM_POINTERACTIVATE:
                return 3                      # PA_NOACTIVATE
            if msg == WM_TIMER:
                if wparam == 2:                       # catch-up redraw
                    if last["dirty"] and last["ready"]:
                        draw()
                elif wparam == 3:                     # redraw pictures after an edit, one at a time
                    ctrl.warm_step()
                else:                                 # keep the overlay on top
                    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log("wndproc error:\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc_ref = WNDPROC(wndproc)          # keep a reference so it isn't garbage collected
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSW()
    cls.lpfnWndProc = wndproc_ref
    cls.hInstance = hinst
    cls.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
    cls.lpszClassName = "SwitchTouchOverlayWnd"
    if not user32.RegisterClassW(ctypes.byref(cls)):
        raise ctypes.WinError(ctypes.get_last_error())

    try:                                    # mouse acts like a finger too (handy for testing)
        user32.EnableMouseInPointer(True)
    except Exception:
        pass

    hwnd = user32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE,
                                  cls.lpszClassName, "Switch Touch Overlay", WS_POPUP,
                                  0, 0, W, H, None, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd_holder["h"] = hwnd

    # no white "touch ripple" / press-and-hold circle over our buttons
    try:
        off = ctypes.c_int(0)
        for feedback in (1, 7, 8, 9, 10):
            user32.SetWindowFeedbackSetting(hwnd, feedback, 0, ctypes.sizeof(off), ctypes.byref(off))
    except Exception:
        pass

    # drawing surface (a 32-bit bitmap we write into, then hand to Windows)
    state["screen_dc"] = user32.GetDC(None)
    state["mem_dc"] = gdi32.CreateCompatibleDC(state["screen_dc"])
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = W
    bmi.bmiHeader.biHeight = -H            # negative = top-down rows
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    state["bits"] = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(state["mem_dc"], ctypes.byref(bmi), 0, ctypes.byref(state["bits"]), None, 0)
    if not hbmp:
        raise ctypes.WinError(ctypes.get_last_error())
    gdi32.SelectObject(state["mem_dc"], hbmp)
    state["size"] = wt.SIZE(W, H)
    state["origin"] = wt.POINT(0, 0)
    state["blend"] = BLENDFUNCTION(0, 0, 255, 1)        # AC_SRC_OVER, AC_SRC_ALPHA

    for w in widgets:                       # draw every button picture once up front
        w.prerender()

    def shutdown():
        try:
            ctrl.release_all()
        except Exception:
            pass
    atexit.register(shutdown)

    last["ready"] = True
    draw()
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd, 1, 1000, None)    # keeps overlay on top; also lets Ctrl+C work
    user32.SetTimer(hwnd, 2, 16, None)      # catch-up redraws while dragging
    user32.SetTimer(hwnd, 3, 40, None)      # background drawing of re-sized buttons
    log("overlay running")

    msg = wt.MSG()
    try:
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        shutdown()


# =============================================================================
#  PREVIEW (works on any computer - just writes a picture)
# =============================================================================
def write_preview(path, W=1920, H=1080, mode="play"):
    ctrl = Controller(build_widgets(W, H, SCALE), lambda k, d: None, lambda: None)
    byname = {}
    for w in ctrl.widgets:
        byname.setdefault(type(w).__name__, []).append(w)
    dirs = byname["DirWidget"]
    if mode == "edit":
        face = byname["Cluster"][0]
        face.place(face.cx - 0.10 * W, face.cy - 0.02 * H, 1.25)      # pretend she dragged + enlarged it
        ctrl.set_mode("edit")
        ctrl.selected = face
    else:
        s = dirs[0]
        # left thumb lands off to the side of the stick -> the stick re-centres under it, then pushes up-right
        ctrl.down(2, s.cx - 0.9 * s.r, s.cy + 0.5 * s.r)
        ctrl.move(2, s.cx - 0.9 * s.r + 0.55 * s.r, s.cy + 0.5 * s.r - 0.55 * s.r)
        ctrl.down(1, dirs[1].cx + dirs[1].r * 0.6, dirs[1].cy)                 # d-pad right
        ctrl.down(3, *byname["Cluster"][0].centres["A"])                        # A
        pills = [p for p in byname["PillButton"] if p.label == "R"]
        ctrl.down(4, pills[0].cx, pills[0].cy)                                  # R
    bg = Image.new("RGBA", (W, H))
    px = bg.load()
    for y in range(H):
        for x in range(0, W):
            t = x / W
            px[x, y] = (int(40 + 60 * t), int(90 + 50 * (1 - y / H)), int(150 - 50 * t), 255)
    bg = Image.alpha_composite(bg, compose(ctrl, W, H))
    bg.convert("RGB").save(path)


def main():
    global OPACITY, SCALE
    ap = argparse.ArgumentParser(description="Clean on-screen Switch gamepad overlay for touchscreens")
    ap.add_argument("--opacity", type=float, default=OPACITY, help="0.2 - 1.0 (default %(default)s)")
    ap.add_argument("--scale", type=float, default=SCALE, help="button size 0.5 - 1.15 (default %(default)s)")
    ap.add_argument("--preview", metavar="PNG", help="write a preview picture and exit")
    ap.add_argument("--preview-edit", metavar="PNG", help="write a picture of edit mode and exit")
    args = ap.parse_args()
    OPACITY = max(0.1, min(1.0, args.opacity))
    SCALE = max(0.5, min(MAX_SCALE, args.scale))

    if args.preview:
        write_preview(args.preview)
        print("wrote", args.preview)
        return
    if args.preview_edit:
        write_preview(args.preview_edit, mode="edit")
        print("wrote", args.preview_edit)
        return
    if sys.platform != "win32":
        print("This overlay only runs on Windows. Use --preview to just draw a picture.")
        return
    try:
        run_overlay()
    except Exception:
        log("fatal error:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
