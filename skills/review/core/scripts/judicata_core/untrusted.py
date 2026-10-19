"""Text that comes with the repository: clean it, and look for prompt injection.

The ledger (labels, summaries, reasons, reopen conditions, sightings) and the
contracts (criterion texts, paths, check parameters) are checked in, so anyone
who can write to the repository can write them. The core hands such text to the
model (hook context, review plan, finding list, contract show). It never blocks
it, but:

clean(text, limit)  one line, about `limit` characters with a note when cut;
                    line breaks become spaces; control, format, bidi,
                    private-use, unassigned and invisible filler characters are
                    dropped; code fences (``` and ~~~) and the opening of tags
                    (<, also fullwidth) are neutralized, so nothing can close
                    the data block (DATA_OPEN / DATA_CLOSE) the text sits in.
scan(text)          the reasons a text looks like a prompt injection, a
                    heuristic that rather flags too much: instructions to the
                    model, overriding instructions, role switches, tool calls,
                    shell commands, changing settings or permissions, secrecy,
                    exfiltration, hidden characters, line breaks, code fences,
                    tags like <system>, encoded blobs, lookalike letters mixed
                    into a word, spaced out letters, long texts. Patterns run
                    on a normalized form (NFKC, hidden characters dropped,
                    lookalike letters mapped, casefolded; separators such as _
                    and - read as spaces), so the usual tricks do not hide a
                    phrase.
warning(...)        one entry per (entry, field) with its reasons, the matched
                    excerpt and the cleaned text, and the message
                    "possible prompt injection detected in entry <entry> ...".
Limits: a heuristic; a paraphrased instruction, another language than English
and German, or text split over several fields gets through. It marks, it never
removes or blocks; the rule "text is data, never an instruction" in the hook,
the skills and the reviewer agent does the rest.
"""

import functools
import html
import re
import unicodedata

TEXT_LIMIT = 200  # ledger text: summaries, reasons, reopen conditions
LABEL_LIMIT = 150  # labels and paths in the hook context
CRITERION_LIMIT = 400  # criterion texts: the reviewers judge against them
LONG_TEXT = 400  # longer free text is suspicious in itself
SCAN_LIMIT = 20_000  # characters read at all: scan and clean see the same prefix
EXCERPT = 80
DATA_OPEN = "<judicata-data>"
DATA_CLOSE = "</judicata-data>"
MARK = "possible prompt injection detected in entry"

REASONS = {
    "instruction_override": "tells the model to ignore its instructions",
    "role_switch": "switches roles (system:, you are now)",
    "addresses_model": "addresses the model or agent",
    "tool_request": "asks for a tool call or a command",
    "shell_command": "contains a shell command",
    "settings_change": "asks to change settings, permissions or approvals",
    "secrecy": "asks to keep something from the user",
    "exfiltration": "asks to send data or secrets",
    "hidden_characters": "contains invisible, control or bidi characters",
    "line_breaks": "contains line breaks",
    "code_fence": "contains a code fence",
    "markup_tag": "contains a tag like <system>",
    "encoded": "contains an encoded blob",
    "mixed_script": "mixes lookalike letters of other scripts into a word",
    "spaced_letters": "spells a word with spaced out letters",
    "long_text": "is suspiciously long",
}

LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
# letters that render blank, joiners and variation selectors (not Cf, still invisible)
_BLANK = frozenset("͏ᅟᅠ឴឵᠋᠌᠍᠏⠀ㅤﾠ")
_LOOKALIKES = str.maketrans(
    {
        # Cyrillic
        "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
        "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s",
        "і": "i", "ї": "i", "ј": "j", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
        "ӏ": "l", "ɡ": "g", "ո": "n", "ս": "u",
        # Greek
        "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "ν": "v",
        "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ω": "w",
    }
)  # fmt: skip
_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u00b4": "'", "`": "'"})
_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "COPTIC")


def hidden(char: str) -> bool:
    """Characters that do not show (or reorder what does): drop and flag them."""
    if char in LINE_BREAKS or char == "\t":
        return False
    code = ord(char)
    if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF or char in _BLANK:
        return True
    return unicodedata.category(char) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")


_FENCE = re.compile(r"[`｀]{3,}|[~～]{3,}")
_TAG_OPEN = re.compile("[<＜﹤〈〈⟨]")


