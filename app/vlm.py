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
from . import immich_api # Note the relative import

def get_vlm_analysis(
    api_client: immich_api.immich_python_sdk.ApiClient,
    sample_asset_ids: list,
    date_str: str,
    location_str: str | None,
    config: dict
) -> dict | None:
    """
    Orchestrates the VLM analysis process: downloads images, builds a prompt,
    queries the VLM, and parses the response.
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
        print("    - [VLM-ERROR] No images could be prepared for analysis.", file=sys.stderr)
        return None

    cfg = config['vlm']
    # This highly-structured prompt is crucial for getting reliable JSON output.
    location_prompt = f"The event took place primarily in '{location_str}'." if location_str else "The event location is unknown."
    prompt = cfg['prompt'].format(
        date_str=date_str,
        location_prompt=location_prompt
    )

    payload = {
        "model": cfg['model'],
        "format": "json",
        "prompt": prompt,
        "stream": False,
        "images": encoded_images,
        "options": {"num_ctx": cfg['context_window']}
    }
    
    try:
        response = requests.post(cfg['api_url'], json=payload, timeout=cfg['api_timeout_seconds'])
        response.raise_for_status()
        
        ### --- UPDATED SECTION (Flexible VLM Response Handling) --- ###
        
        # Based on Design Decision 2B (Flexible): Check for core keys only.
        # We consider the analysis a success if we get at least a title and description.
        required_keys = ['title', 'description']
        if all(key in vlm_data and vlm_data[key] for key in required_keys):
            print(f"    - [VLM] Success. Generated Title: '{vlm_data['title']}'")
            return vlm_data
        else:
            print(f"    - [VLM-WARN] VLM response was valid JSON but missed core keys ('title', 'description'). Response: {vlm_data}", file=sys.stderr)
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"    - [VLM-ERROR] Could not connect to VLM service: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError:
        print(f"    - [VLM-ERROR] VLM response was not valid JSON. Response: {response.text}", file=sys.stderr)
        return None