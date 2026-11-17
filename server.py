#!/usr/bin/env python3
"""Generador de facturas: sirve la página; el PDF se arma en el navegador (jsPDF).
El servidor SOLO guarda los números de factura usados (datos/numeros.txt). Nunca recibe clientes, artículos ni montos.

Variables de entorno (todas opcionales):
  PORT=8080      puerto            HOST=127.0.0.1   en qué dirección escucha (0.0.0.0 para toda la red)
  CLAVE=...      contraseña de acceso (si no la pones, la página queda abierta: úsalo solo en tu computadora o red privada)
  PREFIJO=INV    prefijo del número de factura (INV20261116, INV20261116-2…)
  DATA=./datos   carpeta donde se guardan numeros.txt y el secreto de sesión"""
import os, re, json, hmac, hashlib, time, base64, secrets, threading
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT=os.path.dirname(os.path.abspath(__file__))
DATA=os.environ.get('DATA',os.path.join(ROOT,'datos')); os.makedirs(DATA,exist_ok=True)
NUMS=os.path.join(DATA,'numeros.txt'); SEC=os.path.join(DATA,'secreto')
CLAVE=os.environ.get('CLAVE',''); PREFIJO=re.sub(r'[^A-Za-z0-9]','',os.environ.get('PREFIJO','INV'))[:12] or 'INV'
DURACION=30*86400; COOKIE='sesion'
PUBLICOS={'/login.html','/icon.svg','/logo.svg','/icon-180.png'}; PRIVADOS={'/index.html','/vendor/jspdf.umd.min.js'}
fails={}; lock=threading.Lock(); OK_NUM=re.compile(r'^[A-Za-z0-9._-]{1,40}$')
if not os.path.exists(SEC):
    fd=os.open(SEC,os.O_WRONLY|os.O_CREAT,0o600); os.write(fd,secrets.token_hex(32).encode()); os.close(fd)
SECRETO=open(SEC,'rb').read().strip()

def usados():
    try: return {l.strip() for l in open(NUMS) if l.strip()}
    except FileNotFoundError: return set()
def siguiente():
    base=PREFIJO+datetime.now().strftime('%Y%m%d'); u=usados(); n=base; k=2
    while n in u: n=f'{base}-{k}'; k+=1
    return n
def registrar(n):
    with lock:
        if n in usados(): return False
        with open(NUMS,'a') as f: f.write(n+'\n')
        return True
def firmar():
    msg=str(int(time.time())+DURACION)
    return msg+'.'+hmac.new(SECRETO,msg.encode(),hashlib.sha256).hexdigest()
def valida(tok):
    try:
        exp,sig=tok.split('.'); return int(exp)>time.time() and hmac.compare_digest(sig,hmac.new(SECRETO,exp.encode(),hashlib.sha256).hexdigest())
    except Exception: return False

class H(SimpleHTTPRequestHandler):
    def __init__(s,*a,**k): super().__init__(*a,directory=ROOT,**k)
    def log_message(s,*a): pass
    def end_headers(s):
        s.send_header('Cache-Control','no-store'); s.send_header('X-Content-Type-Options','nosniff'); super().end_headers()
    def reply(s,code,obj,cookie=None):
        b=json.dumps(obj).encode(); s.send_response(code); s.send_header('Content-Type','application/json'); s.send_header('Content-Length',str(len(b)))
        if cookie: s.send_header('Set-Cookie',cookie)
        s.end_headers(); s.wfile.write(b)
    def autorizado(s):
        if not CLAVE: return True
        for p in s.headers.get('Cookie','').split(';'):
            k,_,v=p.strip().partition('=')
            if k==COOKIE: return valida(v)
        return False
    def cuerpo(s,limite=4096):
        n=int(s.headers.get('Content-Length','0')); return s.rfile.read(n) if 0<n<=limite else None
    def do_GET(s):
        p=s.path.split('?')[0]
        if '..' in p or p.startswith('/.'): return s.reply(404,{'error':'no'})
        if p=='/api/siguiente':
            return s.reply(200,{'numero':siguiente()}) if s.autorizado() else s.reply(401,{'error':'Inicia sesión'})
        if p in ('/','/index.html'):
            s.path='/index.html' if s.autorizado() else '/login.html'; return super().do_GET()
        if p in PUBLICOS or (p in PRIVADOS and s.autorizado()): s.path=p; return super().do_GET()
        s.reply(404 if p not in PRIVADOS else 401,{'error':'no'})
    def do_POST(s):
        p=s.path.split('?')[0]
        if p=='/api/login': return s.login()
        if p=='/api/salir': return s.reply(200,{'ok':True},f'{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
        if not s.autorizado(): return s.reply(401,{'error':'Inicia sesión'})
        if p=='/api/numero':
            try: n=str(json.loads(s.cuerpo() or b'{}').get('numero','')).strip()
            except Exception: return s.reply(400,{'error':'Datos inválidos'})
            if not OK_NUM.match(n): return s.reply(400,{'error':'Número inválido (letras, números, guion)'})
            return s.reply(200,{'numero':n,'nuevo':registrar(n)})
        s.reply(404,{'error':'no'})
    def login(s):
        ip=s.headers.get('X-Forwarded-For',s.client_address[0]).split(',')[0].strip() if s.client_address[0]=='127.0.0.1' else s.client_address[0]
        if len([t for t in fails.get(ip,[]) if t>time.time()-600])>=8: return s.reply(429,{'error':'Demasiados intentos. Espera 10 minutos.'})
        try: clave=str(json.loads(s.cuerpo() or b'{}').get('clave',''))
        except Exception: return s.reply(400,{'error':'Datos inválidos'})
        if not CLAVE or not hmac.compare_digest(clave.encode(),CLAVE.encode()):
            fails[ip]=[t for t in fails.get(ip,[]) if t>time.time()-600]+[time.time()]
            return s.reply(401,{'error':'Contraseña incorrecta'})
        fails.pop(ip,None)
        s.reply(200,{'ok':True},f'{COOKIE}={firmar()}; Path=/; Max-Age={DURACION}; HttpOnly; SameSite=Lax')
if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8080'))
    print(f'Generador de facturas en http://{host}:{port}'+('' if CLAVE else '  (SIN contraseña: define CLAVE=... si lo expones a internet)'))
    ThreadingHTTPServer((host,port),H).serve_forever()