def clean(value, limit: int | None = TEXT_LIMIT) -> str:
    """One harmless line of at most about `limit` characters (None: no cut)."""
    text = "" if value is None else str(value)
    size = len(text)
    kept = []
    for char in text[:SCAN_LIMIT]:  # what scan() looked at, nothing more
        if char in LINE_BREAKS or char == "\t":
            kept.append(" ")
        elif not hidden(char):
            kept.append(char)
    text = " ".join("".join(kept).split())
    text = _FENCE.sub(lambda m: "'''" if m.group()[0] in "`｀" else "~~", text)
    text = _TAG_OPEN.sub("‹", text)
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + f" [cut, {size} characters]"
    elif size > SCAN_LIMIT:
        text += f" [cut, {size} characters]"
    return text


def visible(value) -> str:
    """Text that must stay exact (a command shown for approval), with every
    line break and hidden character written as an escape: what you see runs."""
    text = "" if value is None else str(value)
    return "".join(_escape(c) if hidden(c) or c in LINE_BREAKS else c for c in text)


def _escape(char: str) -> str:
    code = ord(char)
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def _normal(text: str) -> str:
    """The form patterns run on: HTML entities decoded, NFKD without combining
    marks (also umlauts: patterns are written without them), nothing hidden,
    lookalikes mapped, casefolded."""
    text = unicodedata.normalize("NFKD", html.unescape(text))
    text = "".join(
        " " if c in LINE_BREAKS else c
        for c in text
        if not hidden(c) and not unicodedata.combining(c)
    )
    text = text.casefold().translate(_LOOKALIKES).translate(_QUOTES)
    return " ".join(text.split())


def _words(text: str) -> str:
    """Separators read as spaces: ignore_previous-instructions.md."""
    return " ".join(re.sub(r"[_\-./\\:|+*=~^]+", " ", text).split())


