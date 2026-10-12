"""Expand explicit source context without silently dropping text or following siblings."""
from copy import deepcopy


DEFAULT_ROLES = frozenset({'parent', 'scope', 'method', 'submethod', 'table', 'footnote', 'reference'})
SUBTREE_ROLES = frozenset({'method', 'submethod', 'reference'})


def expand_context(bundle, chunk_id, roles=None, max_blocks=60, max_chars=24000):
    if type(max_blocks) is not int or max_blocks < 0 or type(max_chars) is not int or max_chars < 0:
        raise ValueError('Context budgets must be nonnegative integers.')
    selected_roles = DEFAULT_ROLES if roles is None else frozenset(roles)
    chunks = {chunk['chunk_id']: chunk for chunk in bundle.get('chunks', [])}
    if chunk_id not in chunks:
        raise ValueError('Chunk does not exist in this bundle.')
    blocks = {block['block_id']: block for block in bundle.get('blocks', [])}
    children = {}
    for block in bundle.get('blocks', []):
        children.setdefault(block.get('parent_id'), []).append(block['block_id'])
    requested = []; seen = set(); omitted = []

    def omit(block_id):
        if block_id not in omitted:
            omitted.append(block_id)

    def add(block_id):
        if block_id not in blocks:
            omit(block_id)
        elif block_id not in seen:
            seen.add(block_id)
            requested.append(block_id)

    def parents(block_id):
        path = set()
        while block_id is not None:
            if block_id in path:
                omit(block_id)
                return
            path.add(block_id)
            add(block_id)
            block_id = blocks[block_id].get('parent_id') if block_id in blocks else None

    def subtree(block_id):
        # An explicit stack also handles deeply nested documents without recursion limits.
        stack = [(block_id, frozenset())]
        while stack:
            current, ancestors = stack.pop()
            if current in ancestors:
                omit(current)
                continue
            add(current)
            if current in blocks:
                stack.extend((child, ancestors | {current})
                             for child in reversed(children.get(current, [])))

    chunk = chunks[chunk_id]
    for block_id in chunk.get('block_ids', []):
        add(block_id)
    if 'parent' in selected_roles:
        parents(chunk.get('parent_block_id'))
    for link in bundle.get('links', []):
        if link['chunk_id'] != chunk_id or link['role'] not in selected_roles:
            continue
        target = link['target_block_id']
        if link['role'] == 'parent':
            parents(target)
        elif link['role'] in SUBTREE_ROLES:
            subtree(target)
        else:
            add(target)
    result = []; characters = 0
    for block_id in requested:
        block = blocks[block_id]
        length = len(block['text_raw'])
        if len(result) >= max_blocks or characters + length > max_chars:
            omit(block_id)
            continue
        result.append(deepcopy(block))
        characters += length
    return {'chunk_id': chunk_id, 'blocks': result, 'complete': not omitted,
            'omitted': omitted, 'characters': characters}
