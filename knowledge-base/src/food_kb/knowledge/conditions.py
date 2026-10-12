"""Validate and interpret a small condition AST without executing input code."""
from datetime import date
from decimal import Decimal, InvalidOperation

from .templates import ALLOWED_FACTS, FACT_TYPES

_COMBINE = {'all', 'any', 'not'}
_LEAVES = {'eq', 'in', 'gt', 'ge', 'lt', 'le', 'range', 'exists'}
_ORDERED = {'number', 'date'}


def _value_type(value):
    if type(value) is bool:
        return 'boolean'
    if type(value) in (int, float, Decimal):
        return 'number'
    if isinstance(value, list):
        return 'set'
    return 'string'


def _typed(value, kind):
    if kind == 'string':
        if not isinstance(value, str):
            raise ValueError('Expected a string.')
        return value
    if kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('Expected a Boolean.')
        return value
    if kind == 'number':
        if type(value) is bool or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError('Expected a finite decimal.')
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Expected a finite decimal.')
        return number
    if kind == 'date':
        if not isinstance(value, str):
            raise ValueError('Expected an ISO date.')
        return date.fromisoformat(value)
    if kind == 'set':
        if not isinstance(value, list) or any(type(v) not in (str, int, float, bool) for v in value):
            raise ValueError('Expected a list of scalar members.')
        return value
    raise ValueError('Unsupported fact type.')


def validate_condition(ast):
    """Return diagnostics. ``None`` means an explicitly unconditional record."""
    issues = []; visited = 0

    def issue(code, message):
        item = {'code': code, 'message': message}
        if item not in issues:
            issues.append(item)

    def visit(node, depth=0):
        nonlocal visited
        visited += 1
        if visited > 1000:
            issue('CONDITION_BUDGET', 'Condition exceeds the supported total node budget.')
            return
        if depth > 32:
            issue('CONDITION_DEPTH', 'Condition depth exceeds the supported resource budget.')
            return
        if not isinstance(node, dict):
            issue('CONDITION_SCHEMA', 'Each condition node must be an object.')
            return
        op = node.get('op')
        if not isinstance(op, str) or op not in _COMBINE | _LEAVES:
            issue('CONDITION_OPERATOR', 'Condition operator is not supported.')
            return
        if op in {'all', 'any'}:
            if set(node) != {'op', 'args'} or not isinstance(node.get('args'), list) or not node['args']:
                issue('CONDITION_SCHEMA', 'all/any require a non-empty args list and no other members.')
                return
            if len(node['args']) > 100:
                issue('CONDITION_BUDGET', 'Condition node exceeds the supported child budget.')
                return
            for child in node['args']:
                visit(child, depth + 1)
            return
        if op == 'not':
            if set(node) != {'op', 'arg'}:
                issue('CONDITION_SCHEMA', 'not requires exactly one arg.')
                return
            visit(node['arg'], depth + 1)
            return
        field = node.get('field')
        if not isinstance(field, str) or field not in ALLOWED_FACTS:
            issue('CONDITION_FACT', 'Condition field is not in the registered fact vocabulary.')
        allowed = {'op', 'field'}
        if op == 'exists':
            if set(node) != allowed:
                issue('CONDITION_SCHEMA', 'exists requires only op and field.')
            return
        kind = node.get('value_type', _value_type(node.get('value')))
        if op == 'range':
            kind = node.get('value_type', 'number')
            allowed |= {'value_type', 'lower', 'upper', 'lower_inclusive', 'upper_inclusive'}
            if not isinstance(kind, str) or kind not in _ORDERED:
                issue('CONDITION_TYPE', 'A range requires number or date values.')
            if not all(name in node for name in ('lower', 'upper', 'lower_inclusive', 'upper_inclusive')):
                issue('CONDITION_SCHEMA', 'A range requires explicit bounds and their inclusion flags.')
                return
            if any(type(node[name]) is not bool for name in ('lower_inclusive', 'upper_inclusive')):
                issue('CONDITION_SCHEMA', 'Range inclusion flags must be Booleans.')
            try:
                lower = _typed(node['lower'], kind); upper = _typed(node['upper'], kind)
                if lower > upper or (lower == upper and not (node['lower_inclusive'] and node['upper_inclusive'])):
                    issue('CONDITION_RANGE', 'Range bounds describe an empty or reversed interval.')
            except (ValueError, InvalidOperation, TypeError):
                issue('CONDITION_VALUE', 'Range bounds do not match their declared type.')
        else:
            allowed |= {'value_type', 'value'}
            if 'value' not in node:
                issue('CONDITION_SCHEMA', 'Comparison requires a value.')
            if not isinstance(kind, str) or kind not in FACT_TYPES:
                issue('CONDITION_TYPE', 'Condition value type is not supported.')
            if op in {'gt', 'ge', 'lt', 'le'} and (not isinstance(kind, str) or kind not in _ORDERED):
                issue('CONDITION_TYPE', 'Ordered comparisons require number or date values.')
            try:
                if op == 'in':
                    if not isinstance(node.get('value'), list) or not node['value']:
                        raise ValueError('Expected non-empty choices.')
                    member_kind = node.get('value_type', _value_type(node['value'][0]))
                    if member_kind == 'set':
                        raise ValueError('Nested set choices are not supported.')
                    for value in node['value']:
                        _typed(value, member_kind)
                else:
                    _typed(node.get('value'), kind)
            except (ValueError, InvalidOperation, TypeError):
                issue('CONDITION_VALUE', 'Condition operand does not match its declared type.')
        if set(node) - allowed:
            issue('CONDITION_SCHEMA', 'Condition contains unsupported members.')

    if ast is not None:
        visit(ast)
    return issues