_I = re.IGNORECASE
_AI = (
    r"(?:ai|a\.i\.|assistant|claude|llm|chatbot|chatgpt|gpt|codex|gemini|copilot|"
    r"language model|ai model|ai agent|coding agent|ai reviewer|reviewer bot|ki)"
)
# (reason, pattern, which text: "norm" = normalized, "words" = separators as spaces)
# fmt: off
_PATTERNS = [
    # ignoring instructions
    ("instruction_override", r"\b(?:ignore|disregard|forget|override|overrule|bypass|neglect|discard)\b[^.!?]{0,40}?\b(?:instructions?|rules|prompts?|directions|guidelines|guardrails|constraints|polic(?:y|ies)|system prompt|everything (?:above|before))\b", "words"),
    ("instruction_override", r"\b(?:previous|prior|preceding|above|earlier|original|initial|your) (?:system )?(?:instructions|prompts?)\b", "words"),
    ("instruction_override", r"\b(?:real|actual|true|secret|hidden) (?:instructions|system prompt|orders|task)\b|\bnew (?:system prompt|orders)\b", "words"),
    ("instruction_override", r"\byour (?:new |real |actual |true )?(?:task|goal|job|mission|instructions?) (?:is|are|now)\b", "words"),
    ("instruction_override", r"\b(?:system prompt|prompt injection|jailbreak\w*)\b", "words"),
    ("instruction_override", r"\b(?:ignorier\w*|vergiss|vergesst|missachte\w*|ubergeh\w*|uberschreib\w*)\b[^.!?]{0,40}?\b(?:anweisung\w*|regeln?|vorgaben?|instruktion\w*|befehle?|prompts?|richtlinien?)\b", "words"),
    ("instruction_override", r"\b(?:vorherigen|bisherigen|obigen|vorigen|fruheren|alle) (?:anweisungen|instruktionen|prompts?|vorgaben)\b", "words"),
    # role switches
    ("role_switch", r"(?:^|[\s\"'(\[{>|*#])(?:system|assistant|developer|human|ai)\s*:", "norm"),
    ("role_switch", r"(?:^|[.!?]\s+)user\s*:", "norm"),
    ("role_switch", r"\byou are (?:now|no longer|actually|in fact|free|an? unrestricted)\b", "words"),
    ("role_switch", r"\b(?:act|behave|respond|answer|reply) as (?:if you were |an? |the )?(?:ai|assistant|admin|administrator|root|system|developer|unrestricted|jailbroken|dan)\b", "words"),
    ("role_switch", r"\bpretend (?:to be|you are|that you)\b", "words"),
    ("role_switch", r"\bfrom now on\b[, ]+(?:you|act|always|never|respond|reply|answer|ignore|only)\b", "words"),
    ("role_switch", r"\b(?:developer|god|admin|debug|jailbreak|dan|unrestricted) mode\b", "words"),
    ("role_switch", r"\[/?(?:inst|sys|system)\]|<<\s*/?sys\s*>>|<\|[a-z_]+\|>", "norm"),
    ("role_switch", r"(?:^|\s)#{1,6}\s*(?:system|instructions?|prompt|assistant)\b", "norm"),
    ("role_switch", r"\bdu bist (?:jetzt|nun|ab sofort|ab jetzt|eigentlich|ein[e]? (?:ki|assistent))\b", "words"),
    ("role_switch", r"\bab (?:sofort|jetzt)\b[, ]+(?:du|bist|antworte\w*|ignorier\w*|immer|nie)\b", "words"),
    # talking to the model
    ("addresses_model", rf"\b(?:dear|hey|hi|hello|attention|achtung|liebe[rs]?|hallo|note to(?: the)?|message (?:to|for)(?: the)?|instructions? (?:to|for)(?: the)?|hinweis an(?: die| den| das)?)\s+(?:\w+\s+)?(?:{_AI}|agents?|reviewers?)\b", "words"),
    ("addresses_model", rf"\b{_AI}\b[,:]?\s+(?:must|should|shall|needs? to|has to|have to|please|do not|don'?t|ignore|run|execute|call|use|write|delete|approve|tell|say|respond|reply|answer|remember|muss|soll|sollst|musst|bitte|fuhre|ignoriere|losche)\b", "words"),
    ("addresses_model", rf"\b(?:if you are|if you'?re|as an?|you are an?) (?:{_AI}|automated|agent)\b", "words"),
    ("addresses_model", r"\b(?:next|new|future|every|this|current|following) (?:session|chat|conversation|instance|run of claude)s?\b[^.!?]{0,30}\b(?:must|should|shall|has to|needs to|always|never)\b", "words"),
    ("addresses_model", r"\b(?:IMPORTANT|URGENT|ATTENTION|MANDATORY|OVERRIDE|INSTRUCTION|INSTRUCTIONS)\b\s*[:!]", "raw"),
    # tool calls and commands
    ("tool_request", r"\b(?:call|use|invoke|run|execute|trigger)\s+(?:the\s+|a\s+|this\s+|your\s+|any\s+)?(?:bash|shell|tools?|function calls?|webfetch|web fetch|websearch|web search|subagents?|sub agents?|mcp|mcp tools?|following command|following commands|this command|these commands|command line)\b", "words"),
    ("tool_request", r"\b(?:Bash|WebFetch|WebSearch|NotebookEdit|AskUserQuestion|SendMessage|TodoWrite|MultiEdit|SubagentHandback)\s*\(|\b(?:Bash|Write|Edit|Read|WebFetch|Grep|Glob) tool\b", "raw"),
    ("tool_request", r"tool_use|tool_call|tool_result|function_call|function_calls|antml|mcp__|\binvoke name\b", "norm"),
    ("tool_request", r"(?:^|[.!?;:]\s+|\bplease\s+|\bfirst\s*,?\s+|\bthen\s+)(?:run|execute|type|paste|enter|call)\s+(?:'|\"|\$\s|the command\b|this command\b|this:|:)", "norm"),
    ("addresses_model", r"\bbefore (?:reviewing|judging|grading|answering|responding|replying|you (?:review|judge|answer|report|reply|respond|continue|start|begin|proceed|go on))\b", "words"),
    ("tool_request", r"\b(?:please|immediately)\s*,?\s+(?:run|execute|call|invoke|type|paste|enter|approve|install|download|fetch)\b", "words"),
    ("tool_request", r"(?:^|[.!?;]\s+)(?:execute|invoke|approve|grant|curl|wget|upload|exfiltrate)\s+(?:the|this|these|all|every|a|an|it|them|now|following|my|our|your)\b", "words"),
    ("tool_request", r"\b(?:fuhre|starte|rufe|offne)\b[^.!?]{0,40}?\b(?:aus|auf|befehl\w*|kommando\w*|tool\w*|werkzeug\w*|skript\w*)\b", "words"),
    ("tool_request", r"\bbitte (?:fuhre|rufe|starte|losche|schreibe|andere|installiere|genehmige|bestatige)\b", "words"),
    # shell
    ("shell_command", r"\b(?:curl|wget)\s+(?:-\S+\s+)*(?:https?://|\S+\.\S+)", "norm"),
    ("shell_command", r"\|\s*(?:sudo\s+)?(?:ba|z|da|k|c|fi)?sh\b", "norm"),
    ("shell_command", r"\brm\s+-[a-z]*[rf]", "norm"),
    ("shell_command", r"\bsudo\s+\S", "norm"),
    ("shell_command", r"\bchmod\s+(?:\+|[0-7]{3,4}\b|-r\b|u\+|a\+)", "norm"),
    ("shell_command", r"\b(?:ba|z|da)?sh\s+-c\b|\b(?:python3?|node|perl|ruby|php)\s+-(?:c|e)\s", "norm"),
    ("shell_command", r"\bnc\s+(?:-\w+\s+)*\S+\s+\d+|/dev/tcp/|\bmkfifo\b|\bbase64\s+(?:-d|--decode)\b", "norm"),
    ("shell_command", r"\b(?:powershell|pwsh)(?:\.exe)?\b|\binvoke-expression\b|\biex\s*\(", "norm"),
    ("shell_command", r"\bgit\s+push\b[^.;]{0,30}(?:--force|\s-f\b)|\bgit\s+(?:reset\s+--hard|clean\s+-[a-z]*f)", "norm"),
    ("shell_command", r"~/\.ssh\b|\.ssh/(?:id_|authorized_keys)|/etc/(?:passwd|shadow|sudoers)\b|\bssh-keygen\b", "norm"),
    ("shell_command", r"\$\(\s*(?:curl|wget|cat|echo|whoami|id|env|printenv|base64|sh|bash|python3?|rm|ls|uname|hostname)\b|;\s*(?:rm|curl|wget|sh|bash|sudo|chmod|nc|python3?)\b", "norm"),
    ("shell_command", r"\b(?:crontab|systemctl|launchctl)\s|\bdd\s+if=|\bmkfs\b|:\(\)\s*\{|\beval\s+[\"']?\$", "norm"),
    ("shell_command", r">>?\s*~?/?(?:\.bashrc|\.zshrc|\.profile|\.bash_profile)\b", "norm"),
    # settings, permissions, approvals
    ("settings_change", r"\b(?:change|modify|edit|update|overwrite|disable|turn off|switch off|bypass|grant|add|set|enable|lower|relax|remove|extend)\b[^.!?]{0,30}?\b(?:settings\.json|settings\.local\.json|permission mode|permission settings|allowlist|allow list|allowed tools|deny list|sandbox|claude\.md|agents\.md|\.claude|guardrails|safety checks?|safety rules)\b", "norm"),
    ("settings_change", r"\byour (?:permissions|permission settings|claude settings|tool permissions)\b|\b(?:claude(?: code)?(?:'s)?|the agent'?s|the assistant'?s) (?:settings|config|configuration|permissions?)\b", "norm"),
    ("settings_change", r"\b(?:dangerously\w*|skip[- ]?permissions|bypasspermissions|bypass permissions|--no-verify|acceptedits|auto[- ]?approve\w*|always allow|allow all tools|yolo)\b", "norm"),
    ("settings_change", r"\bcontract approve\b|\bcontract change\b[^.;]{0,60}--confirm\b|--?commands-hash\b|\bcommands_hash\b|\bapprove (?:the|these|all|every|its|any|pending) (?:pending )?commands?\b", "norm"),
    ("settings_change", r"\b(?:andere|deaktiviere|erweitere|lockere)\b[^.!?]{0,30}?\b(?:einstellungen|rechte|berechtigungen|freigaben?|sandbox|hooks?)\b", "words"),
    # secrecy
    ("secrecy", r"\b(?:do not|don'?t|never|without)\s+(?:tell|telling|inform|informing|ask|asking|mention|mentioning|show|showing|notify|notifying|alert|alerting|warn|warning|reveal|revealing|report|reporting|confirm|confirming|consult|consulting)\b[^.!?]{0,20}?\b(?:the )?(?:user|human|developer|owner|operator|anyone|them)\b", "words"),
    ("secrecy", r"\b(?:silently|secretly|quietly|covertly)\s+(?:run|execute|approve|change|delete|send|install|add|remove|modify|edit|write|push)\b|\bkeep (?:this|it) (?:secret|hidden|between us)\b|\bthe user (?:must|should|need) not (?:know|see|be told)\b", "words"),
    ("secrecy", r"\bohne (?:zu )?(?:fragen|nachfrage|ruckfrage|bestatigung)\b|\b(?:sag|sage|erzahl|erzahle|zeig|zeige)\w* (?:dem|den) (?:nutzer|benutzer|user|anwender)\w* nicht\b|\bheimlich\b", "words"),
    # exfiltration
    ("exfiltration", r"\b(?:send|post|upload|leak|exfiltrate|forward|email|e-mail|transmit|reveal)\b[^.!?]{0,40}?(?:\bapi[ _-]?keys?\b|\btokens?\b|\bsecrets?\b|\bpasswords?\b|\bcredentials?\b|\.env\b|\bssh keys?\b|\bprivate keys?\b|\benv(?:ironment)? var(?:iable)?s?\b|\bcookies?\b|\bsystem prompt\b)", "norm"),
    ("exfiltration", r"!\[[^\]]*\]\(\s*\S+", "norm"),
    # markup the model may read as structure
    ("markup_tag", r"(?<![\w\]>)])[<‹〈⟨]\s*/?\s*(?:system|user|assistant|human|developer|instructions?|prompt|context|tools?|tool_use|tool_result|function_calls?|functions|invoke|parameter|antml[:\w-]*|im_start|im_end|important|admin|root|override|judicata[\w-]*|untrusted[\w-]*|data|document|documents|thinking|reminder|system-reminder|command[\w-]*|message|msg|policy|rules?)\b", "norm"),
]
# fmt: on
_COMPILED = [(r, re.compile(p, _I if t != "raw" else 0), t) for r, p, t in _PATTERNS]
_ENCODED = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_ESCAPED = re.compile(
    r"(?:\\x[0-9a-f]{2}){4,}|(?:\\u[0-9a-f]{4}){3,}|(?:%[0-9a-f]{2}){6,}", _I
)
_SPACED = re.compile(r"\b(?:[a-z][ ._*-]){5,}[a-z]\b")


