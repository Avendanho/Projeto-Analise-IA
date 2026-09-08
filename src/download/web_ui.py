import asyncio
import os
import zipfile
import json
from aiohttp import web

import sys
from pathlib import Path

_ANALISEIA_DIR = Path(__file__).resolve().parent.parent / "analiseia"
if str(_ANALISEIA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_ANALISEIA_DIR.parent))

from analiseia.platform.process import ProcessRegistry
from analiseia.platform.docker import DockerService
# Definir os caminhos base
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / 'frontend'
DATA_DIR = PROJECT_ROOT / 'data'
PDFS_DIR = PROJECT_ROOT / 'pdfs'
SCRIPTS_DIR = PROJECT_ROOT / 'scripts'
STATE_DIR = PROJECT_ROOT / 'state'

# Guarantee directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
PDFS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

active_process = None
process_logs = []
connected_clients = set()

async def background_process_reader():
    global active_process
    while True:
        data = await active_process.stdout.readline()
        if data:
            text = data.decode('utf-8', errors='replace')
            process_logs.append(text)
            if len(process_logs) > 5000:
                process_logs.pop(0)
            
            # Broadcast to all connected clients
            for ws in list(connected_clients):
                try:
                    await ws.send_str(text)
                except Exception:
                    connected_clients.remove(ws)
        else:
            break
    await active_process.wait()

async def handle_index(request):
    global active_process, process_logs
    try:
        ProcessRegistry.terminate_all(label_filter="run_parallel")
        ProcessRegistry.terminate_all(label_filter="cloak_pdf")
        try:
            docker = DockerService()
            docker.compose_down(PROJECT_ROOT)
        except Exception:
            pass
    except Exception:
        pass
        
    if active_process is not None:
        try:
            active_process.terminate()
        except Exception:
            pass
        active_process = None
        process_logs.clear()
        
    return web.FileResponse(str(FRONTEND_DIR / 'index.html'))

async def handle_upload(request):
    reader = await request.multipart()
    field = await reader.next()
    if field.name == 'file':
        filename = field.filename
        if not filename:
            return web.Response(status=400, text="No filename")
        
        size = 0
        file_path = str(DATA_DIR / "DOI's.txt")
        with open(file_path, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
        return web.Response(text=f"Uploaded {size} bytes")
    return web.Response(status=400, text="No file field")

async def handle_download_zip(request):
    zip_path = str(DATA_DIR / 'Artigos_Baixados.zip')
    
    if not PDFS_DIR.exists() or not list(PDFS_DIR.iterdir()):
        return web.Response(status=404, text="Nenhum PDF encontrado na pasta pdfs/")
        
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(PDFS_DIR):
            for file in files:
                if file.endswith('.pdf'):
                    file_path = str(Path(root) / file)
                    zipf.write(file_path, arcname=file)
                    
    response = web.FileResponse(zip_path)
    response.headers['Content-Disposition'] = 'attachment; filename="Artigos_Baixados.zip"'
    return response

async def handle_stats(request):
    if not PDFS_DIR.exists():
        return web.json_response({"success": 0})
    success_count = len([f for f in list(PDFS_DIR.iterdir()) if f.name.endswith('.pdf')])
    return web.json_response({"success": success_count})

async def websocket_handler(request):
    global active_process, process_logs
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    if active_process is not None and active_process.returncode is None:
        for log in process_logs:
            await ws.send_str(log)
        connected_clients.add(ws)
        try:
            async for msg in ws:
                pass
        finally:
            if ws in connected_clients:
                connected_clients.remove(ws)
        return ws
        
    try:
        process_logs.clear()
        connected_clients.add(ws)
        
        run_parallel_script = PROJECT_ROOT / 'src' / 'download' / 'run_parallel.py'
        active_process = await asyncio.create_subprocess_exec(
            sys.executable, str(run_parallel_script),
            '--file', str(DATA_DIR / "DOI's.txt"),
            '--out', str(PDFS_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT / 'src' / 'download')
        )
        ProcessRegistry.register(active_process, label='run_parallel')
        
        asyncio.create_task(background_process_reader())
        
        try:
            async for msg in ws:
                pass
        finally:
            if ws in connected_clients:
                connected_clients.remove(ws)
            
    except Exception as e:
        await ws.send_str(f"\nErro ao executar processo: {e}\n")
        
    return ws

async def handle_start_deep_search(request):
    return web.Response(text="OK")

async def websocket_deep_search_handler(request):
    global active_process, process_logs
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    try:
        process_logs.clear()
        connected_clients.add(ws)
        
        deep_search_script = PROJECT_ROOT / 'src' / 'download' / 'deep_search.py'
        active_process = await asyncio.create_subprocess_exec(
            sys.executable, str(deep_search_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT / 'src' / 'download')
        )
        ProcessRegistry.register(active_process, label='deep_search')
        
        asyncio.create_task(background_process_reader())
        
        try:
            async for msg in ws:
                pass
        finally:
            if ws in connected_clients:
                connected_clients.remove(ws)
            
    except Exception as e:
        await ws.send_str(f"\nErro ao executar busca profunda: {e}\n")
        
    return ws

app = web.Application()

app.router.add_post('/api/upload', handle_upload)
app.router.add_get('/api/download-zip', handle_download_zip)
app.router.add_get('/api/stats', handle_stats)
app.router.add_get('/api/ws', websocket_handler)
app.router.add_get('/api/start-deep-search', handle_start_deep_search)
app.router.add_get('/api/ws-deep-search', websocket_deep_search_handler)

app.router.add_get('/', handle_index)
app.router.add_static('/static', FRONTEND_DIR)

if __name__ == '__main__':
    print("Iniciando interface web em http://0.0.0.0:8080")
    web.run_app(app, host='0.0.0.0', port=8080)
