#!/usr/bin/env python3
"""
LiteralLock Setup & Pre-requisite Verification Script
Checks local dependencies and connection to Mistral AI and Elasticsearch.
"""

import os
import sys
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

def print_header(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)

def check_dependencies():
    print_header("1. Checking Python Dependencies")
    deps = [
        ("streamlit", "Streamlit"),
        ("elasticsearch", "Elasticsearch client"),
        ("mistralai", "Mistral AI SDK"),
        ("pydantic", "Pydantic"),
        ("dotenv", "python-dotenv"),
        ("pytest", "Pytest")
    ]
    all_ok = True
    for module_name, display_name in deps:
        try:
            mod = __import__(module_name)
            ver = getattr(mod, "__version__", "installed")
            print(f"  [OK] {display_name} ({module_name}) -> {ver}")
        except ImportError as e:
            print(f"  [MISSING] {display_name} ({module_name}) -> Error: {e}")
            all_ok = False
    return all_ok

def check_credentials():
    print_header("2. Checking Environment Variables (.env)")
    mistral_key = os.getenv("MISTRAL_API_KEY")
    es_url = os.getenv("ELASTICSEARCH_URL") or os.getenv("ELASTIC_CLOUD_ID")
    es_key = os.getenv("ELASTICSEARCH_API_KEY")
    backend = os.getenv("SEMANTIC_BACKEND", "semantic_text")
    inference_id = os.getenv("ELASTIC_INFERENCE_ID", "literallock-embeddings")

    print(f"  MISTRAL_API_KEY:        {'SET (length ' + str(len(mistral_key)) + ')' if mistral_key else 'NOT SET (Required)'}")
    print(f"  ELASTICSEARCH_URL:      {'SET' if es_url else 'NOT SET (Required)'}")
    print(f"  ELASTICSEARCH_API_KEY:  {'SET' if es_key else 'NOT SET (Required)'}")
    print(f"  SEMANTIC_BACKEND:       {backend}")
    print(f"  ELASTIC_INFERENCE_ID:   {inference_id}")

    if not mistral_key or not es_url or not es_key:
        print("\n  [!] To proceed with live connectivity, create a .env file based on .env.example:")
        print("      cp .env.example .env")
        print("      # Edit .env with your event Mistral and Elasticsearch credentials.")
        return False
    return True

def check_mistral_connection():
    print_header("3. Verifying Mistral AI Connection")
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("  [SKIPPED] Mistral API key is not configured.")
        return False
    
    try:
        try:
            from mistralai import Mistral
        except ImportError:
            from mistralai.client import Mistral

        model = os.getenv("MISTRAL_CHAT_MODEL", "mistral-large-latest")
        print(f"  Testing chat completion with model '{model}'...")
        client = Mistral(api_key=api_key)
        resp = client.chat.complete(
            model=model,
            messages=[{"role": "user", "content": "Respond with 'LITERAL_LOCK_READY' if you can read this."}],
            max_tokens=15,
        )
        reply = resp.choices[0].message.content.strip()
        print(f"  [OK] Mistral responded: {reply}")
        return True
    except Exception as e:
        print(f"  [ERROR] Mistral check failed: {e}")
        return False

def check_elasticsearch_connection():
    print_header("4. Verifying Elasticsearch Connection")
    es_url = os.getenv("ELASTICSEARCH_URL")
    cloud_id = os.getenv("ELASTIC_CLOUD_ID")
    api_key = os.getenv("ELASTICSEARCH_API_KEY")

    if not api_key or (not es_url and not cloud_id):
        print("  [SKIPPED] Elasticsearch credentials are not configured.")
        return False

    try:
        from elasticsearch import Elasticsearch
        if cloud_id:
            es = Elasticsearch(cloud_id=cloud_id, api_key=api_key)
        else:
            es = Elasticsearch(es_url, api_key=api_key)

        info = es.info()
        cluster_name = info.get("cluster_name", "unknown")
        version = info.get("version", {}).get("number", "unknown")
        print(f"  [OK] Connected to Elasticsearch cluster '{cluster_name}', version {version}")
        return True
    except Exception as e:
        print(f"  [ERROR] Elasticsearch connection failed: {e}")
        return False

def main():
    print("Running LiteralLock pre-flight verification...")
    deps_ok = check_dependencies()
    creds_ok = check_credentials()
    mistral_ok = False
    es_ok = False
    if creds_ok:
        mistral_ok = check_mistral_connection()
        es_ok = check_elasticsearch_connection()
    print_header("Summary")
    if deps_ok and mistral_ok and es_ok:
        print("  All local pre-requisites and live connections are ready!")
    elif deps_ok and (not mistral_ok or not es_ok):
        print("  Dependencies are installed, but one or both service connections failed.")
        print("  Please check the keys and URLs in your .env file.")
    else:
        print("  Some dependencies are missing. Run: uv pip install -r requirements.txt --python .venv")

if __name__ == "__main__":
    main()
