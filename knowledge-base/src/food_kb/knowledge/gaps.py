"""Explicit source gaps; absence of evidence is never absence of requirements."""
from ..common import identity

KINDS = {'missing_body', 'historical_version_missing', 'missing_pages', 'unreadable_text',
         'source_conflict', 'unresolved_condition', 'review_pending', 'unregistered_index_rows',
         'identity_unverified', 'version_policy_missing', 'reference_unresolved', 'unsupported_text'}


def gap(kind, target_kind, target_id, reason, evidence, affected_refs=None):
    if kind not in KINDS:
        raise ValueError('Unknown gap kind')
    target = {'kind': target_kind, 'id': target_id}
    return {'gap_id': identity('version-gap', kind, target, reason, evidence), 'kind': kind,
            'target': target, 'reason': reason, 'evidence': evidence,
            'affected_refs': affected_refs or [target], 'state': 'open', 'executable': False}


def affected_gaps(snapshot, targets):
    keys = {(t['kind'], t['id']) for t in targets}
    return [g for g in snapshot.get('gaps', [])
            if any((t['kind'], t['id']) in keys for t in [g['target'], *g.get('affected_refs', [])])]
