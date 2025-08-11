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
        raise VLMError("No images could be prepared for analysis. Check Immich connectivity and asset status.")

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
        vlm_data = response.json()
        
        required_keys = ['title', 'description']
        if all(key in vlm_data and vlm_data[key] for key in required_keys):
            print(f"    - [VLM] Success. Generated Title: '{vlm_data['title']}'")
            return vlm_data
        else:
            raise VLMError(f"Response missed required keys ('title', 'description'). Got: {vlm_data}")
            
    except requests.exceptions.RequestException as e:
        raise VLMError(f"Could not connect to VLM service at {cfg['api_url']}: {e}")
    except json.JSONDecodeError:
        raise VLMError(f"Response was not valid JSON. Response text: {response.text[:200]}...")
