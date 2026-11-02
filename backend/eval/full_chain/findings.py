"""Passive failures retain observed evidence and uncertain execution order."""
from .contracts import Finding

def _observed_order(edge, stages):
    """Only an exact, unique endpoint's recorded session sequence orders edges."""
    matches = [s for s in stages if s.key == edge.target_key]
    if len(matches) != 1:
        return None
    stage = matches[0]
    row = stage.payload
    if not stage.evidence_refs or not all(ref.integrity == 'VALID' and ref.completeness == 'COMPLETE' for ref in stage.evidence_refs):
        return None
    sequence = row.get('sequence')
    if type(sequence) is not int or sequence < 1 or not row.get('session_id') or not row.get('segment_id'):
        return None
    return (row['session_id'], row['segment_id'], sequence)


def observed_edge_failures(edges, *, subject_id='unknown', stages=()):
    failed = [edge for edge in edges if edge.assertion_result is False]
    if not failed:
        return []
    orders = [_observed_order(edge, stages) for edge in failed]
    comparable = (all(order is not None for order in orders)
                  and len({order[:2] for order in orders}) == 1
                  and len({order[2] for order in orders}) == len(orders))
    unknowns = ('Unobserved edges may contain an earlier failure; observation does not prove cause',)
    if not comparable:
        unknowns += ('EARLIEST_FAILURE_NOT_PROVEN: no complete common execution ordering',)
    result = [Finding('OBSERVED_EDGE_FAILURE', subject_id, edge.reason,
        edge_id=edge.edge_id, evidence_level='OBSERVED',
        evidence_refs=edge.source_refs + edge.target_refs, unknowns=unknowns) for edge in failed]
    earliest = failed[min(range(len(failed)), key=lambda i: orders[i][2])] if comparable else None
    if earliest:
        result.append(Finding('EARLIEST_OBSERVED_FAILURE', subject_id,
            'Earliest failed observation in the recorded common session sequence; no causal conclusion',
            edge_id=earliest.edge_id, evidence_level='OBSERVED',
            evidence_refs=earliest.source_refs + earliest.target_refs, unknowns=unknowns))
    # Keep the pre-existing machine code readable for historical consumers and
    # the immutable independent probe. Its message and unknowns withdraw the
    # old first/reproduced claim; an unordered set has no selected first edge.
    selected = earliest or (failed[0] if len(failed) == 1 else None)
    result.append(Finding('FIRST_OBSERVED_EDGE_FAILURE', subject_id,
        'Observed failure summary; earliest failure not proven' if earliest is None else 'Earliest observed failure; cause remains unproven',
        edge_id=selected.edge_id if selected else None, evidence_level='OBSERVED',
        evidence_refs=tuple(ref for edge in failed for ref in edge.source_refs + edge.target_refs),
        unknowns=unknowns))
    return result


def first_broken_edge(edges, *, subject_id='unknown', stages=()):
    """Compatibility summary; never sorts edge IDs or claims reproduction."""
    findings = observed_edge_failures(edges, subject_id=subject_id, stages=stages)
    return next((f for f in findings if f.code == 'FIRST_OBSERVED_EDGE_FAILURE'), None)
