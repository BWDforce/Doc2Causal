# -*- coding: utf-8 -*-
'''Conservative generic entity and alias resolution for D2C_13_2.'''
from __future__ import annotations
import copy
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any
from .util import as_text, sha1
NEGATION = {'non', 'not', 'no', 'without', 'never', 'inactive', 'absent', 'abnormal', 'failed', 'invalid', 'disabled', 'off', 'false'}
STATE = {'active', 'present', 'normal', 'ok', 'valid', 'enabled', 'on', 'true', 'connected', 'healthy', 'available', 'ready', 'working', 'running', 'online', 'stable', 'success', 'successful', 'good', 'correct', 'complete'}
GOOD_STATE_TERMS = STATE
BAD_STATE_TERMS = {'error', 'errors', 'fault', 'faults', 'failure', 'failed', 'fail', 'problem', 'problems', 'issue', 'issues', 'invalid', 'abnormal', 'disabled', 'off', 'offline', 'inactive', 'absent', 'unavailable', 'corrupt', 'corrupted', 'garbled', 'garbling', 'missing', 'broken', 'blocked', 'unconnected'}
ALIAS_CUES = ('aka', 'also known as', 'referred to as', 'known as', 'abbreviated as', 'stands for', 'defined as', 'i.e.', 'that is', 'means')
TIME_RE = re.compile(r'(?<!\d)([01]?\d|2[0-3])[\s:._-]+([0-5]\d)(?!\d)')
NUMBER_RE = re.compile(r'(?<![\d.])\d+(?:\.\d+)?(?![\d.])')
WORD_NUMBERS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13',
    'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
    'eighteen': '18', 'nineteen': '19', 'twenty': '20',
    'first': '1', 'second': '2', 'third': '3', 'fourth': '4',
    'fifth': '5', 'sixth': '6', 'seventh': '7', 'eighth': '8',
    'ninth': '9', 'tenth': '10', 'eleventh': '11', 'twelfth': '12',
    'thirteenth': '13', 'fourteenth': '14', 'fifteenth': '15',
    'sixteenth': '16', 'seventeenth': '17', 'eighteenth': '18',
    'nineteenth': '19', 'twentieth': '20',
}
def _norm(value: Any) -> str:
    text = unicodedata.normalize('NFKC', as_text(value)).casefold()
    text = TIME_RE.sub(lambda match: f"{int(match.group(1)):02d} {match.group(2)}", text)
    text = text.replace('_', ' ').replace('-', ' ').replace(':', ' ')
    text = re.sub(r'[^\w\s.]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' .,:;()[]{}\"')
def _camel_text(value: Any) -> str:
    text = as_text(value)
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text)
    text = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', text)
    return text
def _tokens(value: Any) -> list[str]:
    return re.findall(r'[a-z0-9]+', _norm(_camel_text(value)))
def _times(value: Any) -> set[str]:
    text = unicodedata.normalize('NFKC', as_text(value))
    values = {f"{int(hour):02d}:{minute}" for hour, minute in TIME_RE.findall(text)}
    # Resolve common lexical time forms without treating them as aliases of
    # every nearby clock value.  ``midday`` is the same referent as noon;
    # this guard is corpus-independent and prevents a high embedding score
    # from merging different clock-indexed measurements.
    if re.search(r'\b(?:noon|midday)\b', text, flags=re.IGNORECASE):
        values.add('12:00')
    if re.search(r'\bmidnight\b', text, flags=re.IGNORECASE):
        values.add('00:00')
    return values
def _numbers(value: Any) -> set[str]:
    text = unicodedata.normalize('NFKC', as_text(value))
    text = TIME_RE.sub(' ', text).replace('_', ' ')
    values = set(NUMBER_RE.findall(text))
    # Numerical identity also appears in prose as ordinals/cardinals (for
    # example ``fifth issue flag`` versus ``sixth issue flag``).  Keeping
    # these in the same namespace blocks high-embedding false merges.
    for token in _tokens(text):
        if token in WORD_NUMBERS:
            values.add(WORD_NUMBERS[token])
    return values