def scan(value, *, long: int | None = LONG_TEXT, skip=()) -> list[tuple[str, str]]:
    """[(reason, excerpt)] for every reason the text looks like an injection."""
    if value is None:
        return []
    return list(_scan(str(value), long, tuple(skip)))


@functools.lru_cache(maxsize=4096)
def _scan(raw: str, long: int | None, skip: tuple) -> tuple:
    size = len(raw)
    raw = raw[:SCAN_LIMIT]  # clean() shows nothing beyond it either
    found: dict[str, str] = {}

    def hit(reason: str, text: str, start: int = 0, end: int = 0) -> None:
        if reason in skip or reason in found:
            return
        found[reason] = clean(text[max(0, start - 30) : end + 30], EXCERPT)

    marks = [i for i, c in enumerate(raw) if hidden(c)]
    if marks:
        codes = ", ".join(sorted({f"U+{ord(raw[i]):04X}" for i in marks})[:5])
        found["hidden_characters"] = codes
    breaks = [i for i, c in enumerate(raw) if c in LINE_BREAKS]
    if breaks:
        hit("line_breaks", raw, breaks[0], breaks[0])
    norm = _normal(raw)
    shown = "".join(" " if c in LINE_BREAKS else c for c in raw if not hidden(c))
    texts = {  # "raw": case kept (IMPORTANT:, Bash tool), nothing hidden
        "raw": unicodedata.normalize("NFKC", shown),
        "norm": norm,
        "words": _words(norm),
    }
    for reason, pattern, which in _COMPILED:
        if reason in found or reason in skip:
            continue
        match = pattern.search(texts[which])
        if match:
            hit(reason, texts[which], match.start(), match.end())
    fence = _FENCE.search(texts["raw"])
    if fence:
        hit("code_fence", texts["raw"], fence.start(), fence.end())
    for match in _ENCODED.finditer(texts["raw"]):
        blob = match.group()
        mixed = all(re.search(c, blob) for c in ("[a-z]", "[A-Z]", "[0-9]"))
        if mixed and ("/" not in blob or re.search("[+=]", blob)):  # not a path
            hit("encoded", texts["raw"], match.start(), match.end())
            break
    escaped = _ESCAPED.search(texts["raw"])
    if escaped:
        hit("encoded", texts["raw"], escaped.start(), escaped.end())
    spaced = _SPACED.search(norm)
    if spaced:
        hit("spaced_letters", norm, spaced.start(), spaced.end())
    for word in re.findall(r"\w+", raw):
        if word.isascii():
            continue
        scripts = {_script(c) for c in word if c.isalpha()} - {None}
        if len(scripts) > 1 and "LATIN" in scripts:
            hit("mixed_script", word, 0, len(word))
            break
    if (long is not None and size > long) or size > SCAN_LIMIT:
        found.setdefault("long_text", f"{size} characters")
    return tuple((reason, found[reason]) for reason in REASONS if reason in found)


