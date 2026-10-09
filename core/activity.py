"""User-facing activity notices, separate from internal routing diagnostics."""
import re


def activity_notice(text, assistant_name='arienx'):
    text=str(text);stripped=text.strip()
    complete=re.match(r'^(?:SYS:\s*)?\[AgentComplete\]\s+status=(\w+)',stripped,re.I)
    if complete:
        status=complete.group(1).lower()
        if status in ('verified','completed','success','done'):return 'SYS: Task completed.'
        if status=='observed':return 'SYS: Task finished.'
        if status=='failed':return 'ERR: Task failed.'
        if status in ('needs_confirm','needs_confirmation'):return 'SYS: Task awaiting confirmation.'
        return None
    if re.match(r'^(?:SYS:\s*)?\[(?:Agent[^\]]*|Worker[^\]]*|Planner|Router|Model)\]',stripped,re.I):
        return None
    if stripped.lower().startswith(('you:',str(assistant_name).lower()+':','jarvis:','[web]:')):
        return text
    if re.match(r'^(ERR|WARN|FILE|NET):',stripped,re.I):return text
    important=r'\b(interrupt(?:ed|ion)?|completed|done|finished|cancelled|canceled|failed|error|warning|awaiting|confirmation|permission|unavailable|disconnected|reconnect(?:ed|ing)?|paused|pending|saved|deleted|renamed|muted|unmuted|awake|sleeping|asleep|connected|offline|retry(?:ing)?)\b|not connected|could not|timed out'
    return text if re.search(important,stripped,re.I) else None
