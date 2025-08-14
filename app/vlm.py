# app/vlm.py
"""
Handles all interaction with the Vision Language Model (VLM).
This module prepares images and context, sends them to the VLM service,
and resiliently parses the response to extract album metadata.
"""
# This future import is key to preventing NameErrors with type hints.
# It makes Python treat type hints as strings, resolving them only when needed.
from __future__ import annotations

import base64
import json
import logging
import re
import time
import requests

# We need to import the type for our type hint.
# This will be used by type checkers and IDEs.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.services import ImmichService

from app.exceptions import VLMConnectionError, VLMResponseError

logger = logging.getLogger(__name__)


def get_vlm_analysis(
    immich_service: "ImmichService",
    sample_asset_ids: list[str],
    date_str: str,
    location_str: str | None,
    config: dict
) -> dict | None:
    """
    Orchestrates the VLM analysis process: downloads images, builds a prompt,
    queries the VLM, and resiliently parses the response.
    
    Args:
        immich_service: The service used to download thumbnails.
        sample_asset_ids: A list of asset IDs to use as a sample for analysis.
        date_str: The formatted date string for context (e.g., "August 2025").
        location_str: The location string for context.
        config: The application's YAML configuration dictionary.
        
    Returns:
        A dictionary with 'title', 'description', and 'cover_photo_index', or None on failure.
    """
    logger.info(f"Starting VLM analysis for an event on {date_str} with {len(sample_asset_ids)} samples.")
    
    encoded_images = []
    for asset_id in sample_asset_ids:
        # Use the ImmichService to get thumbnails, abstracting away the API call.
        image_bytes = immich_service.get_thumbnail_bytes(asset_id)
        if image_bytes:
            encoded_images.append(base64.b64encode(image_bytes).decode('utf-8'))

    if not encoded_images:
        logger.error("Could not prepare any images for VLM analysis. Aborting.")
        raise VLMResponseError("No images could be downloaded or prepared for VLM analysis.")

    cfg_vlm = config.get('vlm', {})
    location_prompt = f"The event took place primarily in '{location_str}'." if location_str else "The event location is unknown."
    
    # Using the modern chat-based prompt structure for better model compliance.
    system_prompt = "You are an automated photo album assistant. Your response MUST be a single, valid JSON object and nothing else. Do not include markdown formatting like ```json or any other conversational text."
    user_prompt = f"""
CONTEXT: Event Date: '{date_str}'. {location_prompt}
JSON STRUCTURE: {{"title": "A short, descriptive event title", "description": "A one-paragraph summary of the event, people, and activities", "cover_photo_index": int}}
"""
    
    payload = {
        "model": cfg_vlm.get('model'),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt, "images": encoded_images}
        ],
        "stream": False,
        "options": {
            "num_ctx": cfg_vlm.get('context_window')
        }
    }
    
    api_url = cfg_vlm.get('api_url', '').replace('/api/generate', '/api/chat')
    if not api_url:
        logger.error("VLM API URL is not configured in config.yaml.")
        raise VLMConnectionError("VLM API URL is missing.")

    for attempt in range(cfg_vlm.get('retry_attempts', 3)):
        try:
            logger.debug(f"VLM attempt {attempt + 1}: POSTing to {api_url}")
            response = requests.post(api_url, json=payload, timeout=cfg_vlm.get('api_timeout_seconds', 300))
            response.raise_for_status()

            response_data = response.json()
            raw_content = response_data.get('message', {}).get('content', '')
            
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if not json_match:
                raise VLMResponseError("No JSON object found in the VLM response.")
                
            vlm_data = json.loads(json_match.group(0))

            # Validate response quality
            if not all(key in vlm_data for key in ['title', 'description']):
                 raise VLMResponseError(f"Response missed required keys. Got: {list(vlm_data.keys())}")
            if not vlm_data.get('title') or not vlm_data.get('description'):
                raise VLMResponseError(f"Response contained empty values. Got: {vlm_data}")
            
            logger.info(f"VLM analysis successful. Generated Title: '{vlm_data['title']}'")
            return vlm_data

        except requests.exceptions.RequestException as e:
            logger.warning(f"VLM connection error on attempt {attempt + 1}: {e}")
            if attempt + 1 == cfg_vlm.get('retry_attempts', 3):
                raise VLMConnectionError("VLM analysis failed due to a network error after multiple retries.") from e
        except (json.JSONDecodeError, VLMResponseError) as e:
            logger.warning(f"VLM response error on attempt {attempt + 1}: {e}")
            if attempt + 1 == cfg_vlm.get('retry_attempts', 3):
                raise VLMResponseError("VLM analysis failed due to an invalid response after multiple retries.") from e
        
        time.sleep(cfg_vlm.get('retry_delay_seconds', 5))

    return None # Should only be reached if retries are exhausted