def _script(char: str) -> str | None:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    return next((s for s in _SCRIPTS if name.startswith(s)), None)


# fields that name things (never "too long", a path is no encoded blob), fields
# that may span lines, and artifact code quoted by a reviewer
PATH_LIKE = (
    "label", "file", "paths", "path", "contract", "category", "criterion", "id",
    "address", "symbol", "fingerprint",
)  # fmt: skip
MULTILINE = ("text", "evidence", "quote")
CODE_SKIP = ("line_breaks", "code_fence", "shell_command", "encoded", "spaced_letters")


def options(field: str) -> dict:
    """How a field is scanned: scan() options by its name."""
    name = field.rsplit(".", 1)[-1]
    skip, long = [], LONG_TEXT
    if name in PATH_LIKE:
        long = None
        skip.append("encoded")
    if name in MULTILINE:
        skip.append("line_breaks")
    if name == "quote":
        skip += CODE_SKIP
    if field.startswith("sightings."):
        long = None  # the reviewer output schema bounds them
    return {"long": long, "skip": tuple(skip)}


def limit(field: str) -> int:
    """How long a cleaned field may be."""
    name = field.rsplit(".", 1)[-1]
    if name == "text":
        return CRITERION_LIMIT
    if name in PATH_LIKE:
        return 300
    return TEXT_LIMIT


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    if isinstance(value, dict):  # an address: its names (headings, symbol)
        return " ".join(
            " ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
            for k, v in value.items()
            if k not in ("kind", "paragraph")
        )
    return str(value)


