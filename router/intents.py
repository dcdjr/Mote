"""Conservative fast paths for complete, explicit requests only."""
import re
from router.urls import normalize_url


def _screen_intent_text(query):
    # Literal copy payloads, pasted code and error bodies cannot request a capture.
    if re.match(r"\s*copy (?:this text|text)\s*:", query, re.I):
        return ""
    query = re.split(r"\b(?:error|code|text|traceback|message)\s*:", query, maxsplit=1, flags=re.I)[0]
    return re.sub(r"```[\s\S]*?```|`[^`]*`", "", query)


def explicit_screenshot_requested(query):
    """An instruction to capture, rather than a tutorial or general capability question."""
    query = _screen_intent_text(query)
    if _screen_opt_out(query) or _screen_howto(query):
        return False
    return bool(re.search(
        r"\b(?:take|grab|snap|capture|attach|send|use)\s+(?:(?:a|an|the|new|fresh|another|current|my)\s+){0,4}(?:screen\s*shot|screen\s*capture)\b|"
        r"^\s*(?:please\s+)?screenshot(?:\s+(?:my|the|this)\s+(?:screen|desktop|display))?[.!?]*\s*$|"
        r"\b(?:capture|grab)\s+(?:(?:my|the|this|current)\s+){0,2}(?:screen|desktop|display|monitor)\b", query, re.I))


def _screen_opt_out(query):
    return bool(re.search(
        r"\b(?:don't|do not|never)\s+(?:take|grab|capture|use|attach)\s+(?:(?:a|the|my|new)\s+){0,3}(?:screenshot|screen)|"
        r"\bwithout\s+(?:(?:taking|a|the|using|capturing|my)\s+){0,3}(?:screenshot|screen)", query, re.I))


def _screen_howto(query):
    return bool(re.match(
        r"\s*how (?:do|can|should) (?:i|we|you) (?:take|capture|save) (?:a |the )?screenshot\b", query, re.I))


def screen_capture_forbidden(query):
    intent = _screen_intent_text(query)
    return _screen_opt_out(intent) or _screen_howto(intent)


def screen_needed(query, context):
    intent = _screen_intent_text(query)
    if not intent or _screen_opt_out(intent) or _screen_howto(intent):
        return False
    # A request for a fresh screen always wins over supplied code or an older image.
    if explicit_screenshot_requested(intent):
        return True
    if context.get('image_attached') or context.get('text_attached'):
        return False
    if '```' in query or re.search(r'\b(?:error|code|text|traceback|message)\s*:\s*\S', query, re.I):
        return False
    return bool(re.search(
        r"\b(?:on (?:my |the |this )?screen|my (?:current )?screen|this (?:screen|page|window|popup|icon)|"
        r"(?:look at|inspect|check|describe|read|see|analy[sz]e) (?:my|the|this) (?:screen|desktop|display|monitor))\b|"
        r"^\s*(?:what does this mean|what am i looking at|where (?:do|should) i click)\??\s*$", query, re.I))


def screen_decision(query):
    return {"route": "answer", "reasoning": bool(re.search(
        r"\b(error|debug|diagnose|troubleshoot|broken|crash|fix|compare)\b", query, re.I)),
        "screenshot": True, "command": None}


def _single(text):
    def command(name, **arguments):
        return {'function': name, 'arguments': arguments}
    match = re.fullmatch(r'(?:set|change) (?:the |my |system )?volume (?:to |at )?(\d{1,3})(?:\s*%| percent)?', text, re.I)
    if match:
        level = int(match[1])
        return command('set_volume', level_percent=level) if level <= 100 else None
    match = re.fullmatch(r'(mute|unmute)(?: (?:the |my |system )?(?:audio|sound|volume))?', text, re.I)
    if match:
        return command('adjust_volume', operation=match[1].lower())
    match = re.fullmatch(r'(?:turn|adjust) (?:the |my |system )?volume (up|down)', text, re.I)
    if match:
        return command('adjust_volume', operation='increase' if match[1].lower() == 'up' else 'decrease')
    match = re.fullmatch(r'open (?:the )?(.+?) settings', text, re.I)
    if match:
        page = match[1].lower().replace('-', '').replace(' ', '_')
        if page in {'display', 'sound', 'bluetooth', 'network', 'wifi', 'privacy', 'accessibility', 'windows_update'}:
            return command('open_windows_settings', page=page)
    match = re.fullmatch(r'(?:open|launch|start|visit|go to)\s+(.+)', text, re.I)
    if match:
        target = match[1].strip('"\'')
        explicit_app = re.fullmatch(r'(?:the )?(?:app|application)\s+(.+)', target, re.I)
        if explicit_app:
            target = explicit_app[1]
        else:
            try:
                return command('open_website', url=normalize_url(target))
            except ValueError:
                pass
        # Full sentences, shell syntax, relative references and unresolved clauses go to the model.
        if (not re.search(r'\b(?:and|then|if|when|after|before|unless|because|with|to|it|that|this|website|site)\b', target, re.I)
                and re.fullmatch(r'[\w][\w +().&\-]{0,119}', target)
                and not target.lower().endswith(('.exe', '.bat', '.cmd', '.ps1', '.com'))):
            target = re.sub(r'^the ', '', target, flags=re.I)
            friendly = {'file explorer': 'file_explorer', 'windows settings': 'settings',
                        'edge browser': 'edge', 'chrome browser': 'chrome', 'firefox browser': 'Firefox'}
            target = friendly.get(target.casefold(), target)
            builtin = {'notepad', 'calculator', 'chrome', 'edge', 'file_explorer', 'settings', 'spotify'}
            return command('open_application', application=target.casefold() if target.casefold() in builtin else target)
    return None


def fast_selection(query):
    text = query.strip()
    text = re.sub(r'^please\s+', '', text, flags=re.I)
    text = re.sub(r'\s+please$', '', text, flags=re.I)
    # Copy payloads may themselves contain action words; keep them verbatim.
    match = re.fullmatch(r'copy (?:this text|text)\s*:\s*(.+)', query.strip(), re.I | re.S)
    if match:
        return {'function': 'copy_text', 'arguments': {'text': match[1]}}
    clauses = re.split(r'\s+(?:and(?: then)?|then)\s+(?=(?:open|launch|start|visit|go to|set|change|mute|unmute|turn|adjust)\b)|;\s*', text, flags=re.I)
    if not 1 <= len(clauses) <= 8:
        return None
    selections = [_single(clause.strip()) for clause in clauses]
    if any(item is None for item in selections):
        return None
    return selections[0] if len(selections) == 1 else {'commands': selections}
