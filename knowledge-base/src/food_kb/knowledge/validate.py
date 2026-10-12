"""Machine checks for evidence-bound candidates, separate from human review."""
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from .conditions import condition_facts, validate_condition
from .templates import RECORD_FIELDS, REQUIREMENT_TYPES, SCHEMA_VERSION, SCOPE_CATALOG_FIELDS, STANDARD_TEMPLATES

_NUMERIC_REQUIREMENTS = {'upper_limit', 'lower_limit', 'range'}
_UNITS = {'毫克/千克': 'mg/kg', '毫克每千克': 'mg/kg', '克/千克': 'g/kg', '克每千克': 'g/kg', '百分比': '%'}
_LIMIT_OPS = {'upper_limit': {'<', '<=', '≤'}, 'lower_limit': {'>', '>=', '≥'}}
_METHOD_REFERENCE_FIELDS = {'principle', 'reagents', 'equipment', 'steps', 'formulas',
                            'submethods', 'general_requirements', 'precision', 'sections'}
_METHOD_CHECK_ROLES = {'sample_preparation', 'blank_test', 'measurement_conditions',
                       'parallel_result_aggregation', 'required_reference'}


def _compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


def _empty(value):
    return value is None or value == '' or value == [] or value == {}


def _conflicting_signatures(first, second):
    if first == second:
        return False
    left = json.loads(first); right = json.loads(second)
    if left[3] == right[3] and {left[0], right[0]} == {'upper_limit', 'lower_limit'}:
        lower, upper = (left, right) if left[0] == 'lower_limit' else (right, left)
        try:
            lo = Decimal(lower[2]); hi = Decimal(upper[2])
            return lo > hi or (lo == hi and (lower[1] == '>' or upper[1] == '<'))
        except (InvalidOperation, TypeError, ValueError):
            return True
    return True