def _state(value: Any) -> dict:
    tokens = set(_tokens(value))
    negated = bool(tokens & NEGATION)
    good = bool(tokens & GOOD_STATE_TERMS)
    bad = bool(tokens & BAD_STATE_TERMS)
    if negated and bad and not good:
        status = 'ok'
    elif negated and good:
        status = 'not_ok'
    elif bad and not good:
        status = 'bad'
    elif good and not bad:
        status = 'ok'
    elif good and bad:
        status = 'mixed'
    else:
        status = None
    return {'negation': negated, 'state_terms': sorted(tokens & (GOOD_STATE_TERMS | BAD_STATE_TERMS)), 'status': status}
def _type(entity: dict) -> str:
    return as_text(entity.get('entity_type') or entity.get('type')).strip().lower()
def _labels(entity: dict) -> list[str]:
    return [as_text(entity.get('label'))] + [as_text(x) for x in (entity.get('aliases') or []) if as_text(x)]
def _prefix_coverage(short: list[str], long: list[str]) -> float:
    if not short or not long or len(short) > len(long):
        return 0.0
    matched = 0
    cursor = 0
    for token in short:
        for index in range(cursor, len(long)):
            candidate = long[index]
            if candidate.startswith(token) or token.startswith(candidate):
                matched += 1
                cursor = index + 1
                break
    return matched / len(short)
def _abbreviation(left: Any, right: Any) -> bool:
    a, b = _tokens(left), _tokens(right)
    if not a or not b or a == b:
        return False
    if len(a) == len(b):
        short, long = (a, b) if len(''.join(a)) <= len(''.join(b)) else (b, a)
    else:
        short, long = (a, b) if len(a) < len(b) else (b, a)
    if len(short) > 4 or len(short) > len(long):
        return False
    compact_short, compact_long = ''.join(short), ''.join(long)
    if len(compact_short) / max(1, len(compact_long)) > 0.85:
        return False
    # Require strong token-prefix coverage. Character-subsequence matching is
    # intentionally excluded because it over-links unrelated short labels.
    coverage = sum(1 for token in short if any(other.startswith(token) or token.startswith(other) for other in long)) / len(short)
    if coverage >= 0.99 and (len(short) >= 2 or len(compact_short) >= 4):
        return True
    acronym = ''.join(token[0] for token in long if token)
    return len(compact_short) >= 3 and (acronym == compact_short or acronym.startswith(compact_short))
def _compatible(left: dict, right: dict) -> list[str]:
    reasons = []
    if _type(left) and _type(right) and _type(left) != _type(right):
        reasons.append('type_conflict')
    # Compatibility must inspect every surface on the entity, not only the
    # alphabetically selected primary label. Otherwise an alias with a hidden
    # time/number/state modifier can bypass the identity guard.
    lt = {value for label in _labels(left) for value in _times(label)} or set(left.get('time_indices') or [])
    rt = {value for label in _labels(right) for value in _times(label)} or set(right.get('time_indices') or [])
    if lt and rt and lt != rt:
        reasons.append('time_conflict')
    ln = {value for label in _labels(left) for value in _numbers(label)}
    rn = {value for label in _labels(right) for value in _numbers(label)}
    if ln and rn and ln != rn:
        reasons.append('numeric_conflict')
    left_states = {_state(label).get('status') for label in _labels(left) if _state(label).get('status')}
    right_states = {_state(label).get('status') for label in _labels(right) if _state(label).get('status')}
    if left_states and right_states and left_states != right_states:
        reasons.append('state_conflict')
    return reasons
def _score(left: Any, right: Any) -> tuple[float, dict]:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0, {'reason': 'empty'}
    if a == b:
        return 1.0, {'reason': 'exact', 'abbreviation': False}
    ta, tb = set(_tokens(left)), set(_tokens(right))
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sequence = SequenceMatcher(None, a, b).ratio()
    abbreviation = _abbreviation(left, right)
    base = max(sequence, 0.88 * jaccard + 0.12 * sequence, 0.97 if abbreviation else 0.0)
    ls, rs = _state(left), _state(right)
    state_aware = False
    if ls.get('status') and ls.get('status') == rs.get('status'):
        ignored = GOOD_STATE_TERMS | BAD_STATE_TERMS | NEGATION
        left_core = {t for t in _tokens(left) if t not in ignored}
        right_core = {t for t in _tokens(right) if t not in ignored}
        state_aware = any(a_token.startswith(b_token) or b_token.startswith(a_token) for a_token in left_core for b_token in right_core if len(a_token) >= 3 and len(b_token) >= 3)
    if state_aware and ls.get('status') == 'ok':
        base = max(base, 0.88)
    reason = 'state_aware_review' if state_aware and ls.get('status') == 'ok' else 'similarity'
    return base, {'reason': reason, 'abbreviation': abbreviation, 'sequence': round(sequence, 4), 'jaccard': round(jaccard, 4), 'state_aware': state_aware}


