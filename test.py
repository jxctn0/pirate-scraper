import requests

# Test the mirror that gave 0 results
url = "https://pirateproxy.space/description.php?id=77824169"
headers = {'User-Agent': 'Mozilla/5.0'}

print(f"[*] Fetching: {url}")
response = requests.get(url, headers=headers)

print(f"[*] Status Code: {response.status_code}")
print("\n--- PAGE CONTENT START ---")
print(response.text[:500]) # Prints the first 500 characters
print("--- PAGE CONTENT END ---")