def condition_facts(ast):
    """Collect registered facts from a previously validated AST."""
    if ast is None:
        return []
    if ast['op'] in {'all', 'any'}:
        return sorted({field for child in ast['args'] for field in condition_facts(child)})
    if ast['op'] == 'not':
        return condition_facts(ast['arg'])
    return [ast['field']]


def evaluate_condition(ast, facts):
    """Return four states; missing or unverified facts never become false."""
    diagnostics = validate_condition(ast)
    if diagnostics:
        return {'status': 'unknown', 'required_facts': [], 'issues': diagnostics}
    required = condition_facts(ast)

    def fact_value(field):
        fact = facts.get(field) if isinstance(facts, dict) else None
        if isinstance(fact, list):
            checked = [fact_value_single(item) for item in fact]
            if any(state == 'conflicting' for state, _ in checked):
                return 'conflicting', None
            known = [value for state, value in checked if state == 'known']
            if known and any(value != known[0] for value in known[1:]):
                return 'conflicting', None
            return ('known', known[0]) if known and len(known) == len(checked) else ('unknown', None)
        return fact_value_single(fact)

    def fact_value_single(fact):
        if not isinstance(fact, dict):
            return 'unknown', None
        if fact.get('verification_state') == 'conflicting':
            return 'conflicting', None
        if (fact.get('verification_state') != 'verified' or not fact.get('source_ref') or
                not fact.get('role') or 'raw_value' not in fact or 'normalized_value' not in fact):
            return 'unknown', None
        kind = fact.get('type')
        try:
            return 'known', (kind, _typed(fact['normalized_value'], kind))
        except (ValueError, InvalidOperation, TypeError):
            return 'unknown', None

    def evaluate(node):
        if node is None:
            return 'satisfied'
        op = node['op']
        if op in {'all', 'any'}:
            states = [evaluate(child) for child in node['args']]
            if 'conflicting' in states:
                return 'conflicting'
            if op == 'all':
                if 'not_satisfied' in states:
                    return 'not_satisfied'
                return 'unknown' if 'unknown' in states else 'satisfied'
            if 'satisfied' in states:
                return 'satisfied'
            return 'unknown' if 'unknown' in states else 'not_satisfied'
        if op == 'not':
            return {'satisfied': 'not_satisfied', 'not_satisfied': 'satisfied',
                    'unknown': 'unknown', 'conflicting': 'conflicting'}[evaluate(node['arg'])]
        state, fact = fact_value(node['field'])
        if state != 'known':
            return state
        fact_kind, actual = fact
        if op == 'exists':
            return 'satisfied'
        kind = node.get('value_type', 'number' if op == 'range' else
                        _value_type(node['value'][0]) if op == 'in' else _value_type(node['value']))
        if kind != fact_kind:
            return 'unknown'
        if op == 'range':
            lower = _typed(node['lower'], kind); upper = _typed(node['upper'], kind)
            result = ((actual >= lower if node['lower_inclusive'] else actual > lower) and
                      (actual <= upper if node['upper_inclusive'] else actual < upper))
        elif op == 'in':
            result = actual in [_typed(value, kind) for value in node['value']]
        else:
            value = _typed(node['value'], kind)
            if op == 'eq': result = actual == value
            elif op == 'gt': result = actual > value
            elif op == 'ge': result = actual >= value
            elif op == 'lt': result = actual < value
            else: result = actual <= value
        return 'satisfied' if result else 'not_satisfied'

    return {'status': evaluate(ast), 'required_facts': required, 'issues': []}
