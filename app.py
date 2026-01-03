from flask import Flask, request, Response
import requests

app = Flask(__name__)

TARGET_API = "http://38.49.212.35:4434"

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
def proxy(path):
    try:
        url = f"{TARGET_API}/{path}"
        
        headers = {key: value for key, value in request.headers if key.lower() not in ['host', 'connection']}
        
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            stream=True
        )
        
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_headers]
        
        return Response(
            resp.content,
            resp.status_code,
            response_headers
        )
        
    except requests.exceptions.RequestException as e:
        return Response(f"Proxy Error: {str(e)}", status=502)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)