"""Windows XP "Luna" look & feel.

Palette + widget drawing primitives so the whole app reads like a native XP
program: blue gradient title bars, soft beige dialogs, beveled push buttons,
sunken list fields, combo boxes and checkboxes. Every function just blits onto
a surface you pass in, so the app keeps full control of layout.
"""

import pygame

# ------------------------------------------------------------------ palette

# surfaces
FACE = (236, 233, 216)          # dialog / window background (#ECE9D8)
HILITE = (255, 255, 255)        # raised bevel highlight
SHADOW = (170, 166, 150)        # raised bevel shadow
DK_SHADOW = (120, 116, 100)     # outer / sunken deep shadow
FIELD = (255, 255, 255)         # text fields, list boxes

# title bar gradient (active window)
CAP_TOP = (38, 120, 222)        # light band under the top edge
CAP_HI = (18, 92, 205)          # top of the arch
CAP_MID = (10, 74, 180)         # middle
CAP_BOT = (8, 58, 148)          # where the caption meets the face
CAP_GLOW = (150, 198, 255)      # thin highlight line along the very top

# text
TEXT = (16, 16, 16)
TEXT_MUTED = (110, 110, 100)
CAPTION = (255, 255, 255)

# push buttons
BTN_TOP = (255, 255, 255)
BTN_MID = (240, 240, 232)
BTN_BOT = (214, 217, 199)
BTN_BORDER = (121, 121, 110)
BTN_HOVER_BORDER = (60, 135, 205)
BTN_HOVER_BG = (229, 241, 253)
BTN_DEFAULT_BORDER = (228, 195, 101)   # the green-ish default glow
BTN_PRESSED = (198, 204, 180)

# selection / focus
SELECTION = (49, 106, 197)             # list selection blue

# combo box button
COMBO_A = (255, 255, 255)
COMBO_B = (218, 206, 150)
COMBO_BORDER = (142, 135, 110)

# window caption buttons
SYS_BTN_A = (80, 160, 255)
SYS_BTN_B = (10, 90, 200)
CLOSE_BTN_A = (240, 130, 100)
CLOSE_BTN_B = (200, 40, 30)

_MENU_LABELS = ("File", "View", "Game", "Help")


def _lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def gradient_v(surf, rect, top, bottom):
    """Fill rect with a smooth vertical gradient."""
    rect = pygame.Rect(rect)
    if rect.width <= 0 or rect.height <= 0:
        return
    for i in range(rect.height):
        t = i / max(1, rect.height - 1)
        pygame.draw.line(surf, _lerp(top, bottom, t),
                         (rect.x, rect.y + i), (rect.right - 1, rect.y + i))


def bevel(surf, rect, light=HILITE, dark=SHADOW, sunken=False, width=1):
    """Classic Win32 3D edge. sunken flips the light/shadow sides."""
    x, y, w, h = rect
    a, b = (dark, light) if sunken else (light, dark)
    for k in range(width):
        pygame.draw.line(surf, a, (x + k, y + k), (x + w - 1 - k, y + k))
        pygame.draw.line(surf, a, (x + k, y + k), (x + k, y + h - 1 - k))
        pygame.draw.line(surf, b, (x + w - 1 - k, y + k),
                         (x + w - 1 - k, y + h - 1 - k))
        pygame.draw.line(surf, b, (x + k, y + h - 1 - k),
                         (x + w - 1 - k, y + h - 1 - k))


def raised(surf, rect):
    bevel(surf, rect.inflate(-2, -2), HILITE, DK_SHADOW, sunken=False)
    pygame.draw.rect(surf, DK_SHADOW, rect, 1)


def sunken(surf, rect):
    pygame.draw.rect(surf, DK_SHADOW, rect, 1)
    bevel(surf, rect.inflate(-2, -2), SHADOW, HILITE, sunken=True)


def field(surf, rect, bg=FIELD):
    """A sunken white input/list field, the XP text-box look."""
    pygame.draw.rect(surf, bg, rect)
    sunken(surf, rect)


