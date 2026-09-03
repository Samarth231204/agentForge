import os, sys
from google import genai
os.environ["GEMINI_API_KEY"] = "AQ.Ab8RN6Lwilq2buX_kDTf518TGDNSwiYfnAB_bUBrqRDu_T__Eg"
client = genai.Client()
try:
    response = client.models.generate_content(
        model='gemini-pro-latest',
        contents='hello'
    )
    print("SUCCESS:", response.text)
except Exception as e:
    print("ERROR:", e)