def validate_candidate(candidate, bundle):
    """Reject corrupted provenance; retain incomplete but honest candidates."""
    issues = []; blockers = []

    def add(collection, code, message, field=None):
        item = {'code': code, 'message': message}
        if field is not None:
            item['field'] = field
        if item not in collection:
            collection.append(item)

    def issue(code, message, field=None):
        add(issues, code, message, field)

    def block(code, message, field=None):
        add(blockers, code, message, field)

    if not isinstance(candidate, dict):
        return {'passed': False, 'issues': [{'code': 'CANDIDATE_SCHEMA', 'message': 'Candidate must be an object.'}],
                'publication_blockers': []}
    if candidate.get('schema_version') != SCHEMA_VERSION:
        issue('CANDIDATE_SCHEMA_VERSION', 'Knowledge schema version is unsupported.')
    if not isinstance(candidate.get('logical_key'), str) or not candidate['logical_key'].strip():
        issue('CANDIDATE_KEY', 'A stable logical key is required.')
    if not isinstance(candidate.get('record_type'), str) or candidate.get('record_type') not in RECORD_FIELDS:
        issue('CANDIDATE_TYPE', 'Knowledge record type is unsupported.')
    if not isinstance(candidate.get('standard_type'), str) or candidate.get('standard_type') not in STANDARD_TEMPLATES:
        issue('STANDARD_TYPE', 'Standard template type is unsupported.')
    if not isinstance(candidate.get('title'), str) or not candidate['title'].strip():
        issue('CANDIDATE_TITLE', 'A candidate title is required.')
    if candidate.get('executable') is not False:
        issue('CANDIDATE_EXECUTABLE', 'Extraction cannot grant execution approval.')
    for name in ('review_state', 'approved_by', 'reviewer', 'actor'):
        if name in candidate:
            issue('CANDIDATE_REVIEW_CLAIM', 'Submission cannot appoint a reviewer or grant approval.', name)
    blocks = {item['block_id']: item for item in bundle.get('blocks', [])}
    chunks = {item['chunk_id']: item for item in bundle.get('chunks', [])}
    sources = candidate.get('source_chunk_ids')
    if not isinstance(sources, list) or not sources or any(not isinstance(item, str) or item not in chunks for item in sources):
        issue('SOURCE_CHUNK_MISSING', 'Candidate must reference existing source chunks.')

    def evidence(items, field):
        if not isinstance(items, list) or not items:
            issue('FIELD_EVIDENCE_MISSING', 'Every supplied fact needs independently bound evidence.', field)
            return
        for item in items:
            if not isinstance(item, dict):
                issue('FIELD_EVIDENCE_SCHEMA', 'Evidence must be an object.', field)
                continue
            block_id = item.get('block_id')
            source_block = blocks.get(block_id) if isinstance(block_id, str) else None
            if source_block is None:
                issue('FIELD_BLOCK_MISSING', 'Evidence source block does not exist in this parse.', field)
                continue
            quote = item.get('quote')
            if not isinstance(quote, str) or not quote.strip() or quote not in source_block.get('text_raw', ''):
                issue('FIELD_QUOTE_MISMATCH', 'Evidence quote is absent from the original source block.', field)
            actual = source_block.get('source', {})
            claimed = item.get('source')
            if not isinstance(claimed, dict):
                issue('FIELD_SOURCE_MISSING', 'Evidence must retain the original source location.', field)
                continue
            for name in ('asset_id', 'markdown_asset_id', 'parse_id', 'line_start', 'line_end', 'pdf_pages'):
                if actual.get(name) is not None and claimed.get(name) != actual.get(name):
                    issue('FIELD_SOURCE_MISMATCH', 'Evidence page, line or source identity was changed.', field)
            for name in ('line_ranges', 'bbox', 'printed_pages', 'locator_quality'):
                if name in claimed and claimed[name] != actual.get(name):
                    issue('FIELD_SOURCE_MISMATCH', 'Evidence location differs from its original source block.', field)
            if actual.get('parse_id') is not None and actual['parse_id'] != bundle.get('parse_id'):
                issue('FIELD_CROSS_PARSE', 'Evidence belongs to a different parse.', field)
            for name in ('asset_id', 'markdown_asset_id'):
                if actual.get(name) is not None and actual[name] != bundle.get('source', {}).get(name):
                    issue('FIELD_CROSS_SOURCE', 'Evidence block belongs to another source asset.', field)
            if 'cell' in item:
                claimed_cell = item['cell']
                matching = []
                if isinstance(claimed_cell, dict):
                    for chunk in chunks.values():
                        table = chunk.get('table') or {}
                        for cell in table.get('cells', []):
                            if (isinstance(cell, dict) and cell.get('column') == claimed_cell.get('column') and
                                    cell.get('source_cell') == claimed_cell.get('source_cell') and
                                    isinstance(quote, str) and quote in str(cell.get('value', ''))):
                                matching.append((chunk, cell))
                if (not matching or not any(block_id in chunk.get('block_ids', []) for chunk, _ in matching) or
                        type(claimed_cell.get('inherited')) is not bool or
                        not any(cell.get('inherited') is claimed_cell['inherited'] for _, cell in matching)):
                    issue('FIELD_CELL_MISMATCH', 'Claimed table cell or merged-cell provenance is absent from the source rows.', field)

    fields = candidate.get('fields')
    if not isinstance(fields, dict):
        issue('FIELDS_SCHEMA', 'Knowledge fields must be an object.')
        fields = {}
    required = RECORD_FIELDS.get(candidate.get('record_type'), ()) if isinstance(candidate.get('record_type'), str) else ()
    for name in required:
        if name not in fields or not isinstance(fields[name], dict) or _empty(fields[name].get('value')):
            issue('FIELD_REQUIRED', 'A required typed knowledge field is missing.', name)
    for name, item in fields.items():
        if not isinstance(item, dict) or not all(key in item for key in ('raw', 'value', 'evidence', 'normalization_reason')):
            issue('FIELD_SCHEMA', 'Field must retain raw, value, evidence and normalization reason.', name)
            continue
        raw = item['raw']; value = item['value']; reason = item['normalization_reason']
        if not isinstance(raw, str):
            issue('FIELD_RAW_TYPE', 'Original field text must be a string.', name)
            continue
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            issue('NORMALIZATION_REASON', 'Normalization reason must be meaningful text or null.', name)
        state = item.get('state')
        if state is not None and (not isinstance(state, str) or state not in {'known', 'unknown', 'not_specified'}):
            issue('FIELD_STATE', 'Field state is unsupported.', name)
        if state == 'not_specified':
            if name in required or raw or value is not None or not reason:
                issue('FIELD_NOT_SPECIFIED', 'Only optional fields can record source-supported absence, with a reason and null value.', name)
            evidence(item['evidence'], name)
            continue
        if _empty(value) and not raw and not item['evidence']:
            requirement_field = fields.get('requirement_type')
            qualitative = isinstance(requirement_field, dict) and requirement_field.get('value') == 'qualitative'
            if not (qualitative and name in {'unit', 'comparator'}):
                block('FIELD_UNRESOLVED', 'A field is explicitly unknown and needs source verification.', name)
            continue
        evidence(item['evidence'], name)
        if isinstance(item['evidence'], list) and raw:
            quotes = '\n'.join(e.get('quote', '') for e in item['evidence'] if isinstance(e, dict) and isinstance(e.get('quote'), str))
            # A raw value may be copied from several source fragments, but it
            # cannot introduce a value absent from every cited fragment.
            parts = [part.strip() for part in raw.splitlines() if part.strip()]
            if any(part not in quotes and _compact(part) not in _compact(quotes) for part in parts):
                issue('FIELD_RAW_UNSUPPORTED', 'Original field value is not present in its own evidence.', name)
        if isinstance(value, str) and raw and name != 'requirement_type':
            if _compact(value) != _compact(raw) and _compact(value) not in _compact(raw) and not reason:
                issue('NORMALIZATION_REASON', 'A changed field value needs a derivation reason.', name)
            if name in {'subject', 'project', 'basis', 'expressed_as', 'comparator'} and _compact(value) not in _compact(raw):
                issue('FIELD_VALUE_UNSUPPORTED', 'A critical normalized field is absent from its own source text.', name)
            requirement_type = fields.get('requirement_type')
            if (name == 'value' and isinstance(requirement_type, dict) and requirement_type.get('value') == 'qualitative' and
                    _compact(value) not in _compact(raw)):
                issue('FIELD_VALUE_UNSUPPORTED', 'A qualitative requirement value must remain supported by its original wording.', name)
        if name == 'requirement_type' and isinstance(value, str):
            symbols = {'<': 'upper_limit', '<=': 'upper_limit', '≤': 'upper_limit',
                       '>': 'lower_limit', '>=': 'lower_limit', '≥': 'lower_limit'}
            if _compact(raw) in symbols and symbols[_compact(raw)] != value:
                issue('REQUIREMENT_DIRECTION_CHANGED', 'The requirement type disagrees with its original comparison symbol.', name)
        if name == 'unit' and not _empty(value):
            original = _compact(raw); normalized = _compact(value)
            if original != normalized:
                if _UNITS.get(original) != normalized or not reason:
                    issue('UNIT_CHANGED', 'A unit cannot be changed without a supported normalization.', name)
        if name == 'value' and isinstance(value, str) and re.fullmatch(r'[-+]?\d+(?:\.\d+)?', _compact(raw)):
            try:
                if Decimal(_compact(raw)) != Decimal(value):
                    issue('VALUE_CHANGED', 'Normalized numeric value differs from the quoted decimal.', name)
                elif value != raw and not reason:
                    issue('NORMALIZATION_REASON', 'Numeric formatting normalization needs an explicit reason.', name)
            except InvalidOperation:
                issue('VALUE_TYPE', 'A quoted number needs a finite decimal string.', name)

    def field_value(name):
        field = fields.get(name)
        return field.get('value') if isinstance(field, dict) else None

    if candidate.get('record_type') == 'method':
        lean = ('semantic_coverage' in candidate or 'method_checks' in fields or
                'calculation_dependencies' in fields or any(
                    isinstance(item, dict) and 'block_id' in item
                    for name in _METHOD_REFERENCE_FIELDS for item in
                    (field_value(name) if isinstance(field_value(name), list) else [])))
        if lean:
            coverage = candidate.get('semantic_coverage')
            if (not isinstance(coverage, dict) or coverage.get('state') != 'partial' or
                    any(not isinstance(coverage.get(name), list) or
                        any(not isinstance(value, str) or not value for value in coverage[name])
                        for name in ('structured_roles', 'source_only', 'unsupported_checks')) or
                    not isinstance(coverage.get('note'), str) or not coverage['note'].strip()):
                issue('METHOD_COVERAGE_SCHEMA', 'Lean methods must explicitly retain partial semantic coverage.')
            elif (not {'experimental_conditions', 'formula_variables', 'lod_loq_conditions'} <= set(coverage['source_only']) or
                  not {'full_method_applicability', 'formula_recalculation'} <= set(coverage['unsupported_checks'])):
                issue('METHOD_COVERAGE_OVERCLAIM', 'Method references do not establish complete applicability or executable calculations.')
            block('METHOD_SEMANTICS_PARTIAL', 'Method facts cover selected roles only; full applicability and recalculation remain unsupported.')
            cited_blocks = {block_id for chunk_id in sources if isinstance(chunk_id, str) and chunk_id in chunks
                            for block_id in chunks[chunk_id].get('block_ids', [])} if isinstance(sources, list) else set()
            method_anchor = field_value('method_anchor')
            method_root = next((b for b in blocks.values() if b.get('key') == method_anchor), None)
            name_field = fields.get('method_name'); anchor_field = fields.get('method_anchor')
            name_evidence = name_field.get('evidence', []) if isinstance(name_field, dict) else []
            anchor_evidence = anchor_field.get('evidence', []) if isinstance(anchor_field, dict) else []
            if (not isinstance(method_anchor, str) or method_root is None or
                    method_root['block_id'] not in cited_blocks or
                    any(not isinstance(items, list) or not any(isinstance(e, dict) and
                        e.get('block_id') == method_root['block_id'] and e.get('quote') == method_root.get('title')
                        for e in items) for items in (name_evidence, anchor_evidence))):
                issue('METHOD_ANCHOR_UNBOUND', 'Method anchor and method name must cite the same original method title.', 'method_anchor')

            def bound_source(item, name, key='block_id', quote=None):
                block_id = item.get(key) if isinstance(item, dict) else None
                original = blocks.get(block_id) if isinstance(block_id, str) else None
                if original is None or block_id not in cited_blocks:
                    issue('METHOD_BLOCK_UNBOUND', 'Method fact must reference a block in its own cited chunks.', name)
                    return None
                bound = fields[name].get('evidence', [])
                if not isinstance(bound, list) or not any(
                        isinstance(e, dict) and e.get('block_id') == block_id and
                        isinstance(e.get('quote'), str) and
                        (quote is None or _compact(quote) in _compact(e['quote'])) for e in bound):
                    issue('METHOD_ITEM_EVIDENCE', 'Each method item needs its own source block and wording in field evidence.', name)
                return original

            for name in _METHOD_REFERENCE_FIELDS | {'method_checks'}:
                value = field_value(name)
                if value is None:
                    continue
                if not isinstance(value, list):
                    issue('METHOD_REFERENCE_SCHEMA', 'Method source references must be arrays.', name)
                    continue
                titles = []
                for item in value:
                    required_keys = {'block_id', 'anchor', 'title'} | ({'role', 'label'} if name == 'method_checks' else set())
                    if not isinstance(item, dict) or set(item) != required_keys:
                        issue('METHOD_REFERENCE_SCHEMA', 'Method references retain only block identity, anchor and title, with check roles when applicable.', name)
                        continue
                    original = bound_source(item, name, quote=item.get('title'))
                    if original and (item['anchor'] != original.get('key') or item['title'] != original.get('title')):
                        issue('METHOD_REFERENCE_CHANGED', 'Method reference anchor or title differs from the original block.', name)
                    if original and name != 'general_requirements' and (not isinstance(method_anchor, str) or
                            not (original.get('key') == method_anchor or original.get('key', '').startswith(method_anchor + '.'))):
                        issue('METHOD_REFERENCE_OUTSIDE', 'Method source reference belongs to another method.', name)
                    if original and name == 'general_requirements' and not re.search(r'一般规定|安全提示', original.get('title', '')):
                        issue('METHOD_REFERENCE_OUTSIDE', 'A shared method reference must identify the original general or safety section.', name)
                    if name == 'method_checks' and (not isinstance(item['role'], str) or item['role'] not in _METHOD_CHECK_ROLES or
                            not isinstance(item['label'], str) or not item['label'].strip()):
                        issue('METHOD_CHECK_ROLE', 'Method check roles must be explicit supported query roles.', name)
                    if isinstance(item.get('title'), str):
                        titles.append(item['title'])
                if name in _METHOD_REFERENCE_FIELDS and _compact(fields[name].get('raw', '')) != _compact('\n'.join(titles)):
                    issue('METHOD_REFERENCE_RAW', 'Method reference raw text retains titles rather than copied source paragraphs.', name)

            parameters = field_value('parameters')
            if parameters is not None and not isinstance(parameters, list):
                issue('METHOD_PARAMETER_SCHEMA', 'Structured method parameters must be an array.', 'parameters')
            for item in parameters if isinstance(parameters, list) else []:
                if (not isinstance(item, dict) or set(item) != {'role', 'label', 'value', 'unit', 'comparator', 'raw', 'source_block_id'} or
                        item.get('role') != 'parallel_absolute_difference' or not isinstance(item.get('label'), str) or not item['label'].strip() or
                        not isinstance(item.get('raw'), str) or not isinstance(item.get('value'), str)):
                    issue('METHOD_PARAMETER_SCHEMA', 'Only explicitly worded parallel absolute difference bounds are structured in this version.', 'parameters')
                    continue
                original = bound_source(item, 'parameters', 'source_block_id', item['raw'])
                match = re.search(r'绝对差值不大于([-+]?\d+(?:\.\d+)?)%', _compact(item['raw']))
                if (original is None or _compact(item['raw']) not in _compact(original['text_raw']) or
                        not re.search(r'平行测定|重复性条件', original['text_raw']) or match is None or
                        item['unit'] != '%' or item['comparator'] != '≤'):
                    issue('METHOD_PARAMETER_UNSUPPORTED', 'Parameter role, unit and direction must match their own original precision wording.', 'parameters')
                else:
                    try:
                        if not Decimal(item['value']).is_finite() or Decimal(item['value']) != Decimal(match[1]):
                            raise InvalidOperation
                    except InvalidOperation:
                        issue('METHOD_PARAMETER_CHANGED', 'Structured precision value differs from its original decimal.', 'parameters')

            dependencies = field_value('calculation_dependencies')
            if dependencies is not None and not isinstance(dependencies, list):
                issue('METHOD_DEPENDENCY_SCHEMA', 'Calculation dependencies must be an array.', 'calculation_dependencies')
            for item in dependencies if isinstance(dependencies, list) else []:
                if (not isinstance(item, dict) or set(item) != {'source_method', 'target_method', 'symbol', 'formula_ids', 'raw', 'source_block_id'} or
                        any(not isinstance(item.get(key), str) or not item[key] for key in ('source_method', 'target_method', 'symbol', 'raw')) or
                        not isinstance(item.get('formula_ids'), list) or not item['formula_ids'] or
                        any(not isinstance(value, str) or not value for value in item['formula_ids'])):
                    issue('METHOD_DEPENDENCY_SCHEMA', 'Calculation dependencies need explicit source and target methods, variable, formulas and source wording.', 'calculation_dependencies')
                    continue
                original = bound_source(item, 'calculation_dependencies', 'source_block_id', item['raw'])
                known_keys = {b.get('key') for b in blocks.values()}
                if (original is None or not isinstance(method_anchor, str) or
                        not (item['source_method'] == method_anchor or item['source_method'].startswith(method_anchor + '.')) or
                        item['source_method'] not in known_keys or item['target_method'] not in known_keys or
                        not original.get('key', '').startswith(item['source_method'] + '.') or
                        _compact(item['raw']) not in _compact(original['text_raw']) or
                        not re.search(r'\$' + re.escape(item['symbol']) + r'\$[—–-]+由\s*' + re.escape(item['target_method']) + r'\s*所测得', _compact(item['raw'])) or
                        any(not re.search(r'\\tag\{\s*' + re.escape(formula_id) + r'\s*\}', original['text_raw']) for formula_id in item['formula_ids'])):
                    issue('METHOD_DEPENDENCY_UNSUPPORTED', 'Calculation dependency must match an original variable definition and formula in the cited method.', 'calculation_dependencies')

    if candidate.get('record_type') == 'requirement':
        kind = field_value('requirement_type')
        if not isinstance(kind, str) or kind not in REQUIREMENT_TYPES:
            issue('REQUIREMENT_TYPE', 'Requirement type is unsupported.', 'requirement_type')
        if kind == 'unknown':
            block('REQUIREMENT_TYPE_UNKNOWN', 'Unknown requirement semantics cannot be published.')
        if isinstance(kind, str) and kind in _NUMERIC_REQUIREMENTS:
            if _empty(field_value('unit')):
                block('UNIT_MISSING', 'A numeric requirement has no evidenced unit.', 'unit')
            if kind in _LIMIT_OPS:
                if not isinstance(field_value('comparator'), str) or field_value('comparator') not in _LIMIT_OPS[kind]:
                    issue('COMPARATOR_INVALID', 'Comparison symbol is absent or disagrees with the requirement type.', 'comparator')
                value = field_value('value')
                try:
                    if not isinstance(value, str) or not Decimal(value).is_finite():
                        raise InvalidOperation
                except InvalidOperation:
                    issue('VALUE_TYPE', 'Numeric requirements require exact finite decimal strings.', 'value')
            elif kind == 'range':
                value = field_value('value')
                if not isinstance(value, dict) or not {'lower', 'upper', 'lower_inclusive', 'upper_inclusive'} <= set(value):
                    issue('RANGE_VALUE', 'A range must retain explicit decimal bounds and inclusion flags.', 'value')
                else:
                    try:
                        lo = Decimal(value['lower']); hi = Decimal(value['upper'])
                        if not all(isinstance(value[n], str) for n in ('lower', 'upper')) or not lo.is_finite() or not hi.is_finite() or lo > hi:
                            raise InvalidOperation
                        if any(type(value[n]) is not bool for n in ('lower_inclusive', 'upper_inclusive')):
                            raise InvalidOperation
                    except (InvalidOperation, TypeError, ValueError):
                        issue('RANGE_VALUE', 'Range bounds or inclusion flags are invalid.', 'value')
        project_field = fields.get('project')
        project_raw = project_field.get('raw', '') if isinstance(project_field, dict) else ''
        if isinstance(project_raw, str):
            expressions = re.findall(r'以\s*([^，,）)\n]+?)\s*计', project_raw)
            component_basis = any(_compact(expression) not in {'干基', '湿基'} for expression in expressions)
            if component_basis and _empty(field_value('expressed_as')):
                block('EXPRESSED_AS_MISSING', 'The project expresses a component basis that must be retained.', 'expressed_as')
            if re.search(r'(?:干基|湿基)', project_raw) and _empty(field_value('basis')):
                block('MEASUREMENT_BASIS_MISSING', 'Dry or wet measurement basis must be retained.', 'basis')
        if isinstance(kind, str) and kind in {'formula', 'sampling_plan', 'use_restriction'}:
            block('EXECUTOR_NOT_BOUND', 'This requirement type still needs a supported rule binding.')

    condition = candidate.get('condition')
    if not isinstance(condition, dict) or not {'ast', 'raw', 'unresolved', 'required_facts'} <= set(condition):
        issue('CONDITION_SCHEMA', 'Condition must retain AST, source text, unresolved clauses and required facts.')
    else:
        ast_issues = validate_condition(condition['ast'])
        for item in ast_issues:
            issue(item['code'], item['message'], 'condition')
        if (not isinstance(condition['raw'], str) or not isinstance(condition['unresolved'], list) or
                not isinstance(condition['required_facts'], list) or
                any(not isinstance(field, str) for field in condition['required_facts'])):
            issue('CONDITION_SCHEMA', 'Condition metadata types are invalid.', 'condition')
        else:
            if not ast_issues and sorted(set(condition['required_facts'])) != condition_facts(condition['ast']):
                issue('CONDITION_FACTS_MISMATCH', 'Required facts must match the validated condition AST.', 'condition')
            cited_text = '\n'.join(blocks[block_id].get('text_raw', '')
                                   for chunk_id in sources if isinstance(sources, list) and isinstance(chunk_id, str) and chunk_id in chunks
                                   for block_id in chunks[chunk_id].get('block_ids', []) if block_id in blocks) if isinstance(sources, list) else ''
            original_clauses = [text.strip() for text in re.split(r'[；;\n]', condition['raw']) if text.strip()]
            if any(_compact(text) not in _compact(cited_text) for text in original_clauses):
                issue('CONDITION_RAW_UNSUPPORTED', 'Condition source text is absent from the cited original chunks.', 'condition')
            if condition['unresolved']:
                block('CONDITION_UNRESOLVED', 'Unresolved original conditions prevent publication.', 'condition')
            for item in condition['unresolved']:
                if not isinstance(item, dict) or not item.get('raw') or not item.get('reason'):
                    issue('UNRESOLVED_CONDITION_SCHEMA', 'Unresolved conditions need source text and a reason.', 'condition')
                else:
                    evidence(item.get('evidence'), 'condition')
            if condition['ast'] is None and condition['raw'].strip() and not condition['unresolved']:
                block('CONDITION_UNINTERPRETED', 'Non-empty condition text needs an AST or explicit unresolved record.', 'condition')

    if candidate.get('record_type') == 'standard_scope_catalog':
        for name in SCOPE_CATALOG_FIELDS:
            if _empty(field_value(name)):
                block('SCOPE_CATALOG_GAP', 'Scope catalog coverage, namespace, regime or date context remains unknown.', name)
        date_conditions = field_value('date_conditions')
        if isinstance(date_conditions, dict) and not date_conditions.get('date_role'):
            block('DATE_ROLE_MISSING', 'A date needs an explicit business role rather than an assumed current date.', 'date_conditions')
        if not isinstance(field_value('coverage_state'), str) or field_value('coverage_state') not in {'partial', 'complete', 'unknown'}:
            block('SCOPE_COVERAGE_UNKNOWN', 'Catalog coverage has not been assessed.', 'coverage_state')
        if field_value('coverage_state') != 'complete':
            block('SCOPE_COVERAGE_PARTIAL', 'Catalog does not establish a complete applicable standard set.', 'coverage_state')

    for name, target_name in (('references', 'target'), ('relations', 'target_key')):
        items = candidate.get(name)
        if not isinstance(items, list):
            issue('RELATIONS_SCHEMA', 'References and relations must be arrays.', name)
            continue
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get(target_name), str) or not item[target_name] or
                    not isinstance(item.get('kind'), str) or not item['kind']):
                issue('RELATION_TARGET_MISSING', 'Relation target and relation kind must be explicit.', name)
                continue
            evidence(item.get('evidence'), name)
            if name == 'references':
                if not isinstance(item.get('version_policy'), str) or not item['version_policy']:
                    issue('REFERENCE_VERSION_MISSING', 'Reference version policy must be explicit.', name)
                if not isinstance(item.get('state'), str) or item.get('state') not in {'resolved', 'verified'}:
                    block('REFERENCE_UNRESOLVED', 'Referenced source, version or applicability remains unverified.', name)
    return {'passed': not issues, 'issues': issues, 'publication_blockers': blockers}


