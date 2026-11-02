"""Reference provenance is independent of production verdicts."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping
from .contracts import canonical_hash
from .snapshot import safe_path

FAMILIES={'G1':'SOURCE_FIDELITY','G2':'RETRIEVAL_USE','G3':'WRITER_CLAIMS',
          'G4':'PROVENANCE_VERDICT','G5':'DOCUMENT_QUALITY'}

@dataclass(frozen=True)
class ReferenceView:
    records: tuple[Mapping[str,Any],...]=()
    review_status: Mapping[str,str]=field(default_factory=lambda:{k:'PROVISIONAL' for k in FAMILIES})
    limitations: tuple[str,...]=()

    def for_family(self,family):
        return [r for r in self.records if r.get('family')==family]

def load_references(manifest_path,allowed_roots) -> ReferenceView:
    path=safe_path(manifest_path,allowed_roots)
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version')!='full_chain_reference_manifest_v1':
        raise ValueError('UNSUPPORTED_REFERENCE_SCHEMA')
    records=[]; limitations=[]; statuses={k:'PROVISIONAL' for k in FAMILIES}
    for item in manifest.get('files',[]):
        labels_path=safe_path(path.parent/item['path'],allowed_roots)
        raw=labels_path.read_bytes()
        from hashlib import sha256
        if sha256(raw).hexdigest()!=item['sha256']: raise ValueError('REFERENCE_FILE_HASH_MISMATCH')
        for line in raw.splitlines():
            row=json.loads(line)
            if row.get('family') not in FAMILIES: raise ValueError('UNKNOWN_REFERENCE_FAMILY')
            if 'source' in row and sha256(row['source'].encode('utf-8')).hexdigest()!=row.get('source_sha256'):
                raise ValueError('REFERENCE_SOURCE_HASH_MISMATCH')
            if any(r.get('reference_id')==row.get('reference_id') for r in records):
                raise ValueError('DUPLICATE_REFERENCE_ID')
            row=dict(row); family=row['family']
            status='SYNTHETIC_MECHANISM' if row.get('reference_kind')=='SYNTHETIC_MECHANISM' else 'PROVISIONAL'
            receipt_ref=row.get('review_receipt')
            if receipt_ref:
                receipt=json.loads(safe_path(path.parent/receipt_ref,allowed_roots).read_text(encoding='utf-8'))
                label_hash=canonical_hash(row.get('labels'))
                valid=(receipt.get('schema_version')=='independent_reference_review_v1'
                    and receipt.get('method')=='INDEPENDENT_HUMAN_REVIEW'
                    and receipt.get('reviewer') and receipt.get('reviewer')!=row.get('author')
                    and receipt.get('reviewer')==manifest.get('trusted_reviewers',{}).get(receipt.get('reviewer_id'))
                    and receipt.get('source_sha256')==row.get('source_sha256')
                    and receipt.get('labels_sha256')==label_hash
                    and receipt.get('match_rules_sha256')==canonical_hash(row.get('match_rules'))
                    and receipt.get('verdict')=='APPROVED')
                if valid: status='REVIEWED_BY_INDEPENDENT_HUMAN'
                else: limitations.append('INVALID_INDEPENDENT_REVIEW:'+row.get('reference_id','unknown'))
            row['effective_review_status']=status; records.append(row)
    for family in FAMILIES:
        family_rows=[r for r in records if r['family']==family]
        if family_rows and all(r['effective_review_status']=='REVIEWED_BY_INDEPENDENT_HUMAN' for r in family_rows):
            statuses[family]='REVIEWED_BY_INDEPENDENT_HUMAN'
        if not family_rows: limitations.append('REFERENCE_FAMILY_MISSING:'+family)
    return ReferenceView(tuple(records),statuses,tuple(limitations))
