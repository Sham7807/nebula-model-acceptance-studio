"""A deterministic local provider for desktop QA; never forwards requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import base64,io,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PNG=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")
WAV=io.BytesIO()
with wave.open(WAV,"wb") as audio:
    audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(b"\0"*48000)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200):
        body=json.dumps(data).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Headers','*');self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS');self.end_headers()
    def binary(self,body,mime):
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/fixture.mp4': return self.binary((ROOT/'qa/fixture.mp4').read_bytes(),'video/mp4')
        if self.path.endswith('/models'):self.send({'object':'list','data':[{'id':'fixture-model','object':'model'},{'id':'fixture-second','object':'model'}]})
        else:self.send({'error':{'message':'Mock route not implemented'}},404)
    def do_POST(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
        payload=json.loads(raw or b'{}') if 'application/json' in self.headers.get('Content-Type','') else {}
        if self.path.endswith(('/images/generations','/images/edits')):return self.send({'data':[{'b64_json':base64.b64encode(PNG).decode()}]})
        if self.path.endswith('/audio/speech'):return self.binary(WAV.getvalue(),'audio/wav')
        if self.path.endswith('/videos'):return self.send({'id':'fixture-video','status':'completed','video_url':'http://127.0.0.1:18991/fixture.mp4'})
        if self.path.endswith('/chat/completions'):
            answer={'id':'fixture-response','object':'chat.completion','model':payload.get('model'),'choices':[{'index':0,'message':{'role':'assistant','content':'DESKTOP_FIXTURE_OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':8,'total_tokens':18}}
            self.send(answer)
        else:self.send({'error':{'message':'Mock route not implemented'}},404)

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',18991),Handler)
    print('Offline mock channel ready on loopback :18991',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
