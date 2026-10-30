"""Opt-in prompt compaction and lossless joining of short text replies."""
import re


# Transport provenance stays in Tulpa's database and guards, not in the prompt.
TRANSPORT_FIELDS = {'source', 'conversation_id', 'received_at', 'send_request', 'reaction_epoch'}
SEPARATE_MESSAGES = re.compile(
    r'(?:分|拆|分别|逐条|依次|每次).{0,12}(?:发送|发给|发出|条消息|个气泡)'
    r'|(?:两|三|多|2|3|几)(?:条消息|条发|个气泡)|分开发|分条发|分别发|逐条发'
    r'|不要合并|别合并|一条一条|不要合成|别拼在一起'
    r'|separate\s+(?:messages|bubbles)|(?:send|reply).{0,30}(?:two|three|2|3|multiple)\s+(?:messages|bubbles)'
    r'|one\s+(?:message|bubble)\s+at\s+a\s+time', re.IGNORECASE)


def compact_message(message):
    if message.get('recalled'):
        return {k: message[k] for k in ('id', 'onebot_message_id', 'recalled') if k in message}
    return {k: v for k, v in message.items() if k not in TRANSPORT_FIELDS}


def represented_turn(record, messages):
    """Drop an archive pair only with matching source IDs AND complete text."""
    sources = set(record['sources'])
    if not sources:
        return False
    selected = [(i, m) for i, m in enumerate(messages)
                if str(m.get('onebot_message_id')) in sources]
    if ({str(m.get('onebot_message_id')) for _, m in selected} != sources
            or any(m.get('is_self') or m.get('recalled') for _, m in selected)
            or '\n'.join(m.get('content', '') for _, m in selected) != record['user']):
        return False
    # Only contiguous own replies after the last input can represent this turn.
    replies = []
    for m in messages[selected[-1][0] + 1:]:
        if not m.get('is_self') or m.get('recalled'):
            break
        replies.append(m.get('content', ''))
        if '\n'.join(replies) == record['assistant']:
            return True
    return False


def compact_context(payload, records):
    """Keep all semantic fields and archive turns not proven present recently."""
    recent = payload.get('context', {}).get('messages', [])
    history = [(r['user'], r['assistant']) for r in records if not represented_turn(r, recent)]
    new = payload.get('new_messages', [])
    new_ids = {m['id'] for m in new}
    # Remove current-message duplicates only when the complete records agree.
    # A difference (including recall state) must remain visible to the model.
    new_by_id = {m['id']: m for m in new}
    result = dict(payload)
    result['new_messages'] = [compact_message(m) for m in new]
    result['context'] = dict(payload.get('context', {}), messages=[compact_message(m) for m in recent
        if m['id'] not in new_ids or m != new_by_id[m['id']]])
    # The host already owns the wait/ack lifecycle; retain all semantic guidance.
    result['guidance'] = {k: v for k, v in payload.get('guidance', {}).items()
                          if k not in ('continue_with', 'resume')}
    return result, history


def join_short_text_bubbles(bubbles, incoming, limit=300):
    """Join with newlines; never truncate or relax Tulpa's argument validation."""
    if (not isinstance(bubbles, list) or not 2 <= len(bubbles) <= 3
            or SEPARATE_MESSAGES.search('\n'.join(incoming))):
        return bubbles
    # Addressing, mixed media, unknown fields and malformed queues go unchanged
    # to the framework for its original validation and dispatch semantics.
    if any(not isinstance(b, dict) or set(b) != {'kind', 'text'}
           or b['kind'] != 'text' or not isinstance(b['text'], str) or not b['text'].strip()
           for b in bubbles):
        return bubbles
    text = '\n'.join(b['text'] for b in bubbles)
    return [{'kind': 'text', 'text': text}] if len(text) <= limit else bubbles