def validate_candidates(candidates, bundle):
    """Keep conflicting records visible instead of selecting the last import."""
    results = []; issues = []; grouped = {}; keys = set()
    for index, candidate in enumerate(candidates):
        result = validate_candidate(candidate, bundle)
        results.append({'logical_key': candidate.get('logical_key') if isinstance(candidate, dict) else None, **result})
        if not isinstance(candidate, dict):
            continue
        key = candidate.get('logical_key')
        if not isinstance(key, str):
            continue
        if key in keys:
            issues.append({'code': 'DUPLICATE_LOGICAL_KEY', 'message': 'Logical keys must be unique within an extraction run.', 'logical_key': key})
        keys.add(key)
        fields = candidate.get('fields', {})
        condition = candidate.get('condition')
        if candidate.get('record_type') == 'requirement' and isinstance(fields, dict) and isinstance(condition, dict):
            value = lambda name: fields.get(name, {}).get('value') if isinstance(fields.get(name), dict) else None
            group = json.dumps([value('subject'), value('project'), value('basis'), value('expressed_as'),
                                condition.get('ast')], ensure_ascii=False, sort_keys=True)
            signature = json.dumps([value('requirement_type'), value('comparator'), value('value'), value('unit')],
                                   ensure_ascii=False, sort_keys=True)
            previous = grouped.setdefault(group, [])
            for old_signature, old_index in previous:
                if _conflicting_signatures(old_signature, signature):
                    conflict = {'code': 'FIELD_CONFLICT', 'message': 'Same project and condition have incompatible candidate requirements.',
                                'logical_keys': [candidates[old_index]['logical_key'], key]}
                    issues.append(conflict)
                    for position in (old_index, index):
                        results[position]['publication_blockers'].append(conflict)
            previous.append((signature, index))
    for result, candidate in zip(results, candidates):
        if not isinstance(candidate, dict):
            continue
        for relation in candidate.get('relations', []) if isinstance(candidate.get('relations'), list) else []:
            if isinstance(relation, dict) and (not isinstance(relation.get('target_key'), str) or relation.get('target_key') not in keys):
                item = {'code': 'RELATION_TARGET_UNKNOWN', 'message': 'Related knowledge candidate is absent from this extraction run.',
                        'field': 'relations'}
                result['publication_blockers'].append(item)
    return {'passed': all(result['passed'] for result in results), 'issues': issues,
            'candidates': results, 'counts': {'candidates': len(candidates),
                                             'machine_passed': sum(result['passed'] for result in results),
                                             'conflicts': sum(item['code'] == 'FIELD_CONFLICT' for item in issues)}}