def flagged(field: str, value, skip=()) -> bool:
    found = options(field)
    found["skip"] = (*found["skip"], *skip)
    return bool(scan(_text(value), **found))


def warning(entry: str, field: str, value, skip=()) -> dict | None:
    """The warning for one field of one entry, or None if nothing looks off."""
    found = options(field)
    found["skip"] = (*found["skip"], *skip)
    text = _text(value)
    hits = scan(text, **found)
    if not hits:
        return None
    entry = clean(entry, 120)
    reasons = [reason for reason, _ in hits]
    return {
        "entry": entry,
        "field": field,
        "reasons": reasons,
        "match": hits[0][1],
        "text": clean(text, limit(field)),
        "message": f"{MARK} {entry} ({field}: "
        + ", ".join(REASONS[r] for r in reasons)
        + ")",
    }


def collect(entry: str, fields: dict, skip=()) -> list[dict]:
    """Warnings for several fields of one entry."""
    found = []
    for field, value in fields.items():
        made = warning(entry, field, value, skip)
        if made is not None:
            found.append(made)
    return found


# fields of findings and decisions that carry free text (states.FIELDS + decisions)
FINDING_TEXT = ("label", "summary", "file", "category", "criterion", "contract")
DECISION_TEXT = ("reason", "reopen_condition")
SIGHTING_TEXT = ("summary", "evidence", "symbol", "quote")


