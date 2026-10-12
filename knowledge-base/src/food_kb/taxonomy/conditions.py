"""Bounded taxonomy predicates; classification declarations are only hints."""
from decimal import Decimal, InvalidOperation

from food_kb.knowledge.conditions import evaluate_condition as evaluate_knowledge_condition
from food_kb.knowledge.conditions import validate_condition as validate_knowledge_condition
from food_kb.knowledge.templates import ALLOWED_FACTS

FACTS = ALLOWED_FACTS | {'raw_material', 'ingredients', 'ingredient_proportion', 'packaging',
                        'primary_heat_treatment', 'milk_ingredient_proportion_percent'}
FACT_ROLES = {'sample_fact', 'document_fact', 'verified_observation'}
HINT_FACTS = {'category_id', 'category_namespace'}
PRIMARY_HEAT_TREATMENTS = {'巴氏杀菌', '灭菌', '未热处理'}
_STATES = {'satisfied': 'applicable', 'not_satisfied': 'not_applicable',
           'unknown': 'unknown', 'conflicting': 'conflicting'}


def _validation_ast(ast, depth=0, budget=None, ancestors=frozenset()):
    """Reuse the existing type/operator validator for registered extra fields."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 32 or budget[0] > 1000 or id(ast) in ancestors:
        raise ValueError('Condition exceeds the bounded tree contract.')
    if not isinstance(ast, dict):
        return ast
    value = dict(ast)
    ancestors = ancestors | {id(ast)}
    op = value.get('op')
    if isinstance(op, str) and op in {'all', 'any'} and isinstance(value.get('args'), list):
        if len(value['args']) > 100:
            raise ValueError('Condition exceeds the child budget.')
        value['args'] = [_validation_ast(child, depth + 1, budget, ancestors) for child in value['args']]
    elif op == 'not':
        value['arg'] = _validation_ast(value.get('arg'), depth + 1, budget, ancestors)
    elif isinstance(value.get('field'), str) and value.get('field') in FACTS - ALLOWED_FACTS:
        value['field'] = 'product_identity'
    return value


def validate_condition(ast):
    try:
        return validate_knowledge_condition(_validation_ast(ast))
    except ValueError as error:
        return [{'code': 'CONDITION_BUDGET', 'message': str(error)}]


def _fields(ast):
    if not isinstance(ast, dict):
        return []
    if ast.get('op') in {'all', 'any'}:
        return sorted({field for child in ast.get('args', []) for field in _fields(child)})
    if ast.get('op') == 'not':
        return _fields(ast.get('arg'))
    return [ast['field']] if ast.get('field') in FACTS else []


def checked_fact(field, facts):
    """Keep source conflicts visible, without trusting a caller's category label."""
    if field in HINT_FACTS or not isinstance(facts, dict):
        return None
    value = facts.get(field)
    if isinstance(value, list):
        return [_checked_single(item, field) for item in value]
    return _checked_single(value, field)


def _checked_single(fact, field):
    if not isinstance(fact, dict):
        return None
    if fact.get('verification_state') == 'conflicting':
        return fact
    if fact.get('role') not in FACT_ROLES:
        return None
    if field == 'primary_heat_treatment' and (fact.get('type') != 'string' or
            not isinstance(fact.get('normalized_value'), str) or
            fact['normalized_value'] not in PRIMARY_HEAT_TREATMENTS):
        return None
    if field == 'milk_ingredient_proportion_percent':
        if fact.get('type') != 'number' or type(fact.get('normalized_value')) is bool:
            return None
        try:
            number = Decimal(str(fact.get('normalized_value')))
            if not number.is_finite() or not 0 <= number <= 100:
                return None
        except (InvalidOperation, TypeError, ValueError):
            return None
    return fact


def combine_states(states, operator='all'):
    """Conflicting evidence cannot be hidden by a convenient second branch."""
    if 'conflicting' in states:
        return 'conflicting'
    if operator == 'all':
        if 'not_applicable' in states:
            return 'not_applicable'
        return 'unknown' if 'unknown' in states else 'applicable'
    if 'applicable' in states:
        return 'applicable'
    return 'unknown' if 'unknown' in states else 'not_applicable'


