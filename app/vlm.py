# app/vlm.py
"""
Handles all interaction with the Vision Language Model (VLM).
This module prepares images and context, sends them to the VLM service,
and resiliently parses the response to extract album metadata.
"""
import base64
import json
import requests
import sys
import time # <-- Import the time module
import traceback
from app import immich_api
import re

class VLMError(Exception):
    """Custom exception for VLM-related failures."""
    pass

def get_vlm_analysis(
    api_client: immich_api.immich_python_sdk.ApiClient,
    sample_asset_ids: list,
    date_str: str,
    location_str: str | None,
    config: dict
) -> dict | None:
    """
    Orchestrates the VLM analysis process: downloads images, builds a prompt,
    queries the VLM, and resiliently parses the nested JSON response.
    """
    print(f"    - [VLM] Analyzing {len(sample_asset_ids)} sample images...")
    print(f"    - [VLM] Context -> Date: {date_str}, Location: {location_str or 'N/A'}")
    
    encoded_images = []
    for asset_id in sample_asset_ids:
        # Use the robust image downloader from the immich_api module.
        image_bytes = immich_api.download_and_convert_image(api_client, asset_id, config)
        if image_bytes:
            encoded_images.append(base64.b64encode(image_bytes).decode('utf-8'))

    if not encoded_images:
        raise VLMError("No images could be prepared for analysis. Check Immich connectivity and asset status.")

    cfg = config['vlm']
    # This highly-structured prompt is crucial for getting reliable JSON output.

    location_prompt = f"The event took place primarily in '{location_str}'." if location_str else "The event location is unknown."
    
    # --- MODIFICATION: Use native chat template for Qwen/Instruct models ---
    # This is a more robust way to provide instructions and context.
    
    # The system prompt sets the persona and overall rules.
    system_prompt = """You are an automated photo album assistant. Your response MUST be a single, valid JSON object and nothing else. Do not include markdown formatting like ```json or any other conversational text."""

    # The user prompt provides the specific data and the required JSON structure.
    user_prompt = f"""
CONTEXT: Event Date: '{date_str}'. {location_prompt}
JSON STRUCTURE: {{"title": "A short, descriptive event title", "description": "A one-paragraph summary of the event, people, and activities", "highlights": [int], "cover_photo_index": int}}
"""
    
    # The Ollama API supports a `messages` array for this purpose.
    # Note: When using 'messages', we should use the '/api/chat' endpoint.
    payload = {
        "model": cfg['model'],
        "messages": [
            { "role": "system", "content": system_prompt },
            { 
              "role": "user", 
              "content": user_prompt,
              "images": encoded_images # Attach images to the user message
            }
        ],
        "stream": False,
    }
    
    # Adjust the API endpoint to the standard for chat completions
    api_url = cfg['api_url'].replace('/api/generate', '/api/chat')
    
    for attempt in range(cfg.get('retry_attempts', 1)):
        try:
            # Use the new api_url
            response = requests.post(api_url, json=payload, timeout=cfg['api_timeout_seconds'])
            response.raise_for_status()

            # The response structure for /api/chat is different.
            # The JSON content is in response['message']['content'].
            response_data = response.json()
            raw_response_field = response_data.get('message', {}).get('content', '')
            
            print(f"    - [VLM-DEBUG] Raw response field: {raw_response_field}", flush=True)
            
            # Our existing regex extractor is still perfect for this!
            json_match = re.search(r'\{.*\}', raw_response_field, re.DOTALL)
            
            if not json_match:
                raise VLMError("No JSON object found in the VLM response.")
                
            vlm_data_string = json_match.group(0)
            vlm_data = json.loads(vlm_data_string)

            # Old check was just for key existence.
            required_keys = ['title', 'description']
            if not all(key in vlm_data for key in required_keys):
                 raise VLMError(f"Response missed required keys. Got: {list(vlm_data.keys())}")

            # NEW: Add a check for lazy placeholder values.
            if vlm_data.get('title', '').lower() == 'string' or vlm_data.get('description', '').lower() == 'string':
                raise VLMError(f"VLM returned a lazy placeholder response. Got: {vlm_data}")
            
            # Final check to ensure values are not empty.
            if not all(vlm_data.get(key) for key in required_keys):
                raise VLMError(f"Response contained empty but required values. Got: {vlm_data}")
            # --- MODIFICATION END ---
            
            print(f"    - [VLM] Success. Generated Title: '{vlm_data['title']}'", flush=True)
            return vlm_data # Success, exit the function

        except (requests.exceptions.RequestException, json.JSONDecodeError, VLMError) as e:
            print(f"    - [VLM-WARN] Attempt {attempt + 1}/{cfg.get('retry_attempts', 1)} failed: {e}", file=sys.stderr, flush=True)
            if attempt + 1 == cfg.get('retry_attempts', 1):
                # If this was the last attempt, re-raise the exception to be caught by main.py
                raise VLMError(f"VLM analysis failed after {cfg.get('retry_attempts', 1)} attempts. Last error: {e}") from e
            # Wait before retrying
            time.sleep(cfg.get('retry_delay_seconds', 5))
        # If we get here, it means a retryable error occurred, and the loop will continue.

    return None # Should not be reached, but as a fallback.