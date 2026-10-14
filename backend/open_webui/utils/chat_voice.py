"""Public attribution for browser-submitted voice transcripts."""


def public_voice_provenance(message: dict, author_id: str | None) -> dict | None:
    """Keep only public fields bound to the canonical message author."""
    meta = message.get('meta') if isinstance(message.get('meta'), dict) else {}
    provenance = meta.get('voice_provenance') or message.get('voice_provenance')
    if not isinstance(provenance, dict) or provenance.get('source') != 'browser':
        return None
    actor = provenance.get('user')
    if not author_id or not isinstance(actor, dict) or actor.get('id') != author_id:
        return None
    name = actor.get('name')
    return {
        'source': 'browser',
        'user': {'id': author_id, 'name': name if isinstance(name, str) and name else author_id},
    }
