import os 
import requests 
base = 'https://llmapi.zihangwu.com.cn:8443/v1' 
key = os.environ.get('DEEPSEEK_API_KEY', '') 
headers = {'Authorization': 'Bearer ' + key} 
listing = requests.get(base + '/models', headers=headers, timeout=60) 
print('models_status', listing.status_code) 
models = [item['id'] for item in listing.json().get('data', [])] 
for model in models: 
    body = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}], 'temperature': 0, 'max_tokens': 8} 
    try: 
        response = requests.post(base + '/chat/completions', headers=headers, json=body, timeout=90) 
        result = response.json() 
        message = result.get('choices', [{}])[0].get('message', {}).get('content') if response.ok else result 
        print(model, response.status_code, message) 
    except Exception as exc: 
        print(model, 'ERROR') 
