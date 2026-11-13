#!/usr/bin/env python3
"""Configuração global dos testes do Codis.

Instala stubs robustos de Tkinter (headless), isola CONFIG_DIR/CONFIG_FILE
em tmp_path e fornece fixtures reutilizáveis por toda a suíte.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# A raiz do projeto precisa estar no sys.path ANTES de importar ``src``;
# com o comando ``pytest`` (sem ``python -m``) ela não é adicionada sozinha.
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.config as _config_mod  # noqa: E402
import src.constants as _const  # noqa: E402


def _build_tk_stub() -> types.ModuleType:
    """Constrói stub completo de tkinter para testes headless."""
    tk = types.ModuleType("tkinter")

    for _name, _val in (
        ("END", "end"),
        ("SEL", "sel"),
        ("INSERT", "insert"),
        ("CURRENT", "current"),
        ("BOTH", "both"),
        ("WORD", "word"),
        ("CHAR", "char"),
        ("FLAT", "flat"),
        ("LEFT", "left"),
        ("RIGHT", "right"),
        ("TOP", "top"),
        ("BOTTOM", "bottom"),
        ("X", "x"),
        ("Y", "y"),
        ("NW", "nw"),
        ("CENTER", "center"),
        ("HORIZONTAL", "horizontal"),
        ("VERTICAL", "vertical"),
        ("NORMAL", "normal"),
        ("DISABLED", "disabled"),
        ("HIDDEN", "hidden"),
        ("ACTIVE", "active"),
        ("ALL", "all"),
        ("LAST", "last"),
        ("FIRST", "first"),
        ("RAISED", "raised"),
        ("SUNKEN", "sunken"),
        ("GROOVE", "groove"),
        ("RIDGE", "ridge"),
        ("NONE", "none"),
        ("ROUND", "round"),
        ("BUTT", "butt"),
        ("PROJECTING", "projecting"),
        ("MITER", "miter"),
        ("BEVEL", "bevel"),
        ("ARC", "arc"),
        ("CHORD", "chord"),
        ("PIESLICE", "pieslice"),
    ):
        setattr(tk, _name, _val)

    class TclError(Exception):
        pass

    tk.TclError = TclError

    class _W:
        def __init__(self, *a, **kw):
            self._kw: dict = kw

        @staticmethod
        def pack(**kw):
            pass

        @staticmethod
        def grid(**kw):
            pass

        @staticmethod
        def place(**kw):
            pass

        @staticmethod
        def place_forget():
            pass

        @staticmethod
        def pack_forget():
            pass

        @staticmethod
        def grid_forget():
            pass

        @staticmethod
        def bind(*_a, **_kw):
            return ""

        @staticmethod
        def unbind(*a):
            pass

        def configure(self, **kw):
            self._kw.update(kw)

        def config(self, **kw):
            self._kw.update(kw)

        @staticmethod
        def destroy():
            pass

        @staticmethod
        def winfo_exists():
            return True

        @staticmethod
        def winfo_width():
            return 800

        @staticmethod
        def winfo_height():
            return 600

        @staticmethod
        def winfo_x():
            return 0

        @staticmethod
        def winfo_y():
            return 0

        @staticmethod
        def winfo_screenwidth():
            return 1920

        @staticmethod
        def winfo_screenheight():
            return 1080

        @staticmethod
        def winfo_class():
            return "Widget"

        @staticmethod
        def winfo_id():
            return 0

        def winfo_toplevel(self):
            return self

        @staticmethod
        def winfo_reqwidth():
            return 100

        @staticmethod
        def winfo_reqheight():
            return 30

        @staticmethod
        def after(_ms, *_a, _fn=None):
            return "after#1"

        @staticmethod
        def after_idle(fn, *a):
            pass

        @staticmethod
        def after_cancel(job):
            pass

        @staticmethod
        def update():
            pass

        @staticmethod
        def update_idletasks():
            pass

        @staticmethod
        def focus_set():
            pass

        @staticmethod
        def focus_force():
            pass

        @staticmethod
        def lift():
            pass

        @staticmethod
        def lower():
            pass

        @staticmethod
        def event_add(*a):
            pass

        @staticmethod
        def event_generate(*a):
            pass

        def cget(self, key):
            return self._kw.get(key, "")

        @staticmethod
        def keys():
            return []

        def nametowidget(self, _n):
            return self

        @staticmethod
        def withdraw():
            pass

        @staticmethod
        def deiconify():
            pass

        @staticmethod
        def geometry(*_a):
            return "800x600+0+0"

        @staticmethod
        def resizable(*a):
            pass

        @staticmethod
        def title(*a):
            pass

        @staticmethod
        def protocol(*a):
            pass

        @staticmethod
        def minsize(*a):
            pass

        @staticmethod
        def maxsize(*a):
            pass

        @staticmethod
        def attributes(*a):
            pass

        @staticmethod
        def grab_set():
            pass

        @staticmethod
        def grab_release():
            pass

        @staticmethod
        def wait_window():
            pass

        @staticmethod
        def transient(*a):
            pass

        @staticmethod
        def columnconfigure(*a, **kw):
            pass

        @staticmethod
        def rowconfigure(*a, **kw):
            pass

        @staticmethod
        def option_add(*a):
            pass

        @staticmethod
        def tk_setPalette(*a):
            pass

        @staticmethod
        def tk():
            return None

    class Text(_W):
        def __init__(self, *a, **kw):
            self._content = ""

        def get(self, _start="1.0", _end=None):
            return self._content

        def insert(self, _pos, text, *_tags):
            self._content += text

        def delete(self, _start, _end=None):
            self._content = ""

        def index(self, pos):
            if pos in (tk.INSERT, "insert"):  # pylint: disable=no-member
                return "1.0"
            if pos in (tk.END, "end"):  # pylint: disable=no-member
                return f"{len(self._content.splitlines()) + 1}.0"
            return str(pos)

        @staticmethod
        def mark_set(name, pos):
            pass

        @staticmethod
        def mark_names():
            return []

        @staticmethod
        def tag_add(tag, *pos):
            pass

        @staticmethod
        def tag_remove(tag, *pos):
            pass

        @staticmethod
        def tag_configure(tag, **kw):
            pass

        @staticmethod
        def tag_delete(*tags):
            pass

        @staticmethod
        def tag_names(*pos):
            return []

        @staticmethod
        def tag_ranges(tag):
            return []

        @staticmethod
        def tag_raise(tag, *a):
            pass

        @staticmethod
        def tag_lower(tag, *_a):
            pass

        @staticmethod
        def see(pos):
            pass

        @staticmethod
        def search(*_a, **kw):
            return ""

        @staticmethod
        def count(*a, **kw):
            return (0,)

        @staticmethod
        def compare(_a, op, b):
            return False

        @staticmethod
        def bbox(pos):
            return (0, 0, 10, 10)

        @staticmethod
        def xview(*a):
            return (0.0, 1.0)

        @staticmethod
        def yview(*a):
            return (0.0, 1.0)

        @staticmethod
        def xview_moveto(f):
            pass

        @staticmethod
        def yview_moveto(f):
            pass

        @staticmethod
        def edit_reset():
            pass

        @staticmethod
        def edit_undo():
            pass

        @staticmethod
        def edit_redo():
            pass

        @staticmethod
        def edit_modified(*a):
            return False

        @staticmethod
        def dlineinfo(pos):
            return (0, 0, 10, 10, 5)

        @staticmethod
        def image_create(*a, **kw):
            pass

        @staticmethod
        def window_create(*a, **kw):
            pass

    class Canvas(_W):
        @staticmethod
        def create_rectangle(*_a, **_kw):
            return 1

        @staticmethod
        def create_oval(*_a, **_kw):
            return 1

        @staticmethod
        def create_line(*_a, **_kw):
            return 1

        @staticmethod
        def create_text(*_a, **_kw):
            return 1

        @staticmethod
        def create_image(*_a, **_kw):
            return 1

        @staticmethod
        def create_window(*_a, **_kw):
            return 1

        @staticmethod
        def create_polygon(*a, **kw):
            return 1

        @staticmethod
        def create_arc(*_a, **kw):
            return 1

        @staticmethod
        def delete(*tags):
            pass

        @staticmethod
        def coords(*a):
            return (0, 0, 100, 100)

        @staticmethod
        def itemconfig(*a, **kw):
            pass

        @staticmethod
        def itemconfigure(*a, **kw):
            pass

        @staticmethod
        def move(*a):
            pass

        @staticmethod
        def tag_bind(*a, **kw):
            pass

        @staticmethod
        def tag_lower(*a):
            pass

        @staticmethod
        def tag_raise(*_a):
            pass

        @staticmethod
        def find_withtag(tag):
            return ()

        @staticmethod
        def find_overlapping(*_a):
            return ()

        @staticmethod
        def find_closest(*a):
            return (1,)

        @staticmethod
        def gettags(*a):
            return ()

        @staticmethod
        def addtag_withtag(*a):
            pass

        @staticmethod
        def addtag_overlapping(*a):
            pass

        @staticmethod
        def scale(*a):
            pass

        @staticmethod
        def postscript(**kw):
            return ""

        @staticmethod
        def xview_scroll(*a):
            pass

        @staticmethod
        def yview_scroll(*a):
            pass

    class Frame(_W):
        pass

    class LabelFrame(_W):
        pass

    class Label(_W):
        pass

    class Button(_W):
        pass

    class Entry(_W):
        @staticmethod
        def get():
            return ""

        @staticmethod
        def insert(*a):
            pass

        @staticmethod
        def delete(*a):
            pass

        @staticmethod
        def select_range(*a):
            pass

    class Scrollbar(_W):
        pass

    class Listbox(_W):
        @staticmethod
        def insert(*_a):
            pass

        @staticmethod
        def delete(*a):
            pass

        @staticmethod
        def get(*a):
            return ()

        @staticmethod
        def size():
            return 0

        @staticmethod
        def curselection():
            return ()

        @staticmethod
        def selection_set(*a):
            pass

        @staticmethod
        def selection_clear(*a):
            pass

    class Checkbutton(_W):
        pass

    class Radiobutton(_W):
        pass

    class Scale(_W):
        @staticmethod
        def get():
            return 0

        @staticmethod
        def set(v):
            pass

    class Spinbox(_W):
        @staticmethod
        def get():
            return ""

        @staticmethod
        def set(v):
            pass

    class PanedWindow(_W):
        @staticmethod
        def add(*_a, **kw):
            pass

        @staticmethod
        def remove(*a):
            pass

        @staticmethod
        def sash_coord(*a):
            return (0, 0)

        @staticmethod
        def sash_place(*a):
            pass

    class Toplevel(_W):
        pass

    class Menu(_W):
        @staticmethod
        def add_command(**kw):
            pass

        @staticmethod
        def add_separator():
            pass

        @staticmethod
        def add_cascade(**kw):
            pass

        @staticmethod
        def add_checkbutton(**kw):
            pass

        @staticmethod
        def add_radiobutton(**kw):
            pass

        @staticmethod
        def delete(*a):
            pass

        @staticmethod
        def entryconfig(*a, **kw):
            pass

        @staticmethod
        def post(*_a):
            pass

        @staticmethod
        def unpost():
            pass

        @staticmethod
        def index(*a):
            return 0

    class Misc(_W):
        pass

    class BaseWidget(_W):
        pass

    class Variable:
        def __init__(self, *a, **kw):
            self._val = ""

        def get(self):
            return self._val

        def set(self, v):
            self._val = v

        @staticmethod
        def trace_add(*a):
            pass

        @staticmethod
        def trace_remove(*a):
            pass

        @staticmethod
        def trace_variable(*a):
            pass

    class StringVar(Variable):
        pass

    class IntVar(Variable):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._val = 0

    class BooleanVar(Variable):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._val = False

    class DoubleVar(Variable):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._val = 0.0

    class PhotoImage:
        def __init__(self, **kw):
            self._w = kw.get("width", 1)
            self._h = kw.get("height", 1)

        @staticmethod
        def put(*a, **kw):
            pass

        @staticmethod
        def get(x, y):
            return (0, 0, 0)

        @staticmethod
        def blank():
            pass

        @staticmethod
        def copy():
            return PhotoImage()

        def subsample(self, *a):
            return self

        def zoom(self, *a):
            return self

        def width(self):
            return self._w

        def height(self):
            return self._h

    class BitmapImage:
        @staticmethod
        def __init__(**kw):
            pass

    for _cls in (
        Text,
        Canvas,
        Frame,
        LabelFrame,
        Label,
        Button,
        Entry,
        Scrollbar,
        Listbox,
        Checkbutton,
        Radiobutton,
        Scale,
        Spinbox,
        PanedWindow,
        Toplevel,
        Menu,
        Misc,
        BaseWidget,
        Variable,
        StringVar,
        IntVar,
        BooleanVar,
        DoubleVar,
        PhotoImage,
        BitmapImage,
    ):
        setattr(tk, _cls.__name__, _cls)

    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda **kw: ""
    filedialog.asksaveasfilename = lambda **kw: ""
    filedialog.askdirectory = lambda **kw: ""
    filedialog.askopenfilenames = lambda **kw: ()

    messagebox = types.ModuleType("tkinter.messagebox")
    for _n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, _n, lambda *a, **kw: None)
    messagebox.askyesno = lambda *a, **kw: True
    messagebox.askokcancel = lambda *a, **kw: True
    messagebox.askquestion = lambda *a, **kw: "yes"
    messagebox.YES = "yes"
    messagebox.NO = "no"
    messagebox.OK = "ok"
    messagebox.CANCEL = "cancel"

    simpledialog = types.ModuleType("tkinter.simpledialog")
    simpledialog.askstring = lambda *a, **kw: None
    simpledialog.askinteger = lambda *a, **kw: None
    simpledialog.askfloat = lambda *a, **kw: None
    simpledialog.Dialog = _W

    ttk = types.ModuleType("tkinter.ttk")
    for _n in (
        "Frame",
        "Label",
        "Button",
        "Entry",
        "Combobox",
        "Treeview",
        "Scrollbar",
        "Notebook",
        "PanedWindow",
        "Progressbar",
        "Scale",
        "Separator",
        "Spinbox",
        "Checkbutton",
        "Radiobutton",
        "LabelFrame",
    ):
        _m = type(
            _n,
            (_W,),
            {
                "insert": lambda self, *a, **kw: None,
                "delete": lambda self, *a, **kw: None,
                "get": lambda self, *a: "",
                "set": lambda self, *a: None,
                "heading": lambda self, *a, **kw: None,
                "column": lambda self, *a, **kw: None,
                "selection": lambda self: (),
                "selection_set": lambda self, *a: None,
                "identify_row": lambda self, y: "",
                "identify_column": lambda self, x: "",
                "item": lambda self, *a, **kw: {},
                "get_children": lambda self, *a: (),
                "focus": lambda self, *a: "",
                "add": lambda self, *a, **kw: None,
                "tab": lambda self, *a, **kw: None,
                "tabs": lambda self: (),
                "select": lambda self, *a: None,
                "index": lambda self, *a: 0,
                "forget": lambda self, *a: None,
                "configure": lambda self, **kw: None,
                "current": lambda self, *a: 0,
            },
        )
        setattr(ttk, _n, _m)

    class _Style(_W):
        @staticmethod
        def theme_use(*a):
            pass

        @staticmethod
        def theme_names():
            return ()

        @staticmethod
        def layout(*a):
            pass

        @staticmethod
        def element_create(*a, **kw):
            pass

        @staticmethod
        def map(*a, **kw):
            pass

        @staticmethod
        def configure(*a, **kw):
            pass

    ttk.Style = _Style

    font_mod = types.ModuleType("tkinter.font")

    class _Font:
        def __init__(self, *a, **kw):
            self._opts = kw

        def configure(self, **kw):
            self._opts.update(kw)

        @staticmethod
        def actual(key=None):
            return {} if key is None else 13

        @staticmethod
        def measure(text):
            return len(text) * 8

        @staticmethod
        def metrics(*a, **kw):
            return {"linespace": 16, "fixed": 1, "ascent": 12, "descent": 4}

        def copy(self):
            return _Font(**self._opts)

        def cget(self, key):
            return self._opts.get(key, "")

        def keys(self):
            return list(self._opts.keys())

    font_mod.Font = _Font
    font_mod.families = lambda **kw: ("Consolas", "Courier New", "Monaco")
    font_mod.names = lambda: ()
    font_mod.nametofont = lambda n: _Font()

    colorchooser = types.ModuleType("tkinter.colorchooser")
    colorchooser.askcolor = lambda **kw: (None, None)

    scrolledtext = types.ModuleType("tkinter.scrolledtext")

    class ScrolledText(Text):
        """Stub de scrolledtext.ScrolledText - reaproveita o Text stub acima."""

    scrolledtext.ScrolledText = ScrolledText

    tk.filedialog = filedialog
    tk.messagebox = messagebox
    tk.simpledialog = simpledialog
    tk.ttk = ttk
    tk.font = font_mod
    tk.colorchooser = colorchooser
    tk.scrolledtext = scrolledtext

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox
    sys.modules["tkinter.simpledialog"] = simpledialog
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.font"] = font_mod
    sys.modules["tkinter.colorchooser"] = colorchooser
    sys.modules["tkinter.scrolledtext"] = scrolledtext
    return tk


_build_tk_stub()


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Redireciona CONFIG_DIR/CONFIG_FILE para tmp_path em cada teste."""
    cfg_dir = tmp_path / ".codis_test"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(_const, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(_const, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(_config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(_config_mod, "CONFIG_FILE", cfg_file)
    yield cfg_dir


@pytest.fixture()
def cfg():
    """ConfigManager limpo com diretório isolado."""
    from src.config import ConfigManager

    return ConfigManager()


@pytest.fixture()
def tmp_py_file(tmp_path):
    """Arquivo .py temporário com conteúdo simples."""
    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    return 42\n", encoding="utf-8")
    return f


@pytest.fixture()
def tmp_js_file(tmp_path):
    """Arquivo .js temporário com conteúdo simples."""
    f = tmp_path / "sample.js"
    f.write_text(
        "function greet(name) {\n  return `Hello, ${name}`;\n}\n", encoding="utf-8"
    )
    return f


@pytest.fixture()
def mock_workspace(tmp_path):
    """Workspace simulado com estrutura mínima de projeto Python."""
    (tmp_path / "main.py").write_text("# entry\n", encoding="utf-8")
    (tmp_path / "utils.py").write_text("# utils\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# TestProject\nMy project.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def make_symlink():
    """Cria symlinks, pulando o teste quando o SO não permite (ex.: Windows sem privilégio)."""

    def _make(link, target, *, directory: bool = False) -> None:
        try:
            Path(link).symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks indisponíveis neste ambiente")

    return _make
