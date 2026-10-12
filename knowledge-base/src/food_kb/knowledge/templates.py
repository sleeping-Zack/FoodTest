"""Shared candidate contracts; template conformance does not grant approval."""

SCHEMA_VERSION = 'knowledge-1'
REQUIREMENT_TYPES = frozenset({
    'upper_limit', 'lower_limit', 'range', 'qualitative', 'use_restriction',
    'formula', 'sampling_plan', 'unknown',
})
STANDARD_TEMPLATES = {
    'general': {
        'label': '通用标准',
        'fields': ('subject', 'project', 'requirement_type', 'value', 'condition', 'exceptions', 'references'),
    },
    'product': {
        'label': '产品标准',
        'fields': ('subject', 'definition', 'scope_text', 'project', 'value', 'method_reference', 'general_standard_relation'),
    },
    'practice': {
        'label': '生产经营规范',
        'fields': ('stage', 'subject', 'measure', 'target', 'responsible_party', 'condition', 'required_evidence'),
    },
    'method': {
        'label': '检验方法',
        'fields': ('method_name', 'scope', 'submethods', 'matrix', 'project', 'components',
                   'sample_preparation', 'principle', 'reagents', 'equipment', 'steps',
                   'parameters', 'formulas', 'precision', 'lod_loq', 'general_requirements'),
    },
}
RECORD_FIELDS = {
    'requirement': ('subject', 'project', 'requirement_type', 'value'),
    'definition': ('subject', 'definition'),
    'method': ('method_name',),
    'practice': ('stage', 'measure', 'responsible_party'),
    'standard_scope_catalog': ('subject', 'scope_text'),
}
SCOPE_CATALOG_FIELDS = ('namespaces', 'regime', 'date_conditions', 'coverage_state')

# Input facts must retain their own provenance and verification state. These
# names define a contract, rather than allowing model-generated field paths.
ALLOWED_FACTS = frozenset({
    'product_identity', 'measurement_basis', 'food_name', 'food_species', 'food_part',
    'process', 'physical_state', 'intended_use', 'population', 'production_stage',
    'regime', 'business_date', 'date_role', 'category_id', 'category_namespace',
    'matrix', 'project', 'component', 'sampling_plan',
})
FACT_TYPES = frozenset({'string', 'number', 'boolean', 'date', 'set'})
