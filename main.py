import requests
import config
import queries
import json

url = config.API_URL

headers = {
    "Authorization": f"Bearer {config.API_TOKEN}",
    "Content-Type": "application/json"
}

payload = queries.companyBySlug(config.COMPANY_SLUG)

response = requests.request('POST', url, headers=headers, data = payload)
print(response.json())
print(json.dumps(json.loads(response.text), indent=4))