def button(surf, rect, label, font, default=False, hovered=False,
           pressed=False, enabled=True):
    """Draw a Luna push button; returns the rect."""
    interior = rect.inflate(-2, -2)
    if pressed and enabled:
        surf.fill(BTN_PRESSED, rect)
        bevel(surf, interior, BTN_PRESSED, BTN_PRESSED)
        text_y = rect.centery + 1
        text_x = rect.centerx
    else:
        gradient_v(surf, rect, BTN_TOP, BTN_BOT)
        pygame.draw.rect(surf, BTN_BORDER, rect.inflate(2, 2), 1,
                         border_radius=3)
        pygame.draw.rect(surf, BTN_MID, rect, 1, border_radius=3)
        text_y = rect.centery
        text_x = rect.centerx
    if enabled and hovered and not pressed:
        pygame.draw.rect(surf, BTN_HOVER_BG, rect.inflate(-2, -2),
                         border_radius=3)
        pygame.draw.rect(surf, BTN_HOVER_BORDER, rect, 1, border_radius=3)
    if enabled and default:
        pygame.draw.rect(surf, BTN_DEFAULT_BORDER, rect.inflate(2, 2), 1,
                         border_radius=3)
        pygame.draw.rect(surf, BTN_BORDER, rect.inflate(4, 4), 1,
                         border_radius=3)
    color = TEXT_MUTED if not enabled else TEXT
    img = font.render(label, True, color)
    surf.blit(img, (text_x - img.get_width() // 2,
                    text_y - img.get_height() // 2))
    return rect


def checkbox(surf, pos, checked, label, font, text_color=TEXT):
    """13x13 sunken box with a check plus a label; pos is the vertical
    center shared by box and text so they never sit misaligned.
    Returns its full rect."""
    bx = pygame.Rect(pos[0], pos[1] - 13 // 2, 13, 13)
    surf.fill(FIELD, bx)
    sunken(surf, bx)
    if checked:
        pygame.draw.lines(surf, (20, 90, 30), False,
                          [(bx.x + 2, bx.centery),
                           (bx.x + 5, bx.bottom - 3),
                           (bx.right - 2, bx.y + 2)], 2)
    img = font.render(label, True, text_color)
    surf.blit(img, (bx.right + 6, pos[1] - img.get_height() // 2))
    return pygame.Rect(bx.x, bx.y, bx.width + 6 + img.get_width(),
                        max(bx.height, img.get_height()))


def combo(surf, rect, value, font, hovered=False):
    """Read-only dropdown: white field, value text, and an arrow button."""
    field(surf, rect)
    img = font.render(value, True, TEXT)
    surf.blit(img, (rect.x + 4, rect.centery - img.get_height() // 2))
    btn = pygame.Rect(rect.right - 16, rect.y + 2, 15, rect.height - 4)
    gradient_v(surf, btn, COMBO_A, COMBO_B)
    pygame.draw.rect(surf, COMBO_BORDER, btn, 1)
    if hovered:
        pygame.draw.rect(surf, BTN_HOVER_BORDER, btn, 1)
    cx, cy = btn.centerx, btn.centery
    pygame.draw.polygon(surf, (40, 40, 40),
                        [(cx - 3, cy - 2), (cx + 3, cy - 2), (cx, cy + 2)])
    return rect


def groupbox(surf, rect, label, font, text_color=TEXT_MUTED):
    """Etched frame with a caption breaking the top border."""
    pygame.draw.rect(surf, HILITE, rect.inflate(2, 2), 1)
    pygame.draw.rect(surf, SHADOW, rect, 1)
    pad = 6
    img = font.render(label, True, text_color)
    surf.fill(FACE, (rect.x + pad * 2, rect.y - img.get_height() // 2,
                     img.get_width() + pad, img.get_height()))
    surf.blit(img, (rect.x + pad * 2 + pad // 2,
                    rect.y - img.get_height() // 2))
    return img


def titlebar(surf, rect, title, font, icon=None, dialog=False):
    """Blue Luna caption with gradient, glow line and title.
    dialog=True draws only the close button, like an XP message box;
    returns the close button rect."""
    g = rect.copy()
    # base gradient split into the classic arch + lower band
    gradient_v(surf, (g.x, g.y, g.w, g.h), CAP_HI, CAP_BOT)
    # soft middle glow
    glow = pygame.Rect(g.x + 2, g.y + 2, g.w - 4, int(g.h * 0.55))
    gradient_v(surf, glow, CAP_TOP, CAP_MID)
    # rounded top corners
    pygame.draw.rect(surf, CAP_GLOW, (g.x + 2, g.y, g.w - 4, 1))
    for corner in (g.x, g.right - 2):
        pygame.draw.line(surf, FACE, (corner, g.bottom - 1),
                         (corner, g.bottom - 1))
    # icon: vertically centred, and shrunk to fit when it is taller than
    # the caption (a 32 px logo dropped into a 24 px dialog bar must not
    # spill over the edges)
    x = g.x + 5
    if icon is not None:
        if icon.get_height() > g.height - 4:
            ratio = (g.height - 4) / icon.get_height()
            icon = pygame.transform.smoothscale(
                icon, (max(1, int(icon.get_width() * ratio)),
                       g.height - 4))
        surf.blit(icon, (x, g.centery - icon.get_height() // 2))
        x += icon.get_width() + 6      # start clear of the icon, never on it
    else:
        x += 20
    t = font.render(title, True, CAPTION)
    shadow = font.render(title, True, (10, 40, 90))
    ty = g.centery - t.get_height() // 2    # the caption centres its text
    surf.blit(shadow, (x + 3, ty + 1))      # offset drop shadow, Luna style
    surf.blit(t, (x + 2, ty))
    # caption buttons on the right: minimize, maximize, close
    # (a dialog caption carries only the close button)
    size = 16
    pad = 3
    top = g.y + (g.height - size) // 2
    close = pygame.Rect(g.right - size - 3, top, size, size)
    maximize = close.move(-(size + pad), 0)
    minimize = close.move(-2 * (size + pad), 0)
    if not dialog:
        for b in (minimize, maximize):
            gradient_v(surf, b, SYS_BTN_A, SYS_BTN_B)
            pygame.draw.rect(surf, (200, 225, 255), b, 1)
        pygame.draw.rect(surf, (255, 255, 255),
                         (minimize.x + 4, minimize.bottom - 5, 7, 2))
        pygame.draw.rect(surf, (255, 255, 255),
                         (maximize.x + 3, maximize.y + 3, 9, 8), 1)
        pygame.draw.line(surf, (255, 255, 255), (maximize.x + 3, maximize.y + 4),
                         (maximize.right - 4, maximize.y + 4), 2)
    gradient_v(surf, close, CLOSE_BTN_A, CLOSE_BTN_B)
    pygame.draw.rect(surf, (255, 210, 200), close, 1)
    pygame.draw.lines(surf, (255, 255, 255), False,
                      [(close.x + 4, close.y + 4),
                       (close.right - 4, close.bottom - 4)], 2)
    pygame.draw.lines(surf, (255, 255, 255), False,
                      [(close.right - 4, close.y + 4),
                       (close.x + 4, close.bottom - 4)], 2)
    return close


def menubar(surf, rect, active=None, font=None, labels=_MENU_LABELS):
    surf.fill(FACE, rect)
    pygame.draw.line(surf, SHADOW, (rect.x, rect.bottom - 1),
                     (rect.right, rect.bottom - 1))
    pygame.draw.line(surf, HILITE, (rect.x, rect.bottom),
                     (rect.right, rect.bottom))
    x = rect.x + 6
    for i, lbl in enumerate(labels):
        img = font.render(lbl, True, TEXT)
        w = img.get_width() + 12
        cell = pygame.Rect(x, rect.y + 2, w, rect.height - 4)
        if active == i:
            field(surf, cell, SELECTION)
            img = font.render(lbl, True, (255, 255, 255))
        x += w + 2
        surf.blit(img, (cell.x + 6, cell.centery - img.get_height() // 2))


def statusbar(surf, rect, panels, font):
    """Bottom grip bar with sunken text panels."""
    surf.fill(FACE, rect)
    pygame.draw.line(surf, HILITE, (rect.x, rect.y), (rect.right, rect.y))
    pygame.draw.line(surf, SHADOW, (rect.x, rect.y + 1),
                     (rect.right, rect.y + 1))
    x = rect.x + 3
    n = len(panels)
    for i, text in enumerate(panels):
        if i == n - 1:
            w = rect.right - x - 20
        else:
            w = int((rect.width - 24) * (0.5 if i == 0 else 0.28))
        panel = pygame.Rect(x, rect.y + 3, max(20, w), rect.height - 6)
        bevel(surf, panel, SHADOW, HILITE, sunken=True)
        img = font.render(text, True, TEXT)
        surf.blit(img, (panel.x + 4, panel.centery - img.get_height() // 2))
        x = panel.right + 3
    # resize grip dots
    for r in range(3):
        for c in range(3):
            if r + c >= 2:
                p = (rect.right - 4 - c * 4, rect.bottom - 4 - r * 4)
                pygame.draw.line(surf, HILITE, p, p)
                pygame.draw.line(surf, SHADOW, (p[0] + 1, p[1]), (p[0] + 1, p[1]))


def tooltip(surf, pos, lines, font):
    """A classic XP tool tip: pale yellow bubble, hairline black border,
    drawn just below-right of the cursor and clamped inside the surface so
    it never hangs off a window edge. Returns the bubble rect."""
    imgs = [font.render(line, True, (0, 0, 0)) for line in lines]
    w = max([i.get_width() for i in imgs] or [0]) + 8
    h = sum(i.get_height() + 1 for i in imgs) + 6
    box = pygame.Rect(pos[0] + 12, pos[1] + 18, w, h)
    area = surf.get_rect()
    if box.right > area.right - 2:
        box.x = area.right - 2 - w
    if box.bottom > area.bottom - 2:
        box.y = max(2, pos[1] - h - 8)
    surf.fill((255, 255, 225), box)
    pygame.draw.rect(surf, (0, 0, 0), box, 1)
    y = box.y + 3
    for img in imgs:
        surf.blit(img, (box.x + 4, y))
        y += img.get_height() + 1
    return box


def message_frame(surf, rect, title, font, icon=None):
    """A modal dialog window: caption + face body.
    Returns (body rect, close button rect)."""
    pygame.draw.rect(surf, DK_SHADOW, rect.inflate(2, 2), 1)
    surf.fill(FACE, rect)
    cap = pygame.Rect(rect.x + 2, rect.y + 2, rect.w - 4, 24)
    close = titlebar(surf, cap, title, font, icon=icon, dialog=True)
    body = pygame.Rect(rect.x + 2, cap.bottom, rect.w - 4,
                       rect.bottom - cap.bottom - 2)
    surf.fill(FACE, body)
    return body, close
