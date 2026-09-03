import asyncio
import os
import zipfile
import json
from aiohttp import web

# Definir os caminhos base
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
PDFS_DIR = os.path.join(PROJECT_ROOT, 'pdfs')
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, 'scripts')
STATE_DIR = os.path.join(PROJECT_ROOT, 'state')

# Garantir que os diretórios necessários existem
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PDFS_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

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
        os.system("pkill -f run_parallel.py")
        os.system("pkill -f cloak_pdf.py")
        os.system(f"cd {PROJECT_ROOT} && docker compose down >/dev/null 2>&1")
    except Exception:
        pass
        
    if active_process is not None:
        try:
            active_process.terminate()
        except Exception:
            pass
        active_process = None
        process_logs.clear()
        
    return web.FileResponse(os.path.join(FRONTEND_DIR, 'index.html'))

async def handle_upload(request):
    reader = await request.multipart()
    field = await reader.next()
    if field.name == 'file':
        filename = field.filename
        if not filename:
            return web.Response(status=400, text="No filename")
        
        size = 0
        file_path = os.path.join(DATA_DIR, "DOI's.txt")
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
    zip_path = os.path.join(DATA_DIR, 'Artigos_Baixados.zip')
    
    if not os.path.exists(PDFS_DIR) or not os.listdir(PDFS_DIR):
        return web.Response(status=404, text="Nenhum PDF encontrado na pasta pdfs/")
        
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(PDFS_DIR):
            for file in files:
                if file.endswith('.pdf'):
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, arcname=file)
                    
    response = web.FileResponse(zip_path)
    response.headers['Content-Disposition'] = 'attachment; filename="Artigos_Baixados.zip"'
    return response

async def handle_stats(request):
    if not os.path.exists(PDFS_DIR):
        return web.json_response({"success": 0})
    success_count = len([f for f in os.listdir(PDFS_DIR) if f.endswith('.pdf')])
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
        
        start_script = os.path.join(SCRIPTS_DIR, 'start.sh')
        active_process = await asyncio.create_subprocess_exec(
            start_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=PROJECT_ROOT
        )
        
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
        
        deep_search_script = os.path.join(SCRIPTS_DIR, 'start_deep_search.sh')
        with open(deep_search_script, "w") as f:
            f.write("#!/bin/bash\ncd " + PROJECT_ROOT + "\nexec docker compose run --rm paper-fetch python src/deep_search.py\n")
        os.chmod(deep_search_script, 0o755)
            
        active_process = await asyncio.create_subprocess_exec(
            deep_search_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=PROJECT_ROOT
        )
        
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
