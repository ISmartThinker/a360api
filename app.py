from flask import Flask, request, Response, stream_with_context, jsonify
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import gzip
import time
from functools import wraps

app = Flask(__name__)

TARGET_API = "http://72.61.243.207:4434"

class ConnectionPool:
    def __init__(self):
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
        )
        adapter = HTTPAdapter(
            pool_connections=100,
            pool_maxsize=200,
            max_retries=retry_strategy,
            pool_block=False
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            'Connection': 'keep-alive',
            'Keep-Alive': 'timeout=120, max=1000'
        })
    
    def request(self, method, url, **kwargs):
        return self.session.request(method, url, **kwargs)

pool = ConnectionPool()

def should_compress(content_type, size):
    compressible_types = [
        'text/', 'application/json', 'application/javascript',
        'application/xml', 'application/xhtml+xml'
    ]
    return any(content_type.startswith(ct) for ct in compressible_types) and size > 1024

def compress_response(data):
    return gzip.compress(data, compresslevel=6)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
def proxy(path):
    start_time = time.time()
    
    try:
        url = f"{TARGET_API}/{path}"
        
        headers = {}
        for key, value in request.headers:
            if key.lower() not in ['host', 'connection', 'content-length', 'transfer-encoding']:
                headers[key] = value
        
        headers['X-Real-IP'] = request.remote_addr
        headers['X-Forwarded-For'] = request.headers.get('X-Forwarded-For', request.remote_addr)
        headers['X-Forwarded-Proto'] = request.scheme
        headers['X-Forwarded-Host'] = request.host
        
        resp = pool.request(
            method=request.method,
            url=url,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            stream=True,
            timeout=(10, 300)
        )
        
        excluded_headers = [
            'content-encoding', 'content-length', 'transfer-encoding', 
            'connection', 'keep-alive', 'proxy-authenticate', 
            'proxy-authorization', 'te', 'trailers', 'upgrade'
        ]
        
        response_headers = {}
        for name, value in resp.raw.headers.items():
            name_lower = name.lower()
            if name_lower not in excluded_headers:
                if name_lower == 'location':
                    value = value.replace(TARGET_API, request.host_url.rstrip('/'))
                    value = value.replace('http://72.61.243.207:4434', request.host_url.rstrip('/'))
                response_headers[name] = value
        
        content_type = resp.headers.get('Content-Type', '')
        is_streaming = 'stream' in content_type.lower() or resp.status_code == 206
        
        if is_streaming or request.method == 'HEAD':
            @stream_with_context
            def generate():
                try:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                except Exception as e:
                    app.logger.error(f"Stream error: {e}")
                    raise
            
            return Response(
                generate() if request.method != 'HEAD' else b'',
                resp.status_code,
                response_headers,
                direct_passthrough=True
            )
        else:
            content = resp.content
            
            accept_encoding = request.headers.get('Accept-Encoding', '').lower()
            if 'gzip' in accept_encoding and should_compress(content_type, len(content)):
                content = compress_response(content)
                response_headers['Content-Encoding'] = 'gzip'
                response_headers['Vary'] = 'Accept-Encoding'
            
            response = Response(
                content,
                resp.status_code,
                response_headers
            )
            
            response.headers['X-Proxy-Time'] = f"{(time.time() - start_time) * 1000:.2f}ms"
            response.headers['X-Cache-Status'] = 'MISS'
            
            return response
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "Gateway Timeout", "message": "Backend server timeout"}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Bad Gateway", "message": "Cannot connect to backend"}), 502
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Proxy Error", "message": str(e)}), 502
    except Exception as e:
        app.logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal Server Error", "message": "Proxy error occurred"}), 500

@app.route('/health')
def health_check():
    try:
        resp = pool.request('GET', f"{TARGET_API}/", timeout=5)
        backend_status = "healthy" if resp.status_code < 500 else "unhealthy"
    except:
        backend_status = "unreachable"
    
    return jsonify({
        "proxy": "healthy",
        "backend": backend_status,
        "target": TARGET_API
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not Found", "message": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal Server Error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
