"""Padrões de highlight de sintaxe para as linguagens suportadas pelo Codis.

Define expressões regulares compiladas (via ``_pat``) para cada linguagem
(Python, JS/TS, Rust, Go, C/C++, etc.) e o dicionário ``LANG_PATTERNS``
que mapeia identificadores de linguagem aos seus respectivos padrões.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("Codis")


def _pat(tokens: list[tuple[str, str]], flags: int = 0) -> re.Pattern:
    """Compila lista de (nome, padrão) em um pattern com grupos nomeados."""
    return re.compile(
        "|".join(f"(?P<{n}>{p})" for n, p in tokens),
        re.MULTILINE | flags,
    )


_JS_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("comment_line", r"//[^\n]*"),
    ("string_tmpl", r"`(?:[^`\\]|\\.|\$\{(?:[^}]|\{[^}]*\})*\})*`"),
    ("string", r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    ("regexp", r"(?<![)\w\d])/(?![/*])(?:[^/\\\n]|\\.)+/[gimsuyv]*"),
    ("decorator", r"@[\w.]+"),
    (
        "keyword",
        r"\b(?:async|await|break|case|catch|class|const|continue|debugger|"
        r"default|delete|do|else|export|extends|finally|for|from|function|"
        r"if|import|in|instanceof|let|new|of|return|static|super|switch|"
        r"throw|try|typeof|var|void|while|with|yield|abstract|declare|"
        r"override|accessor|using)\b",
    ),
    (
        "type_kw",
        r"\b(?:any|boolean|never|number|object|string|symbol|unknown|void|"
        r"interface|type|enum|namespace|implements|readonly|keyof|infer|"
        r"is|as|satisfies|asserts|intrinsic|unique|out|in)\b",
    ),
    (
        "builtin",
        r"\b(?:Array|Boolean|console|Date|Error|Function|JSON|Map|Math|Number|"
        r"Object|Promise|Proxy|Reflect|RegExp|Set|String|Symbol|WeakMap|"
        r"WeakRef|WeakSet|undefined|null|true|false|NaN|Infinity|globalThis|"
        r"setTimeout|clearTimeout|setInterval|clearInterval|queueMicrotask|"
        r"fetch|Request|Response|Headers|FormData|AbortController|URL|"
        r"URLSearchParams|TextEncoder|TextDecoder|Blob|File|FileReader|"
        r"crypto|performance|require|module|exports|process|Buffer|"
        r"window|document|navigator|location|history|localStorage|"
        r"sessionStorage|indexedDB|WebSocket|Worker|ServiceWorker|"
        r"addEventListener|removeEventListener|dispatchEvent|"
        r"querySelector|querySelectorAll|getElementById|createElement|"
        r"getComputedStyle|requestAnimationFrame|cancelAnimationFrame)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F][0-9a-fA-F_]*n?\b|"
        r"\b0[oO][0-7][0-7_]*\b|"
        r"\b0[bB][01][01_]*n?\b|"
        r"\b\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?n?\b",
    ),
    ("func_call", r"\b([A-Za-z_$][\w$]*)\s*(?=\()"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_$]*\b"),
    (
        "operator",
        r"(?:\?\?=?|&&=?|\|\|=?|>>>?=?|<<=?|>>=?|[+\-*/%^&|~]=?|[!<>]=?|===?|!==?)",
    ),
]

_TS_TOKENS = _JS_TOKENS

_PY_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    (
        "string",
        r'(?:f|r|b|u|rb|br|rf|fr)?"""[\s\S]*?"""|'
        r"(?:f|r|b|u|rb|br|rf|fr)?'''[\s\S]*?'''|"
        r'(?:f|r|b|u|rb|br|rf|fr)?"(?:[^"\\]|\\.)*"|'
        r"(?:f|r|b|u|rb|br|rf|fr)?'(?:[^'\\]|\\.)*'",
    ),
    ("decorator", r"@[\w.]+(?:\([^\)]*\))?"),
    (
        "keyword",
        r"\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|"
        r"else|except|finally|for|from|global|if|import|in|is|lambda|"
        r"nonlocal|not|or|pass|raise|return|try|while|with|yield|match|case)\b",
    ),
    (
        "builtin",
        r"\b(?:abs|all|any|ascii|bin|bool|breakpoint|bytearray|bytes|callable|"
        r"chr|classmethod|compile|complex|copyright|delattr|dict|dir|divmod|"
        r"enumerate|eval|exec|filter|float|format|frozenset|getattr|globals|"
        r"hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|"
        r"license|list|locals|map|max|memoryview|min|next|object|oct|open|ord|"
        r"pow|print|property|range|repr|reversed|round|set|setattr|slice|"
        r"sorted|staticmethod|str|sum|super|tuple|type|vars|zip|"
        r"None|True|False|NotImplemented|Ellipsis|__debug__|"
        r"__name__|__file__|__doc__|__package__|__spec__|__loader__|"
        r"__builtins__|__build_class__|__import__|"
        r"ArithmeticError|AssertionError|AttributeError|BaseException|"
        r"BlockingIOError|BrokenPipeError|BufferError|BytesWarning|"
        r"ChildProcessError|ConnectionAbortedError|ConnectionError|"
        r"ConnectionRefusedError|ConnectionResetError|DeprecationWarning|"
        r"EOFError|EnvironmentError|Exception|FileExistsError|FileNotFoundError|"
        r"FloatingPointError|FutureWarning|GeneratorExit|IOError|ImportError|"
        r"ImportWarning|IndentationError|IndexError|InterruptedError|IsADirectoryError|"
        r"KeyError|KeyboardInterrupt|LookupError|MemoryError|ModuleNotFoundError|"
        r"NameError|NotADirectoryError|NotImplemented|NotImplementedError|"
        r"OSError|OverflowError|PendingDeprecationWarning|PermissionError|"
        r"ProcessLookupError|RecursionError|ReferenceError|ResourceWarning|"
        r"RuntimeError|RuntimeWarning|StopAsyncIteration|StopIteration|"
        r"SyntaxError|SyntaxWarning|SystemError|SystemExit|TabError|"
        r"TimeoutError|TypeError|UnboundLocalError|UnicodeDecodeError|"
        r"UnicodeEncodeError|UnicodeError|UnicodeTranslateError|UnicodeWarning|"
        r"UserWarning|ValueError|Warning|WindowsError|ZeroDivisionError)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F][0-9a-fA-F_]*\b|"
        r"\b0[oO][0-7][0-7_]*\b|"
        r"\b0[bB][01][01_]*\b|"
        r"\b\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?[jJ]?\b",
    ),
    ("func_def", r"(?<=def\s)[\w]+"),
    ("class_def", r"(?<=class\s)[\w]+"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
    (
        "escape",
        r"\\[nrtfvabx\\'\"\n0-9]|\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}|\\N\{[^}]+\}",
    ),
    ("operator", r"[+\-*/%@&|^~<>]=?|[=!]=|<<|>>|\*\*|//|->|:="),
]

_HTML_TOKENS = [
    ("comment_block", r"<!--[\s\S]*?-->"),
    ("doctype", r"<!DOCTYPE[^>]*>"),
    ("cdata", r"<!\[CDATA\[[\s\S]*?\]\]>"),
    ("tag_open", r"</?\s*[A-Za-z][A-Za-z0-9.-]*"),
    ("attr_name", r"\s[A-Za-z:][A-Za-z0-9_:.@-]*(?=\s*=|\s|/?>)"),
    ("string", r'"[^"]*"|\'[^\']*\''),
    ("entity", r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]\w*);"),
    ("bracket", r"[<>/]"),
]

_CSS_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("string", r'"[^"]*"|\'[^\']*\''),
    ("at_rule", r"@[\w-]+"),
    ("value_color", r"#[0-9a-fA-F]{3,8}\b"),
    ("property", r"[\w-]+(?=\s*:)"),
    (
        "number",
        r"-?\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|vmin|vmax|pt|cm|mm|"
        r"in|pc|ex|ch|fr|deg|rad|turn|s|ms|Hz|kHz|dpi|dpcm|dppx)?",
    ),
    ("important", r"!important\b"),
    ("pseudo", r"::?[\w-]+"),
    ("bracket", r"[{}():;,]"),
    ("variable", r"--[\w-]+"),
    (
        "builtin",
        r"\b(?:var|calc|min|max|clamp|env|attr|rgb|rgba|hsl|hsla|hwb|lab|"
        r"lch|oklch|oklab|color|color-mix|linear-gradient|radial-gradient|"
        r"conic-gradient|repeating-linear-gradient|repeating-radial-gradient|"
        r"image-set|cross-fade|element|image|url|format|local|counters?|"
        r"symbols|translate|scale|rotate|skew|matrix|perspective|"
        r"translateX|translateY|translateZ|scaleX|scaleY|rotateX|"
        r"rotateY|rotateZ|blur|brightness|contrast|drop-shadow|"
        r"grayscale|hue-rotate|invert|opacity|saturate|sepia)\b",
    ),
]

_SCSS_TOKENS = [
    ("comment_line", r"//[^\n]*"),
    *_CSS_TOKENS,
    ("decorator", r"&|%[\w-]+"),
    ("variable", r"\$[\w-]+"),
    (
        "keyword",
        r"\b(?:mixin|include|extend|import|use|forward|function|"
        r"return|if|else|each|for|while|through|from|to|"
        r"in|warn|error|debug|with|as)\b",
    ),
]

_JSON_TOKENS = [
    ("comment_line", r"//[^\n]*"),
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("key", r'"(?:[^"\\]|\\.)*"(?=\s*:)'),
    ("string", r'"(?:[^"\\]|\\.)*"'),
    ("number", r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"),
    ("bool", r"\b(?:true|false)\b"),
    ("null", r"\bnull\b"),
    ("brace", r"[{}\[\]]"),
    ("colon", r":"),
    ("comma", r","),
]

_MD_TOKENS = [
    ("code_block", r"```[\s\S]*?```|`[^`\n]+`"),
    ("heading", r"^#{1,6}\s+.+$"),
    ("bold", r"\*\*(?:[^*]|\*(?!\*))+\*\*|__(?:[^_]|_(?!_))+__"),
    ("italic", r"(?<!\*)\*(?:[^*\n])+\*(?!\*)|(?<!_)_(?:[^_\n])+_(?!_)"),
    ("strikethrough", r"~~[^~\n]+~~"),
    ("link", r"\[([^\]]+)\]\([^)]+\)|\[([^\]]*)\]\[[^\]]*\]"),
    ("image", r"!\[([^\]]*)\]\([^)]+\)"),
    ("blockquote", r"^>\s*.+$"),
    ("list_item", r"^[ \t]*(?:[-*+]|\d+[.)]) "),
    ("hr", r"^(?:---+|\*\*\*+|___+)\s*$"),
    ("table", r"^\|.+\|$"),
    ("escape", r"\\[\\`*_{}\[\]()#+\-.!~|]"),
]

_YAML_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    ("string", r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    ("key", r"^[ \t]*[\w.-]+(?=\s*:)"),
    ("bool", r"\b(?:true|false|yes|no|on|off|True|False|YES|NO|ON|OFF)\b"),
    ("null", r"\b(?:null|~|Null|NULL)\b"),
    ("number", r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"),
    ("anchor", r"[&*][\w]+"),
    ("directive", r"^---$|^\.\.\.$"),
    ("special", r"[|>+-]"),
]

_SHELL_TOKENS = [
    ("shebang", r"^#!.*$"),
    ("comment_line", r"#[^\n]*"),
    (
        "string",
        r'"(?:[^"\\$`]|\\.|\$(?:\w+|\{[^}]+\})|`[^`]*`)*"|' r"'[^']*'|`[^`]*`",
    ),
    (
        "keyword",
        r"\b(?:if|then|else|elif|fi|case|esac|for|while|until|do|done|in|"
        r"function|return|exit|break|continue|local|export|readonly|declare|"
        r"typeset|unset|set|shift|source|\.|trap|eval|exec|select|time|"
        r"coproc)\b",
    ),
    (
        "builtin",
        r"\b(?:echo|printf|read|cd|ls|pwd|mkdir|rm|cp|mv|cat|grep|sed|awk|"
        r"find|sort|uniq|wc|head|tail|curl|wget|chmod|chown|sudo|su|test|"
        r"true|false|:|alias|bg|bind|builtin|caller|command|compgen|complete|"
        r"compopt|dirs|disown|enable|fc|fg|getopts|hash|help|history|"
        r"jobs|kill|let|logout|mapfile|popd|pushd|readarray|times|type|"
        r"ulimit|umask|unalias|wait|xargs|tee|cut|tr|touch|stat|file|"
        r"basename|dirname|realpath|readlink|which|whereis|type|env|"
        r"printenv|uname|hostname|whoami|id|ps|top|kill|pkill|pgrep|"
        r"systemctl|service|journalctl|apt|yum|dnf|brew|pip|npm|git)\b",
    ),
    ("variable", r"\$(?:\w+|\{[^}]+\}|\d|\$|\?|!|#|@|\*)"),
    ("operator", r"[|&<>!;(){}]|&&|\|\||>>|<<"),
    ("number", r"\b\d+\b"),
]

_XML_TOKENS = [
    ("comment_block", r"<!--[\s\S]*?-->"),
    ("cdata", r"<!\[CDATA\[[\s\S]*?\]\]>"),
    ("pi", r"<\?[\s\S]*?\?>"),
    ("tag_open", r"</?\s*[\w:.-]+"),
    ("attr_name", r"[ \t][\w:.-]+(?=\s*=)"),
    ("string", r'"[^"]*"|\'[^\']*\''),
    ("entity", r"&(?:#\d+|#x[0-9a-fA-F]+|[\w]+);"),
    ("bracket", r"[<>/]"),
]

_SQL_TOKENS = [
    ("comment_line", r"--[^\n]*"),
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("string", r"'(?:[^'\\]|\\.)*'|\"[^\"]*\""),
    (
        "keyword",
        r"\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|"
        r"ALTER|DROP|TABLE|INDEX|VIEW|DATABASE|SCHEMA|TRIGGER|PROCEDURE|"
        r"FUNCTION|CURSOR|DECLARE|BEGIN|END|IF|THEN|ELSE|ELSIF|ELSEIF|CASE|"
        r"WHEN|ELSE|END|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|NATURAL|ON|"
        r"AS|DISTINCT|GROUP|ORDER|BY|HAVING|LIMIT|OFFSET|FETCH|UNION|ALL|"
        r"INTERSECT|EXCEPT|EXISTS|IN|NOT|AND|OR|IS|NULL|BETWEEN|LIKE|ILIKE|"
        r"SIMILAR|PRIMARY|FOREIGN|UNIQUE|CHECK|DEFAULT|CONSTRAINT|KEY|"
        r"REFERENCES|CASCADE|RESTRICT|DEFERRABLE|DEFERRED|COMMIT|ROLLBACK|"
        r"TRANSACTION|SAVEPOINT|GRANT|REVOKE|WITH|RECURSIVE|WINDOW|PARTITION|"
        r"OVER|PRECEDING|FOLLOWING|UNBOUNDED|CURRENT|ROW|ROWS|RANGE|"
        r"MATERIALIZED|TEMPORARY|TEMP|TRUNCATE|VACUUM|ANALYZE|EXPLAIN|"
        r"SHOW|SET|RESET|COPY|LISTEN|NOTIFY|LOCK|UNLOCK|FLUSH)\b",
    ),
    (
        "builtin",
        r"\b(?:COUNT|SUM|AVG|MAX|MIN|ABS|CEIL|CEILING|FLOOR|ROUND|TRUNC|"
        r"COALESCE|NULLIF|CAST|CONVERT|CONCAT|CONCAT_WS|LENGTH|CHAR_LENGTH|"
        r"SUBSTRING|SUBSTR|LEFT|RIGHT|TRIM|LTRIM|RTRIM|UPPER|LOWER|"
        r"REPLACE|REGEXP_REPLACE|SPLIT_PART|POSITION|STRPOS|OVERLAY|"
        r"TO_CHAR|TO_DATE|TO_TIMESTAMP|NOW|CURRENT_DATE|CURRENT_TIME|"
        r"CURRENT_TIMESTAMP|DATE_PART|DATE_TRUNC|EXTRACT|AGE|"
        r"ISNULL|IFNULL|NVL|DECODE|IIF|CHOOSE|"
        r"ROW_NUMBER|RANK|DENSE_RANK|NTILE|PERCENT_RANK|CUME_DIST|"
        r"FIRST_VALUE|LAST_VALUE|NTH_VALUE|LAG|LEAD|"
        r"ARRAY_AGG|STRING_AGG|JSON_AGG|JSONB_AGG|"
        r"GENERATE_SERIES|UNNEST|ARRAY|ROW)\b",
    ),
    ("number", r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
]

_TOML_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    ("string",
     r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\]|\\.)*"|\'[^\']*\''),
    ("table", r"^\[+[^\]]+\]+"),
    ("key", r"^[ \t]*[\w.-]+(?=\s*=)"),
    ("bool", r"\b(?:true|false)\b"),
    (
        "datetime",
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}"
        r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?",
    ),
    ("number", r"[+-]?\b(?:inf|nan|\d[\d_]*(?:[._]\d[\d_]*)*(?:[eE][+-]?\d+)?)\b"),
]

_INI_TOKENS = [
    ("comment_line", r"[;#][^\n]*"),
    ("section", r"^\[[^\]]+\]"),
    ("key", r"^[ \t]*[\w.-]+(?=\s*=)"),
    ("string", r'"[^"]*"|\'[^\']*\''),
    ("bool", r"\b(?:true|false|yes|no|on|off|True|False|1|0)\b"),
    ("number", r"\b\d+(?:\.\d+)?\b"),
    ("variable", r"\$\{?[\w]+\}?|\$\([\w]+\)"),
]

_RUST_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("comment_line", r"//[^\n]*"),
    ("string", r'r#*"[\s\S]*?"#*|"(?:[^"\\]|\\.)*"|b"(?:[^"\\]|\\.)*"'),
    ("lifetime", r"'[a-zA-Z_]\w*(?!\w)"),
    ("char", r"'(?:[^'\\]|\\.)'"),
    ("decorator", r"#!?\[[^\]]*\]"),
    ("macro", r"\b\w+!(?=\s*[\[({\"])"),
    (
        "keyword",
        r"\b(?:as|async|await|break|const|continue|crate|dyn|else|enum|"
        r"extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|"
        r"ref|return|self|Self|static|struct|super|trait|true|type|"
        r"union|unsafe|use|where|while|abstract|become|box|do|"
        r"final|macro|override|priv|try|typeof|unsized|virtual|yield)\b",
    ),
    (
        "builtin",
        r"\b(?:i8|i16|i32|i64|i128|isize|u8|u16|u32|u64|u128|usize|"
        r"f32|f64|bool|char|str|String|Vec|HashMap|HashSet|BTreeMap|"
        r"BTreeSet|LinkedList|VecDeque|BinaryHeap|Option|Result|Box|Rc|Arc|"
        r"Cell|RefCell|Mutex|RwLock|Cow|MaybeUninit|ManuallyDrop|"
        r"Some|None|Ok|Err|"
        r"Default|Debug|Display|Clone|Copy|Drop|Fn|FnMut|FnOnce|"
        r"Iterator|IntoIterator|FromIterator|Extend|PartialEq|Eq|"
        r"PartialOrd|Ord|Hash|Sized|Send|Sync|Unpin|"
        r"println|print|eprintln|eprint|format|write|writeln|"
        r"assert|assert_eq|assert_ne|debug_assert|todo|unimplemented|"
        r"unreachable|panic|dbg|vec|include|include_str|include_bytes|"
        r"concat|env|file|line|column|module_path)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F][0-9a-fA-F_]*(?:_?[iu]\d+)?\b|"
        r"\b0[oO][0-7][0-7_]*(?:_?[iu]\d+)?\b|"
        r"\b0[bB][01][01_]*(?:_?[iu]\d+)?\b|"
        r"\b\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?(?:_?[fiu]\d*)?\b",
    ),
    ("func_def", r"(?<=fn\s)[\w]+"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
    ("operator", r"[+\-*/%^&|~!<>]=?|&&|\|\||::|\.\.|\.\.=|=>|->"),
]

_GO_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("comment_line", r"//[^\n]*"),
    ("string", r'`[^`]*`|"(?:[^"\\]|\\.)*"'),
    (
        "keyword",
        r"\b(?:break|case|chan|const|continue|default|defer|else|fallthrough|"
        r"for|func|go|goto|if|import|interface|map|package|range|return|"
        r"select|struct|switch|type|var)\b",
    ),
    (
        "builtin",
        r"\b(?:true|false|nil|iota|make|new|len|cap|append|copy|delete|"
        r"close|panic|recover|print|println|complex|real|imag|"
        r"string|int|int8|int16|int32|int64|uint|uint8|uint16|uint32|uint64|"
        r"uintptr|float32|float64|complex64|complex128|bool|byte|rune|"
        r"any|comparable|error|fmt|os|io|log|time|sync|context|"
        r"net|http|json|math|sort|strings|strconv|bufio|bytes|unicode|"
        r"regexp|path|filepath|runtime|reflect|errors|testing)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F]+\b|\b0[oO][0-7]+\b|\b0[bB][01]+\b|"
        r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[i]?\b",
    ),
    ("func_def", r"(?<=func\s)[\w]+"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
    ("decorator", r"//go:[\w]+"),
]

_C_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("comment_line", r"//[^\n]*"),
    ("string", r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    (
        "decorator",
        r"#\s*(?:include|define|ifdef|ifndef|if|else|elif|"
        r"endif|pragma|error|warning|undef|line)[^\n]*",
    ),
    (
        "keyword",
        r"\b(?:auto|break|case|char|const|continue|default|do|double|else|"
        r"enum|extern|float|for|goto|if|inline|int|long|register|restrict|"
        r"return|short|signed|sizeof|static|struct|switch|typedef|union|"
        r"unsigned|void|volatile|while|nullptr|true|false|"
        r"alignas|alignof|and|and_eq|asm|bitand|bitor|compl|concept|"
        r"co_await|co_return|co_yield|consteval|constexpr|constinit|"
        r"decltype|delete|export|final|friend|mutable|new|noexcept|not|"
        r"not_eq|operator|or|or_eq|override|private|protected|public|"
        r"reinterpret_cast|requires|static_assert|static_cast|template|"
        r"this|thread_local|throw|try|catch|typeid|typename|using|"
        r"virtual|wchar_t|xor|xor_eq)\b",
    ),
    (
        "builtin",
        r"\b(?:int8_t|int16_t|int32_t|int64_t|uint8_t|uint16_t|uint32_t|"
        r"uint64_t|size_t|ptrdiff_t|intptr_t|uintptr_t|FILE|NULL|"
        r"printf|scanf|fprintf|sprintf|snprintf|sscanf|fopen|fclose|"
        r"malloc|calloc|realloc|free|memcpy|memmove|memset|memcmp|"
        r"strlen|strcpy|strcat|strcmp|strncmp|strstr|strchr|strtok|"
        r"atoi|atof|atol|strtod|strtol|abs|fabs|sqrt|pow|sin|cos|tan|"
        r"std|cout|cin|endl|vector|string|map|set|pair|tuple|array|"
        r"deque|list|queue|stack|bitset|algorithm|iterator|memory|"
        r"iostream|fstream|sstream|iomanip|chrono|thread|mutex|future|"
        r"optional|variant|any|span|string_view|function|bind|ref|cref|"
        r"make_shared|make_unique|shared_ptr|unique_ptr|weak_ptr|move|"
        r"forward|declval|exchange|swap|sort|find|fill|transform|"
        r"begin|end|rbegin|rend|cbegin|cend|size|empty|push_back|"
        r"emplace_back|pop_back|insert|erase|clear|reserve|resize)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F]+[uUlL]*\b|\b0[0-7]+[uUlL]*\b|"
        r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLuU]*\b",
    ),
    ("func_def", r"(?<!\w)([A-Za-z_]\w*)\s*(?=\()"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
    ("operator", r"[+\-*/%^&|~!<>]=?|&&|\|\||::|<<|>>|\.\.\.|->"),
]

_CPP_TOKENS = _C_TOKENS

_JAVA_TOKENS = [
    ("comment_block", r"/\*[\s\S]*?\*/"),
    ("comment_line", r"//[^\n]*"),
    ("string", r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|"""[\s\S]*?"""'),
    ("decorator", r"@[\w.]+"),
    (
        "keyword",
        r"\b(?:abstract|assert|boolean|break|byte|case|catch|char|class|"
        r"const|continue|default|do|double|else|enum|extends|final|finally|"
        r"float|for|goto|if|implements|import|instanceof|int|interface|long|"
        r"native|new|package|private|protected|public|return|short|static|"
        r"strictfp|super|switch|synchronized|this|throw|throws|transient|"
        r"try|var|void|volatile|while|yield|record|sealed|permits|non-sealed)\b",
    ),
    (
        "builtin",
        r"\b(?:String|Integer|Long|Double|Float|Boolean|Character|Byte|Short|"
        r"Object|Class|Number|Math|System|Runtime|Thread|Runnable|Comparable|"
        r"Iterable|Iterator|Collection|List|ArrayList|LinkedList|Map|HashMap|"
        r"TreeMap|Set|HashSet|TreeSet|Queue|Deque|ArrayDeque|PriorityQueue|"
        r"Optional|Stream|Collectors|Arrays|Collections|Objects|"
        r"StringBuilder|StringBuffer|Scanner|BufferedReader|"
        r"IOException|RuntimeException|Exception|Error|"
        r"NullPointerException|IllegalArgumentException|IndexOutOfBoundsException|"
        r"true|false|null)\b",
    ),
    (
        "number",
        r"\b0[xX][0-9a-fA-F]+[lL]?\b|\b0[bB][01]+[lL]?\b|"
        r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFdDlL]?\b",
    ),
    ("func_def", r"(?<!\w)([A-Za-z_]\w*)\s*(?=\()"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
]

_GRAPHQL_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    ("string", r'"""[\s\S]*?"""|"(?:[^"\\]|\\.)*"'),
    (
        "keyword",
        r"\b(?:query|mutation|subscription|fragment|on|directive|schema|"
        r"scalar|type|interface|union|enum|input|extend|implements|"
        r"true|false|null)\b",
    ),
    ("type_kw", r"\b(?:String|Int|Float|Boolean|ID)\b"),
    ("decorator", r"@[\w]+"),
    ("class_name", r"\b[A-Z][A-Za-z0-9_]*\b"),
    ("number", r"\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
]

_DOCKER_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    (
        "keyword",
        r"^(?:FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|"
        r"USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b",
    ),
    ("string", r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    ("variable", r"\$(?:\w+|\{[^}]+\})"),
    ("number", r"\b\d+\b"),
]

_MAKEFILE_TOKENS = [
    ("comment_line", r"#[^\n]*"),
    (
        "keyword",
        r"^(?:include|override|export|unexport|sinclude|-include|"
        r"define|endef|ifdef|ifndef|ifeq|ifneq|else|endif|"
        r"vpath|VPATH)\b",
    ),
    ("func_call", r"\$\([A-Za-z_]\w*"),
    ("variable", r"\$[({@%<^+?*]|\$\$"),
    ("func_def", r"^[\w.\-/]+(?=\s*:(?!=))"),
    ("string", r'"[^"]*"|\'[^\']*\''),
    ("number", r"\b\d+\b"),
]

LANG_RE: dict[str, re.Pattern] = {}
_LANG_TOKENS = {
    "javascript": _JS_TOKENS,
    "typescript": _TS_TOKENS,
    "python": _PY_TOKENS,
    "html": _HTML_TOKENS,
    "vue": _HTML_TOKENS,
    "svelte": _HTML_TOKENS,
    "css": _CSS_TOKENS,
    "scss": _SCSS_TOKENS,
    "less": _SCSS_TOKENS,
    "json": _JSON_TOKENS,
    "markdown": _MD_TOKENS,
    "yaml": _YAML_TOKENS,
    "shell": _SHELL_TOKENS,
    "xml": _XML_TOKENS,
    "sql": _SQL_TOKENS,
    "toml": _TOML_TOKENS,
    "ini": _INI_TOKENS,
    "rust": _RUST_TOKENS,
    "go": _GO_TOKENS,
    "c": _C_TOKENS,
    "cpp": _CPP_TOKENS,
    "java": _JAVA_TOKENS,
    "graphql": _GRAPHQL_TOKENS,
    "docker": _DOCKER_TOKENS,
    "makefile": _MAKEFILE_TOKENS,
}


def _compile_all_patterns() -> None:
    """Pré-compila todos os padrões regex para uso no highlighter."""
    for lang, tokens in _LANG_TOKENS.items():
        seen: dict[str, int] = {}
        unique_tokens = []
        for name, pattern in tokens:
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            unique_tokens.append((name, pattern))
        try:
            LANG_RE[lang] = _pat(unique_tokens)
        except re.error as e:
            logger.warning("Pattern compile error for %s: %s", lang, e)

_compile_all_patterns()
