"""Read-only text for the "Bot Dev" screen: the bot API and installed code.

No pygame, no app state. ``api_lines()`` introspects fligchess.core.bot_api
with `inspect` and lays out the module intro plus, for `Position` and `Bot`,
every public method with its signature and docstring - so the docs always
match the real signatures instead of a hand-copied page. ``bot_source_lines``
returns the source text of an installed bot class so it can be shown, never
edited, beside the docs.

Everything comes back as a flat list of lines the caller scrolls.
"""

import inspect
import textwrap

from fligchess.core import bot_api

_WIDTH = 68           # wrap column for the docs panel


def _signature(fn):
    try:
        return str(inspect.signature(fn))
    except (ValueError, TypeError):
        return "()"


def _doc(method):
    doc = inspect.getdoc(method) or ""
    return [ln.strip() for ln in doc.splitlines()]


def _wrap_block(text, indent="    "):
    out = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        out.extend(textwrap.wrap(para, _WIDTH - len(indent)))
    return [indent + line for line in out]


def _class_lines(cls, title):
    lines = [title, "-" * len(title), ""]
    doc = inspect.getdoc(cls)
    if doc:
        lines += _wrap_block(doc)
        lines.append("")
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            lines.append(f"  {name}  (read-only property)")
            lines += ["      " + ln for ln in _doc(member.fget)]
            lines.append("")
        elif inspect.isfunction(member) or inspect.ismethod(member):
            lines.append(f"  {name}{_signature(member)}")
            lines += ["      " + ln for ln in _doc(member)]
            lines.append("")
    return lines


def api_lines():
    """The bot-authoring reference as display lines."""
    lines = []
    module_doc = inspect.getdoc(bot_api) or ""
    lines += _wrap_block(module_doc, indent="")
    lines += ["", "=" * _WIDTH, ""]
    lines += _class_lines(bot_api.Position, "class Position")
    lines += ["", "=" * _WIDTH, ""]
    lines += _class_lines(bot_api.Bot, "class Bot")
    lines += ["", "load_bots(directory)"]
    lines += ["      " + ln for ln in _doc(bot_api.load_bots)]
    return lines


def bot_source_lines(cls):
    """The full source text of an installed bot class, as display lines.

    Reads the file the class was defined in. Returns a short note instead if
    the source cannot be located (a frozen build, or a class built at
    runtime)."""
    try:
        path = inspect.getsourcefile(cls)
        if not path:
            raise OSError("no source file")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().splitlines()
    except (OSError, TypeError, ValueError):
        return [f"<source for {getattr(cls, 'name', cls)!r} is not available>"]
