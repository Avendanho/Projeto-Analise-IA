import sqlite3
import json
import threading
from typing import Dict, Optional, List
from pathlib import Path
from ..domain.models import FinalResult, ScreeningDecision

class AnalysisRepository:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_dir: str):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AnalysisRepository, cls).__new__(cls)
                cls._instance._init_repo(db_dir)
            return cls._instance

    def _init_repo(self, db_dir: str):
        self.db_path = Path(db_dir) / "analysis.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    filename TEXT,
                    hash TEXT,
                    status TEXT,
                    decision TEXT,
                    exclusion_code TEXT,
                    confidence TEXT,
                    analysis_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

        self._cache = {}
        self._load_cache()

    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.row_factory = sqlite3.Row
        return conn

    def _load_cache(self):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT article_id, hash, status, analysis_json FROM articles").fetchall()
            for r in rows:
                self._cache[r['article_id']] = {
                    'hash': r['hash'],
                    'status': r['status'],
                    'analysis_json': r['analysis_json']
                }

    def get_article(self, article_id: str) -> Optional[Dict]:
        return self._cache.get(article_id)

    def save_final_result(self, filename: str, file_hash: str, result: FinalResult):
        # Convert Pydantic object to dict correctly preserving types
        result_dict = result.model_dump(mode='json')
        
        # Legacy compatibility format mapping
        analysis_json = {
            "decision": result.decision.value,
            "exclusion_code": result.exclusion_code,
            "confidence": result.confidence,
            "justificativa": result.justification,
            "raw_json": result_dict,
            # For report compat
            "key_synthesis": result.key_synthesis or "N/A",
            "project_value_added": result.project_value_added or "N/A"
        }
        
        from analiseia.analysis.evidence.store import global_evidence_store
        
        for cid, cres in result.criteria_results.items():
            analysis_json[cid] = cres.answer
            if "extracted_snippets" not in analysis_json:
                analysis_json["extracted_snippets"] = {}
            
            snippets = []
            for eid in cres.evidence_ids:
                ev = global_evidence_store.get_evidence(eid)
                if ev:
                    snippets.append(ev.text)
            
            analysis_json["extracted_snippets"][cid] = snippets
            
        analysis_str = json.dumps(analysis_json, ensure_ascii=False)
        
        db_status = "ERROR" if result.decision.value == "PROCESSAMENTO COM FALHA" else "COMPLETED"
        self._cache[result.article_id] = {
            'hash': file_hash,
            'status': db_status,
            'analysis_json': analysis_str
        }
        
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO articles (article_id, filename, hash, status, decision, exclusion_code, confidence, analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    filename=excluded.filename,
                    hash=excluded.hash,
                    status=excluded.status,
                    decision=excluded.decision,
                    exclusion_code=excluded.exclusion_code,
                    confidence=excluded.confidence,
                    analysis_json=excluded.analysis_json,
                    updated_at=CURRENT_TIMESTAMP
            ''', (
                result.article_id, filename, file_hash, db_status, 
                result.decision.value, result.exclusion_code, 
                result.confidence, analysis_str
            ))
            conn.commit()
