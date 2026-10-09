"""Replay every indexed definition name and audit table linkage, not semantics."""
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from engine import Library, VERSION

library = Library()
relations = [dict(r, section_id=d['section_id']) for d in library.definitions.values() for r in d['relationships']]
queries = []
failures = []
for food in sorted(library.definition_terms):
    result = library.classify({'food': food})
    resolution = result['inspection_resolution']
    record = {'food': food, 'state': resolution['state'], 'status': result['status'],
              'selected': result['selected']['label'] if result['selected'] else None,
              'items': result['counts']['items'], 'option_count': len(resolution['options'])}
    if result['selected']:
        entry = library.entries[result['selected']['id']]
        if entry['kind'] != 'leaf' or not result['inspection_rows'] or {x['id'] for x in result['inspection_rows']} != {x['id'] for x in entry['rows']}:
            failures.append(record)
    elif result['counts']['items'] is not None:
        failures.append(record)
    queries.append(record)
report = {'engine_version': VERSION, 'fingerprint': library.fingerprint,
          'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
          'definition_sections': len(library.definitions), 'relation_count': len(relations),
          'relation_states': dict(Counter(r['link_state'] for r in relations)),
          'query_count': len(queries), 'query_states': dict(Counter(q['state'] for q in queries)),
          'failures': failures, 'queries': queries,
          'unmapped_relations': [r for r in relations if r['link_state']=='unmapped'],
          'note': '逐名称回放验证有项目表时数据不被界面选择状态清空；未关联数值为 null。有歧义按分支展示，不合并整个分节。此核验不证明分类语义准确率或全部食品覆盖。'}
path = library.lib / '索引/食品层级检验表关联核验.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps({k:report[k] for k in ['definition_sections','relation_count','relation_states','query_count','query_states','failures']}, ensure_ascii=False))
if failures:
    raise SystemExit(1)
