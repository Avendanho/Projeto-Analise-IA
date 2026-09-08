import sqlite3
import json
import threading
from typing import Dict, Optional, List
from pathlib import Path
from config import settings

class AnalysisRepository:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AnalysisRepository, cls).__new__(cls)
                cls._instance._init_repo()
            return cls._instance

    def _init_repo(self):
        self.db_path = Path(settings.db_dir) / "analysis.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Init table
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

        # Load cache
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

    def save_analysis(self, article_id: str, filename: str, file_hash: str, analysis: dict):
        decision = analysis.get("decision", "REVISÃO MANUAL")
        exclusion_code = analysis.get("exclusion_code")
        confidence = analysis.get("confidence", "BAIXO")
        analysis_json = json.dumps(analysis, ensure_ascii=False)
        
        # Update cache immediately
        self._cache[article_id] = {
            'hash': file_hash,
            'status': "COMPLETED",
            'analysis_json': analysis_json
        }
        
        # Perform single write (Batch queueing could be added for extreme scale, but reusing single conn with WAL is 100x faster already)
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO articles (article_id, filename, hash, status, decision, exclusion_code, confidence, analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    status=excluded.status,
                    decision=excluded.decision,
                    exclusion_code=excluded.exclusion_code,
                    confidence=excluded.confidence,
                    analysis_json=excluded.analysis_json,
                    updated_at=CURRENT_TIMESTAMP
            ''', (article_id, filename, file_hash, "COMPLETED", decision, exclusion_code, confidence, analysis_json))
            conn.commit()
            
    def save_batch(self, records: List[Dict]):
        """Salva múltiplos registros em uma única transação."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            for r in records:
                article_id = r['article_id']
                file_hash = r['file_hash']
                analysis_json = json.dumps(r['analysis'], ensure_ascii=False)
                
                self._cache[article_id] = {
                    'hash': file_hash,
                    'status': "COMPLETED",
                    'analysis_json': analysis_json
                }
                
                cursor.execute('''
                    INSERT INTO articles (article_id, filename, hash, status, decision, exclusion_code, confidence, analysis_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(article_id) DO UPDATE SET
                        status=excluded.status,
                        decision=excluded.decision,
                        exclusion_code=excluded.exclusion_code,
                        confidence=excluded.confidence,
                        analysis_json=excluded.analysis_json,
                        updated_at=CURRENT_TIMESTAMP
                ''', (article_id, r['filename'], file_hash, "COMPLETED", r['analysis'].get("decision", "REVISÃO MANUAL"), r['analysis'].get("exclusion_code"), r['analysis'].get("confidence", "BAIXO"), analysis_json))
            conn.commit()


# Interfaces compatíveis para não quebrar main.py e report
def get_db():
    repo = AnalysisRepository()
    return repo._get_conn()

def init_db():
    AnalysisRepository()

def get_article(article_id: str) -> dict:
    return AnalysisRepository().get_article(article_id)

def save_analysis(article_id: str, filename: str, file_hash: str, analysis: dict):
    AnalysisRepository().save_analysis(article_id, filename, file_hash, analysis)