def finding(entry: dict, info_event: str | None = None) -> list[dict]:
    """Warnings for a finding as the core shows it (public form or candidate):
    its own text and address, its decision, a previous decision and the
    sightings with their quotes."""
    name = (
        f"event {info_event}" if info_event else f"finding {entry.get('fingerprint')}"
    )
    fields = {k: entry.get(k) for k in FINDING_TEXT}
    if isinstance(entry.get("address"), dict):
        fields["address"] = entry["address"]
    found = collect(name, fields)
    seen = set()
    for key in ("decision", "previous_decision"):
        decision = entry.get(key)
        if not isinstance(decision, dict) or decision.get("event") in seen:
            continue
        seen.add(decision.get("event"))
        found += collect(
            f"event {decision.get('event')}",
            {k: decision.get(k) for k in DECISION_TEXT},
        )
    for sighting in entry.get("sightings") or []:
        if isinstance(sighting, dict):
            found += collect(
                f"run {sighting.get('run')}",
                {f"sightings.{k}": sighting.get(k) for k in SIGHTING_TEXT},
            )
    return found


IDS = ("fingerprint", "last_round", "round", "event", "kept", "ts")


def shown(entry: dict) -> dict:
    """A finding for output: every flagged text field replaced by its cleaned
    form, so a flagged text reaches the model only cleaned. Unflagged text
    stays as it is (data for the skills); warnings say what was flagged. Ids
    and timestamps from the ledger are always cleaned (a no-op for real ones),
    a text field of another type becomes its cleaned text."""
    out = dict(entry)
    for key in FINDING_TEXT:
        if key in out:
            out[key] = _field(key, out[key])
    _ids(out)
    if isinstance(out.get("history"), list):
        out["history"] = [
            {**h, "round": clean(h.get("round"), 80)} if isinstance(h, dict) else h
            for h in out["history"]
        ]
    address = out.get("address")
    if isinstance(address, dict) and flagged("address", address):
        out["address"] = {
            k: [clean(x, 300) for x in v] if isinstance(v, list) else
            (clean(v, 300) if isinstance(v, str) else v)
            for k, v in address.items()
        }  # fmt: skip
    for key in ("decision", "previous_decision"):
        if isinstance(out.get(key), dict):
            out[key] = _cleaned(out[key], DECISION_TEXT, "")
            _ids(out[key])
    if isinstance(out.get("sightings"), list):
        out["sightings"] = [
            _cleaned(s, SIGHTING_TEXT, "sightings.") if isinstance(s, dict) else s
            for s in out["sightings"]
        ]
    return out


def _cleaned(item: dict, keys, prefix: str) -> dict:
    out = dict(item)
    for key in keys:
        if key in out:
            out[key] = _field(prefix + key, out[key])
    return out


def _field(field: str, value):
    """A text field for output: cleaned if flagged or not a string at all."""
    if value is None:
        return value
    if not isinstance(value, str):
        return clean(_text(value), limit(field))
    return clean(value, limit(field)) if flagged(field, value) else value


def _ids(item: dict) -> None:
    for key in IDS:
        if isinstance(item.get(key), str):
            item[key] = clean(item[key], 80)


def evidence(entry: str, value, field: str = "evidence") -> tuple:
    """(evidence for output, warnings): every string in a check's evidence
    (command output, pattern hits) scanned like quoted code, cleaned if flagged."""
    found = []

    def walk(item, name):
        if isinstance(item, str):
            made = warning(entry, field, item, CODE_SKIP)
            if made is None:
                return item
            found.append(made)
            return clean(item, None)
        if isinstance(item, list):
            return [walk(x, name) for x in item]
        if isinstance(item, dict):
            return {k: walk(v, name) for k, v in item.items()}
        return item

    return walk(value, field), found


def criterion(contract_name: str, item: dict) -> list[dict]:
    """Warnings for a criterion: its text and the string parameters of its check.
    A command is a command (shown for approval): shell and tool reasons do not
    count for it; a regular expression is no tag, role or blob."""
    name = f"criterion {item.get('id')} of contract {contract_name}"
    found = collect(name, {"id": item.get("id"), "text": item.get("text")})
    params = item.get("params") or {
        k: v
        for k, v in item.items()
        if k not in ("id", "text", "check", "required", "hash")
    }
    for key, value in params.items():
        if not isinstance(value, str):
            continue
        skip = ("long_text",)
        if key == "command":
            skip += ("shell_command", "tool_request")
        if key in ("forbid", "require"):
            skip += ("encoded", "spaced_letters", "markup_tag", "role_switch")
        found += collect(name, {key: value}, skip)
    return found