def _context_values(entity: dict, context_by_id: dict[str, list[str]] | None = None) -> list[str]:
    values = []
    if context_by_id:
        values.extend(context_by_id.get(as_text(entity.get('id')), []) or [])
    for key in ('context_texts', 'context'):
        value = entity.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    return list(dict.fromkeys(values))


def _surface_pattern(value: str) -> str:
    tokens = re.findall(r'[a-z0-9]+', _norm(value))
    return r'\s+'.join(re.escape(token) for token in tokens)


def _surface_pattern_flexible(value: str) -> str:
    tokens = re.findall(r'[a-z0-9]+', _norm(value))
    return r'[\s:._-]+'.join(re.escape(token) for token in tokens)


def _explicit_alias_link(text: str, left: str, right: str) -> bool:
    # Check ``Entity: definition`` before normalizing colons away.  Structured
    # engineering documents commonly use this form, and replacing ':' first
    # turns an explicit definition into mere context co-occurrence.
    colon_raw = re.sub(r'\s+', ' ', as_text(text).casefold()).strip()
    colon_left = _surface_pattern_flexible(left)
    colon_right = _surface_pattern_flexible(right)
    if colon_left and colon_right:
        colon_patterns = [
            rf'^\s*{colon_left}\s*:\s*{colon_right}\s*[.!?;]?\s*$',
            rf'^\s*{colon_right}\s*:\s*{colon_left}\s*[.!?;]?\s*$',
        ]
        if any(re.search(pattern, colon_raw, flags=re.IGNORECASE | re.DOTALL) for pattern in colon_patterns):
            return True
    raw = re.sub(r'\s+', ' ', as_text(text).casefold().replace('_', ' ').replace(':', ' ').replace('-', ' '))
    a = _surface_pattern(left)
    b = _surface_pattern(right)
    if not a or not b:
        return False
    patterns = [
        rf'{a}\s*\(\s*{b}',
        rf'{b}\s*\(\s*{a}',
        rf'{a}\s*=\s*{b}',
        rf'{b}\s*=\s*{a}',
        rf'{a}\s*:\s*{b}',
        rf'{b}\s*:\s*{a}',
    ]
    cues = r'(?:also\s+known\s+as|referred\s+to\s+as|known\s+as|stands\s+for|defined\s+as|abbreviated\s+as|i\.e\.|that\s+is|means)'
    patterns.extend([
        rf'{a}.{{0,120}}{cues}.{{0,120}}{b}',
        rf'{b}.{{0,120}}{cues}.{{0,120}}{a}',
    ])
    return any(re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _context_alias_score(left: dict, right: dict, left_contexts: list[str], right_contexts: list[str]) -> tuple[float, dict]:
    contexts = list(dict.fromkeys([*left_contexts, *right_contexts]))
    if not contexts:
        return 0.0, {}
    for a in _labels(left):
        for b in _labels(right):
            an, bn = _norm(a), _norm(b)
            if not an or not bn or an == bn:
                continue
            for text in contexts:
                normalized = _norm(text)
                if an not in normalized or bn not in normalized:
                    continue
                if _explicit_alias_link(text, a, b):
                    return 1.0, {'reason': 'context_definition', 'context': text, 'alias_a': a, 'alias_b': b}
                return 0.9, {'reason': 'context_cooccurrence', 'context': text, 'alias_a': a, 'alias_b': b}
    return 0.0, {}


def entity_match(left: dict, right: dict, left_contexts: list[str] | None = None, right_contexts: list[str] | None = None) -> dict:
    reasons = _compatible(left, right)
    best = 0.0
    detail = {}
    for a in _labels(left):
        for b in _labels(right):
            score, row = _score(a, b)
            if score > best:
                best, detail = score, row
    context_score, context_detail = _context_alias_score(left, right, left_contexts or _context_values(left), right_contexts or _context_values(right))
    if context_score > best:
        best, detail = context_score, context_detail
    return {'score': round(best, 4) if not reasons else 0.0, 'compatible': not reasons, 'reasons': reasons, 'features': detail, 'abbreviation': bool(detail.get('abbreviation')), 'state_left': _state(left.get('label')), 'state_right': _state(right.get('label')), 'a_label': left.get('label'), 'b_label': right.get('label')}
def _label_rank(entity: dict) -> tuple:
    label = as_text(entity.get('label'))
    return (bool(_times(label)), bool(re.search(r'[A-Z].*[A-Z]|[0-9]|_', label)), len(_tokens(label)), -len(label), label)
def _role_profile_conflict(left: dict, right: dict, min_observations: int = 2) -> str | None:
    """Return a generic source/target role asymmetry for two mentions."""
    lc = (left.get("role_counts") or {}) if isinstance(left, dict) else {}
    rc = (right.get("role_counts") or {}) if isinstance(right, dict) else {}
    left_source = int(lc.get("source", 0) or 0)
    left_target = int(lc.get("target", 0) or 0)
    right_source = int(rc.get("source", 0) or 0)
    right_target = int(rc.get("target", 0) or 0)
    if left_source >= min_observations and right_target >= min_observations and left_target == 0 and right_source == 0:
        return "source_target_role_asymmetry"
    if left_target >= min_observations and right_source >= min_observations and left_source == 0 and right_target == 0:
        return "source_target_role_asymmetry"
    return None

def resolve_entity_aliases(artifact: dict, auto_merge_threshold: float = 0.97, review_threshold: float = 0.84, context_by_id: dict[str, list[str]] | None = None, forced_pairs: set[tuple[str, str]] | None = None, extra_review_candidates: list[dict] | None = None, max_review_per_entity: int = 2, role_profiles: dict[str, dict] | None = None, relation_role_guard: bool = True) -> dict:
    entities = artifact.setdefault('entities', [])
    if len(entities) < 2:
        return {'auto_merges': 0, 'review_candidates': 0, 'self_loops_rejected': 0}
    parent = list(range(len(entities)))
    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    candidates = []
    auto_merges = 0
    forced = {tuple(sorted((str(a), str(b)))) for a, b in (forced_pairs or set())}
    entity_ids = {str(entity.get('id')): index for index, entity in enumerate(entities)}
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            pair_key = tuple(sorted((str(entities[i].get('id')), str(entities[j].get('id')))))
            is_forced = pair_key in forced
            match = entity_match(
                entities[i], entities[j],
                _context_values(entities[i], context_by_id),
                _context_values(entities[j], context_by_id),
            )
            if match['score'] < review_threshold and not is_forced:
                continue
            reason = match['features'].get('reason')
            role_conflict = None
            if relation_role_guard and role_profiles:
                role_conflict = _role_profile_conflict(role_profiles.get(str(entities[i].get('id')), {}), role_profiles.get(str(entities[j].get('id')), {}))
            base_strong = is_forced or (match['compatible'] and match['score'] >= auto_merge_threshold and reason in {'exact', 'normalized_exact', 'context_definition'})
            strong = bool(base_strong and not role_conflict)
            features = dict(match['features'])
            if role_conflict:
                features['relation_role_reason'] = role_conflict
            candidates.append({'a': entities[i].get('id'), 'b': entities[j].get('id'), 'a_label': entities[i].get('label'), 'b_label': entities[j].get('label'), 'score': match['score'], 'decision': 'auto_merge' if strong else 'needs_review', 'compatible': match['compatible'], 'reasons': match['reasons'] + ([role_conflict] if role_conflict else []), 'features': features, 'relation_role_guard': bool(role_conflict), 'role_guard_suppressed_auto': bool(role_conflict and base_strong), 'source': 'document_alias' if is_forced else 'label_alias'})
            if strong:
                union(i, j)
                auto_merges += 1

    # Add document-level candidates that the local label scorer would not see.
    seen_candidate = {(str(row.get('a')), str(row.get('b'))) for row in candidates}
    for row in extra_review_candidates or []:
        a, b = str(row.get('entity_a')), str(row.get('entity_b'))
        if not a or not b or a == b or a not in entity_ids or b not in entity_ids:
            continue
        key = tuple(sorted((a, b)))
        if key in seen_candidate:
            continue
        seen_candidate.add(key)
        candidates.append({
            'a': a, 'b': b,
            'a_label': entities[entity_ids[a]].get('label'),
            'b_label': entities[entity_ids[b]].get('label'),
            'score': row.get('score', 0.0),
            'decision': 'needs_review',
            'compatible': True,
            'reasons': row.get('reasons', []),
            'features': row.get('features', {'reason': row.get('reason', 'document_context')}),
            'source': 'document_alias',
        })

    # Keep high-confidence auto merges, but cap document review candidates per
    # entity using a generic relative rule rather than a corpus-specific count.
    auto_rows = [row for row in candidates if row['decision'] == 'auto_merge']
    review_rows = sorted(
        [row for row in candidates if row['decision'] == 'needs_review'],
        key=lambda row: (float(row.get('score') or 0.0), float((row.get('features') or {}).get('sequence') or 0.0)),
        reverse=True,
    )
    kept_review = []
    per_entity = defaultdict(int)
    for row in review_rows:
        a, b = str(row.get('a')), str(row.get('b'))
        if per_entity[a] >= max_review_per_entity and per_entity[b] >= max_review_per_entity:
            continue
        kept_review.append(row)
        per_entity[a] += 1
        per_entity[b] += 1
    candidates = auto_rows + kept_review
    groups = defaultdict(list)
    for index, entity in enumerate(entities):
        groups[find(index)].append(entity)
    id_map = {}
    label_map = {}
    merged = []
    for group in groups.values():
        chosen = max(group, key=_label_rank)
        canonical = copy.deepcopy(chosen)
        aliases = set(canonical.get('aliases') or [])
        for entity in group:
            aliases.update(_labels(entity))
            id_map[as_text(entity.get('id'))] = as_text(canonical.get('id'))
            label_map[as_text(entity.get('id'))] = as_text(canonical.get('label'))
        aliases.discard(as_text(canonical.get('label')))
        canonical['aliases'] = sorted(aliases)
        canonical['identity_key'] = sha1('|'.join([_norm(canonical.get('label')), str(_state(canonical.get('label'))), str(sorted(_times(canonical.get('label'))))]))
        merged.append(canonical)
    artifact['entities'] = sorted(merged, key=lambda entity: as_text(entity.get('id')))
    self_loops = 0
    for edge in artifact.get('edges', []):
        source, target = as_text(edge.get('source')), as_text(edge.get('target'))
        if source in id_map:
            edge['source'] = id_map[source]
            edge['source_name'] = label_map.get(source, edge.get('source_name'))
        if target in id_map:
            edge['target'] = id_map[target]
            edge['target_name'] = label_map.get(target, edge.get('target_name'))
        if edge.get('source') == edge.get('target'):
            edge['kind'] = 'rejected'
            edge['edge_kind'] = 'rejected'
            edge['identity_status'] = 'self_loop'
            edge['verification_status'] = 'rejected'
            edge['provenance'] = 'alias_self_loop_guard'
            self_loops += 1
    for assertion in artifact.get('assertions', []):
        if as_text(assertion.get('source')) in id_map:
            assertion['source'] = id_map[as_text(assertion.get('source'))]
        if as_text(assertion.get('target')) in id_map:
            assertion['target'] = id_map[as_text(assertion.get('target'))]
    for hypothesis in artifact.get('hypotheses', []):
        if as_text(hypothesis.get('source')) in id_map:
            hypothesis['source'] = id_map[as_text(hypothesis.get('source'))]
        if as_text(hypothesis.get('target')) in id_map:
            hypothesis['target'] = id_map[as_text(hypothesis.get('target'))]
    diagnostics = artifact.setdefault('diagnostics', {})
    review_count = sum(1 for row in candidates if row['decision'] == 'needs_review')
    diagnostics['alias_resolution'] = {'auto_merges': auto_merges, 'review_candidates': review_count, 'review_candidates_total': len(review_rows), 'review_candidates_kept': review_count, 'relation_role_guard_blocked_auto_merges': sum(1 for row in candidates if row.get('role_guard_suppressed_auto')), 'self_loops_rejected': self_loops, 'context_candidates': sum(1 for row in candidates if row['features'].get('reason', '').startswith('context_')), 'document_alias_candidates': sum(1 for row in candidates if row.get('source') == 'document_alias'), 'candidates': candidates, 'protocol': 'normalized labels + relation-role compatibility + state compatibility + abbreviation + document context + local causal neighborhood + capped abstention'}
    return {'auto_merges': auto_merges, 'review_candidates': diagnostics['alias_resolution']['review_candidates'], 'self_loops_rejected': self_loops}
if __name__ == '__main__':
    raise SystemExit('entity_resolution is a library module')