def evaluate_condition(condition, facts):
    """Return applicability and required/missing facts, preserving prose gaps.

    Source conditions use ``state=structured`` with an AST, or an explicit
    ``unconditional`` state. Missing/unresolved prose is never unconditional.
    A bare AST is accepted for the read-only predicate API, not source records.
    """
    wrapped = isinstance(condition, dict) and 'op' not in condition
    ast = condition.get('ast') if wrapped else condition
    diagnostics = validate_condition(ast)
    declared = condition.get('required_facts', []) if wrapped else []
    if not isinstance(declared, list) or any(not isinstance(field, str) or field not in FACTS for field in declared):
        diagnostics.append({'code': 'CONDITION_FACT', 'message': 'required_facts 必须是注册事实字段列表。'})
    if diagnostics:
        return {'status': 'unknown', 'required_facts': [], 'missing_facts': [], 'issues': diagnostics,
                'raw': condition.get('raw', '') if wrapped else '', 'evidence': []}
    required = sorted(set(_fields(ast)) | set(declared))
    missing = []
    conflicting = []
    for field in required:
        probe = {'op': 'exists', 'field': 'product_identity'}
        state = evaluate_knowledge_condition(probe, {'product_identity': checked_fact(field, facts)})['status']
        if state == 'unknown':
            missing.append(field)
        elif state == 'conflicting':
            conflicting.append(field)
    result = {'status': 'unknown', 'required_facts': required, 'missing_facts': missing,
              'issues': diagnostics, 'raw': condition.get('raw', '') if wrapped else '',
              'evidence': condition.get('evidence', []) if wrapped else []}
    evidenced = (wrapped and isinstance(condition.get('evidence'), list) and condition['evidence'] and
                 all(isinstance(item, dict) and item for item in condition['evidence']))
    partial = wrapped and condition.get('state') == 'partial' and evidenced and ast is not None
    if wrapped and not partial and (condition.get('state') not in {'structured', 'resolved', 'unconditional'} or
                    condition.get('unresolved') or not evidenced):
        result['issues'] = [{'code': 'CONDITION_UNRESOLVED', 'message': '原文条件尚未完成有来源的结构化。'}]
        if conflicting:
            result['status'] = 'conflicting'
        return result
    if ast is None:
        if wrapped and condition.get('state') == 'unconditional' and not required:
            result['status'] = 'applicable'
        else:
            result['issues'] = [{'code': 'CONDITION_UNRESOLVED', 'message': '未声明无条件，不能省略适用判断。'}]
        return result

    def evaluate(node):
        op = node['op']
        if op in {'all', 'any'}:
            return combine_states([evaluate(child) for child in node['args']], op)
        if op == 'not':
            return {'applicable': 'not_applicable', 'not_applicable': 'applicable',
                    'unknown': 'unknown', 'conflicting': 'conflicting'}[evaluate(node['arg'])]
        leaf = dict(node); leaf['field'] = 'product_identity'
        value = checked_fact(node['field'], facts)
        return _STATES[evaluate_knowledge_condition(leaf, {'product_identity': value})['status']]

    result['status'] = evaluate(ast)
    additional_required = set(declared) - set(_fields(ast))
    if additional_required.intersection(conflicting):
        result['status'] = 'conflicting'
    elif result['status'] == 'applicable' and additional_required.intersection(missing):
        result['status'] = 'unknown'
        result['issues'] = [{'code': 'REQUIRED_FACT_MISSING', 'message': '声明的额外必要事实尚未核实。'}]
    if partial and result['status'] == 'applicable':
        result['status'] = 'unknown'
        result['issues'] = [{'code': 'NECESSARY_CONDITION_ONLY', 'message': '明确必要条件满足，剩余定义仍须核实。'}]
    return result
