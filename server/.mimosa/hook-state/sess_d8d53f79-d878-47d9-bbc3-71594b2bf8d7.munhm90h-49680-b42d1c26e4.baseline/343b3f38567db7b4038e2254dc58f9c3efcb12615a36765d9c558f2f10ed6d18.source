"""Execution Contract store (execution-audit Spec §8.5/§11).

A contract is the structured "what SHOULD happen" — required steps, their
dependencies, the tools that evidence each step, and success conditions.
Sources, in trust order:
  explicit   — authored in SKILL.md frontmatter (`execution_contract:` YAML)
               or registered via the admin API
  inferred   — LLM/手动 drafted, must keep confidence < 1 and be reviewed
No contract ⇒ the auditor reports unknown, never invents violations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 'v1'


def _hash_contract(contract: dict) -> str:
    return hashlib.sha256(
        json.dumps(contract, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()


def contract_from_skill_md(path: str) -> dict | None:
    """Extract `execution_contract:` from a SKILL.md frontmatter block.

    Returns the contract dict or None when absent/invalid. Invalid YAML is
    logged, not raised — a broken contract must not break skill upload."""
    try:
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read(65536)
        if not text.startswith('---'):
            return None
        end = text.find('\n---', 3)
        if end < 0:
            return None
        meta = yaml.safe_load(text[3:end]) or {}
        contract = meta.get('execution_contract')
        if isinstance(contract, dict) and isinstance(contract.get('steps'), list):
            contract.setdefault('forbidden_tools', [])
            contract.setdefault('success_conditions', [])
            return contract
        return None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning('contract parse failed %s: %s', path, e)
        return None


def upsert_contract(name: str, *, owner_type: str, owner_id: str,
                    source: str, contract: dict,
                    confidence: float | None = None) -> str:
    """Insert/activate a contract version (keeps history via new id)."""
    from db import get_db
    cid = 'con_' + secrets.token_hex(6)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_execution_contracts SET active = FALSE "
                "WHERE owner_type = %s AND owner_id = %s", (owner_type, owner_id))
            cur.execute(
                "INSERT INTO ai_execution_contracts "
                "(id, name, owner_type, owner_id, source, content_hash, "
                " schema_version, contract, confidence, active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)",
                (cid, name, owner_type, owner_id, source,
                 _hash_contract(contract), SCHEMA_VERSION,
                 json.dumps(contract, ensure_ascii=False), confidence))
        conn.commit()
    return cid


def get_active_contract(owner_type: str, owner_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, source, contract, confidence, content_hash "
                "FROM ai_execution_contracts "
                "WHERE owner_type=%s AND owner_id=%s AND active "
                "ORDER BY created_at DESC LIMIT 1", (owner_type, owner_id))
            row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'name': row[1], 'source': row[2],
            'contract': row[3] if isinstance(row[3], dict)
            else json.loads(row[3]),
            'confidence': float(row[4]) if row[4] is not None else None,
            'content_hash': row[5]}


def contract_for_skill(skill_name: str, skill_md_path: str | None = None) -> dict | None:
    """Active DB contract for a skill; falls back to parsing its SKILL.md
    frontmatter (auto-registering what it found as explicit)."""
    active = get_active_contract('skill', skill_name)
    if active:
        return active
    if skill_md_path and os.path.isfile(skill_md_path):
        contract = contract_from_skill_md(skill_md_path)
        if contract:
            upsert_contract(skill_name, owner_type='skill', owner_id=skill_name,
                            source='explicit', contract=contract, confidence=1.0)
            return get_active_contract('skill', skill_name)
    return None
