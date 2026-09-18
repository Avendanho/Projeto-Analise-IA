import re
from typing import Dict, List, Tuple
from rank_bm25 import BM25Okapi

CRITERIA_KEYWORDS = {
    "Q1_HUMAN": ["human", "child", "adult", "patient", "subject", "biopsy", "participant", "boy", "girl", "men", "women", "clinical", "post-mortem", "serum", "plasma", "blood"],
    "Q2_TEA_DIAGNOSIS": ["autism", "asd", "asperger", "autistic", "pervasive developmental disorder", "pdd-nos", "neurodevelopmental"],
    "Q3_FORMAL_DIAGNOSIS": ["dsm", "icd", "ados", "adi-r", "cars", "diagnosis", "diagnostic criteria", "psychologist", "psychiatrist", "formal diagnosis"],
    "Q4_SUBGROUP_DATA": ["stratification", "subgroup", "isolated", "separate", "comorbidity", "mixed"],
    "Q5_GENETIC_COMPONENT": ["gene", "rna", "dna", "genotype", "snp", "variant", "allele", "expression", "transcript", "methylation", "epigenetic", "transcriptome", "gwas", "wes", "wgs", "mutation", "polymorphism"],
    "Q6_GENETIC_MEASURED": ["sequence", "genotyping", "pcr", "microarray", "rna-seq", "rt-pcr", "assay", "measurement", "quantified", "extracted", "amplified", "genotyped", "sequenced"],
    "Q7_INFLAMMATORY": ["inflammation", "immune", "cytokine", "interleukin", "tnf", "ifn", "crp", "chemokine", "microglia", "astrocyte", "macrophage", "t-cell", "b-cell", "inflammatory marker", "neuroinflammation", "immune system", "autoantibody"],
    "Q8_INFLAMMATORY_MEASURED": ["elisa", "assay", "flow cytometry", "multiplex", "measured", "levels", "concentration", "serum levels", "quantified", "plasma levels", "luminex"],
    "Q9_GENE_INFLAMMATION_LINK": ["correlation", "association", "eqtl", "enrichment", "pathway", "interaction", "linked", "associated with", "correlated", "relationship", "network", "modulate", "regulate"],
    "Q10_TEA_CONTEXT": ["pathophysiology", "susceptibility", "role in", "pathogenesis", "mechanism", "etiology", "development"],
    "Q11_STUDY_DESIGN": ["case-control", "cohort", "cross-sectional", "clinical trial", "review", "meta-analysis", "study design", "methodology", "participants", "subjects"],
    "Q12_CONFIRM_NOT_ANIMAL": ["mouse", "rat", "murine", "zebrafish", "animal", "in vivo", "in vitro", "cell line", "macaque", "rodent", "model"]
}

class LocalRetriever:
    def __init__(self, sections_db: Dict[str, dict]):
        self.sections_db = sections_db
        self.section_ids = list(sections_db.keys())
        self.corpus = []
        
        for sid in self.section_ids:
            # Join heading and text, lowercase and tokenize
            text = (sections_db[sid].get("heading", "") + " " + sections_db[sid].get("text", "")).lower()
            tokens = re.findall(r'\b\w+\b', text)
            self.corpus.append(tokens)
            
        self.bm25 = BM25Okapi(self.corpus) if self.corpus else None

    def retrieve(self, criterion_id: str, top_k: int = 5, layer: int = 1, avoid_sections: List[str] = None) -> List[str]:
        if not self.bm25:
            return []
            
        avoid_sections = avoid_sections or []
            
        keywords = CRITERIA_KEYWORDS.get(criterion_id, [])
        # Na camada 2, nós expandimos se a busca inicial falhar
        # Para fins práticos aqui, usamos BM25 puro e heurísticas
        
        query_tokens = []
        for kw in keywords:
            query_tokens.extend(kw.lower().split())
            
        scores = self.bm25.get_scores(query_tokens)
        
        # Boost heurístico baseado em roles/headings
        for i, sid in enumerate(self.section_ids):
            h = self.sections_db[sid].get("heading", "").lower()
            # Se critério for "MEASURED" (Q6, Q8) ou STUDY_DESIGN, boost Methods/Results
            if criterion_id in ["Q6_GENETIC_MEASURED", "Q8_INFLAMMATORY_MEASURED", "Q11_STUDY_DESIGN", "Q1_HUMAN"]:
                if any(x in h for x in ["method", "material", "participant", "subject", "result"]):
                    scores[i] *= 1.5
            # Se for link/context, boost Discussion/Results
            if criterion_id in ["Q9_GENE_INFLAMMATION_LINK", "Q10_TEA_CONTEXT"]:
                if any(x in h for x in ["result", "discuss", "conclu"]):
                    scores[i] *= 1.5
            
            if sid in avoid_sections:
                scores[i] = -1.0 # Remove completely
                
        # Rank
        ranked = sorted(zip(self.section_ids, scores), key=lambda x: x[1], reverse=True)
        
        # Filtra os que tem score > 0
        best_sections = [sid for sid, score in ranked if score > 0][:top_k]
        
        # Fallback se BM25 não achou nada bom, e estamos no layer 1
        if not best_sections and layer == 1:
            for sid, sdata in self.sections_db.items():
                if sid not in avoid_sections:
                    h = sdata.get("heading", "").lower()
                    if any(k in h for k in ["method", "result", "abstract"]):
                        best_sections.append(sid)
                        if len(best_sections) >= top_k:
                            break
                            
        return best_sections