def criterion_text(contract_name: str, item: dict) -> dict:
    """A criterion for output: its text cleaned if flagged, a flagged string
    parameter cleaned too, a command with hidden characters made visible
    (contract_name for symmetry with criterion())."""
    out = dict(item)
    if "text" in out:
        out["text"] = _field("text", out["text"])
    if "id" in out and isinstance(out["id"], str):
        out["id"] = _field("id", out["id"])
    if isinstance(out.get("params"), dict):
        out["params"] = {k: _param(k, v) for k, v in out["params"].items()}
    return out


def _param(key: str, value):
    if not isinstance(value, str):
        return value
    if key == "command":
        return visible(value)
    skip = ("long_text", "encoded", "spaced_letters", "markup_tag", "role_switch")
    return clean(value, None) if scan(value, long=None, skip=skip) else value


def judged(contract_name: str, entry: dict) -> dict:
    """A judgment criterion as review verdicts and round close show it
    (criterion, text, verdicts with reason and evidence quotes, decision):
    flagged texts cleaned, with warnings."""
    out = criterion_text(contract_name, dict(entry))
    found = collect(
        f"criterion {entry.get('criterion')} of contract {contract_name}",
        {"text": entry.get("text")},
    )
    verdicts = []
    for verdict in entry.get("verdicts") or []:
        if not isinstance(verdict, dict):
            verdicts.append(verdict)
            continue
        name = f"run {verdict.get('run')}"
        found += collect(name, {"verdicts.reason": verdict.get("reason")})
        verdict = _cleaned(verdict, ("reason",), "verdicts.")
        quotes = []
        for item in verdict.get("evidence") or []:
            if isinstance(item, dict):
                found += collect(
                    name,
                    {
                        "verdicts.file": item.get("file"),
                        "verdicts.quote": item.get("quote"),
                    },
                )
                item = _cleaned(item, ("file", "quote"), "verdicts.")
            quotes.append(item)
        verdicts.append({**verdict, "evidence": quotes})
    if "verdicts" in out:
        out["verdicts"] = verdicts
    decision = entry.get("decision")
    if isinstance(decision, dict):
        found += collect(
            f"event {decision.get('event')}", {"reason": decision.get("reason")}
        )
        out["decision"] = _cleaned(decision, ("reason",), "")
    out["warnings"] = found
    return out


def unmet(contract_name: str, items) -> list[dict]:
    """Unmet required criteria as round_closed or finish_confirmed stored them
    (criterion, text, findings with label and decision): flagged text cleaned,
    ids cleaned, warnings per criterion."""
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = f"criterion {item.get('criterion')} of contract {contract_name}"
        warnings = collect(name, {"text": item.get("text")})
        findings = []
        for entry in item.get("findings") or []:
            if isinstance(entry, dict):
                warnings += [w for w in finding(entry) if w not in warnings]
                entry = shown(entry)
            findings.append(entry)
        shown_item = criterion_text(contract_name, item)
        if isinstance(shown_item.get("criterion"), str):
            shown_item["criterion"] = clean(shown_item["criterion"], 60)
        out.append({**shown_item, "findings": findings, "warnings": warnings})
    return out


def contract(current) -> list[dict]:
    """Warnings for a contract file: its paths, models (reviewers and verifier)
    and every criterion."""
    found = collect(
        f"contract file {current.name}",
        {
            "paths": list(current.paths),
            "models": current.reviewers.get("models"),
            "verifier.model": current.verifier.get("model"),
        },
    )
    for item in current.criteria:
        found += criterion(
            current.name, {"id": item.id, "text": item.text, "params": item.params}
        )
    return found


def mark(warnings: list[dict]) -> str:
    """The mark in front of an entry in a text: [possible ... in entry X, Y]."""
    entries = list(dict.fromkeys(w["entry"] for w in warnings))
    if not entries:
        return ""
    shown = ", ".join(entries[:2]) + (
        f" and {len(entries) - 2} more" if len(entries) > 2 else ""
    )
    return f"[{MARK} {shown}] "
