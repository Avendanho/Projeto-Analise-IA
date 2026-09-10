import subprocess
import shutil
import platform
import os
import psutil

class HardwareInfo:
    def __init__(self):
        self.ram_total_gb = self._get_ram_gb()
        self.vram_total_gb = self._get_vram_gb()
        self.has_gpu = self.vram_total_gb > 0
        self.cpu_cores = os.cpu_count() or 4
        
    def _get_ram_gb(self) -> float:
        mem = psutil.virtual_memory()
        return mem.total / (1024**3)
        
    def _get_vram_gb(self) -> float:
        try:
            if shutil.which("nvidia-smi"):
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, check=True
                )
                total_mb = sum(int(x.strip()) for x in result.stdout.strip().split('\n') if x.strip().isdigit())
                return total_mb / 1024.0
        except Exception:
            pass
        return 0.0

def check_model_compatibility(model_name: str, min_ram_gb: float = 8.0, min_vram_gb: float = 0.0) -> tuple[bool, str]:
    hw = HardwareInfo()
    warnings = []
    
    if min_vram_gb > 0 and hw.vram_total_gb < min_vram_gb:
        warnings.append(f"VRAM insuficiente para o modelo (requer {min_vram_gb}GB, detectado {hw.vram_total_gb:.1f}GB). O modelo pode rodar via CPU, mas será lento e consumirá RAM principal.")
        
    # Se não há VRAM, o modelo inteiro vai pra RAM
    required_ram = min_ram_gb
    if hw.vram_total_gb < min_vram_gb:
        required_ram += (min_vram_gb - hw.vram_total_gb)
        
    mem_available = psutil.virtual_memory().available / (1024**3)
    if mem_available < required_ram:
        return False, f"Memória RAM disponível insuficiente ({mem_available:.1f}GB). Necessário {required_ram}GB. O sistema entraria em swap extremo."
        
    msg = "Hardware compatível." if not warnings else " ".join(warnings)
    return True, msg

def get_hardware_profile():
    return HardwareInfo()
