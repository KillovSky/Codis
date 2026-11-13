"""Codis Events - catálogo de tipos de eventos do sistema."""

from __future__ import annotations

from enum import StrEnum


class EventKind(StrEnum):
    """Identificadores canônicos dos eventos do sistema."""

    EDITOR_CHANGE = "editor.change"
    EDITOR_CURSOR_MOVE = "editor.cursor_move"
    EDITOR_SELECTION = "editor.selection"
    EDITOR_SCROLL = "editor.scroll"
    EDITOR_FOCUS = "editor.focus"
    EDITOR_BLUR = "editor.blur"
    EDITOR_ZOOM = "editor.zoom"
    EDITOR_KEY_PRESS = "editor.key_press"
    EDITOR_PASTE = "editor.paste"
    EDITOR_UNDO = "editor.undo"
    EDITOR_REDO = "editor.redo"
    EDITOR_SAVE = "editor.save"
    EDITOR_CLOSE = "editor.close"
    EDITOR_OPEN = "editor.open"
    EDITOR_ACTIVE = "editor.active"
    EDITOR_MODIFIED = "editor.modified"

    FILE_OPEN = "file.open"
    FILE_SAVE = "file.save"
    FILE_SAVE_AS = "file.save_as"
    FILE_CLOSE = "file.close"
    FILE_REVERT = "file.revert"
    FILE_WATCH_CHANGE = "file.watch_change"
    FILE_ENCODING = "file.encoding"
    FILE_EOL = "file.eol"

    LSP_DIAGNOSTICS = "lsp.diagnostics"
    LSP_COMPLETION = "lsp.completion"
    LSP_HOVER = "lsp.hover"
    LSP_DEFINITION = "lsp.definition"
    LSP_REFERENCES = "lsp.references"
    LSP_SIGNATURE = "lsp.signature"
    LSP_SYMBOLS = "lsp.symbols"
    LSP_RENAME = "lsp.rename"
    LSP_CODE_ACTION = "lsp.code_action"
    LSP_FORMAT = "lsp.format"
    LSP_INLAY_HINT = "lsp.inlay_hint"
    LSP_SEMANTIC_TOKENS = "lsp.semantic_tokens"
    LSP_SERVER_START = "lsp.server_start"
    LSP_SERVER_STOP = "lsp.server_stop"
    LSP_LOG = "lsp.log"

    LINT_RESULT = "lint.result"
    LINT_STARTED = "lint.started"
    LINT_CANCELLED = "lint.cancelled"
    LINT_ERROR = "lint.error"

    SYNTAX_HIGHLIGHT = "syntax.highlight"
    SYNTAX_LANG_CHANGE = "syntax.lang_change"

    UI_PANEL_SHOW = "ui.panel_show"
    UI_PANEL_HIDE = "ui.panel_hide"
    UI_SIDEBAR_SHOW = "ui.sidebar_show"
    UI_SIDEBAR_HIDE = "ui.sidebar_hide"
    UI_COMMAND_PALETTE = "ui.command_palette"
    UI_SEARCH_OPEN = "ui.search_open"
    UI_STATUS_UPDATE = "ui.status_update"
    UI_NOTIFICATION = "ui.notification"
    UI_RESIZE = "ui.resize"

    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ERROR = "plugin.error"
    PLUGIN_HOOK = "plugin.hook"

    CONFIG_CHANGE = "config.change"
    CONFIG_RESET = "config.reset"

    THEME_CHANGE = "theme.change"
    THEME_APPLY = "theme.apply"

    GIT_STATUS = "git.status"
    GIT_COMMIT = "git.commit"
    GIT_PUSH = "git.push"
    GIT_PULL = "git.pull"
    GIT_BRANCH = "git.branch"
    GIT_BLAME = "git.blame"
    GIT_DIFF = "git.diff"
    GIT_CLONE_DONE = "git.clone_done"

    SESSION_SAVE = "session.save"
    SESSION_RESTORE = "session.restore"

    WORKSPACE_OPEN = "workspace.open"
    WORKSPACE_CLOSE = "workspace.close"

    IA_REQUEST = "ia.request"
    IA_CHUNK = "ia.chunk"
    IA_DONE = "ia.done"
    IA_ERROR = "ia.error"
    IA_CANCEL = "ia.cancel"

    PERF_METRIC = "perf.metric"

    FILE_SANITIZED = "file.sanitized"
    FILE_BINARY_BLOCKED = "file.binary_blocked"
    FILE_BINARY_FORCED = "file.binary_forced"
    FILE_ARCHIVE_OPEN = "file.archive_open"

    MEMORY_TAB_PURGE = "memory.tab_purge"
    MEMORY_GC = "memory.gc"

    COMPLEXITY_UPDATE = "complexity.update"

    TIMEMACHINE_DIFF = "timemachine.diff"

    HEATMAP_UPDATE = "heatmap.update"

    FOCUS_MODE_ON = "focus.on"
    FOCUS_MODE_OFF = "focus.off"

    COLLAB_CURSOR = "collab.cursor"
    COLLAB_CHANGE = "collab.change"

    WILDCARD = "*"
