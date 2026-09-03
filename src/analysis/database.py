import sqlite3
import json
from pathlib import Path
from config import settings

def get_db():
    db_path = Path(settings.db_dir) / "analysis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
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
    conn.close()

def save_analysis(article_id: str, filename: str, file_hash: str, analysis: dict):
    conn = get_db()
    c = conn.cursor()
    
    decision = analysis.get("decision", "REVISÃO MANUAL")
    exclusion_code = analysis.get("exclusion_code")
    confidence = analysis.get("confidence", "BAIXO")
    
    c.execute('''
        INSERT INTO articles (article_id, filename, hash, status, decision, exclusion_code, confidence, analysis_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            status=excluded.status,
            decision=excluded.decision,
            exclusion_code=excluded.exclusion_code,
            confidence=excluded.confidence,
            analysis_json=excluded.analysis_json,
            updated_at=CURRENT_TIMESTAMP
    ''', (article_id, filename, file_hash, "COMPLETED", decision, exclusion_code, confidence, json.dumps(analysis, ensure_ascii=False)))
    
    conn.commit()
    conn.close()

