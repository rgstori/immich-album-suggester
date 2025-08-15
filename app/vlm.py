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
from app.models import VLMAnalysis

logger = logging.getLogger(__name__)


def get_vlm_analysis(
    immich_service: "ImmichService",
    sample_asset_ids: list[str],
    date_str: str,
    location_str: str | None,
    config: dict
) -> VLMAnalysis:
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
        A VLMAnalysis DTO with results or error information.
    """
    start_time = time.time()
    logger.info(f"Starting VLM analysis for an event on {date_str} with {len(sample_asset_ids)} samples.")
    
    try:
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
    
    # Validate total request size to prevent VLM context window overflow
    max_context_size = cfg_vlm.get('context_window', 32768)  # Default Ollama context
    _validate_vlm_request_size(encoded_images, system_prompt + user_prompt, max_context_size)
    
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
            processing_time = time.time() - start_time
            
            # Extract cover photo index if provided
            cover_asset_id = None
            if 'cover_photo_index' in vlm_data and isinstance(vlm_data['cover_photo_index'], int):
                cover_index = vlm_data['cover_photo_index']
                if 0 <= cover_index < len(sample_asset_ids):
                    cover_asset_id = sample_asset_ids[cover_index]
                    
            return VLMAnalysis(
                vlm_title=vlm_data.get('title'),
                vlm_description=vlm_data.get('description'),
                cover_asset_id=cover_asset_id,
                confidence_score=vlm_data.get('confidence_score'),
                processing_time_seconds=processing_time
            )

        except requests.exceptions.RequestException as e:
            logger.warning(f"VLM connection error on attempt {attempt + 1}: {e}")
            if attempt + 1 == cfg_vlm.get('retry_attempts', 3):
                error_msg = f"VLM analysis failed due to network error after {cfg_vlm.get('retry_attempts', 3)} attempts"
                logger.error(error_msg)
                return VLMAnalysis(error_message=error_msg, processing_time_seconds=time.time() - start_time)
        except (json.JSONDecodeError, VLMResponseError) as e:
            logger.warning(f"VLM response error on attempt {attempt + 1}: {e}")
            if attempt + 1 == cfg_vlm.get('retry_attempts', 3):
                error_msg = f"VLM analysis failed due to invalid response after {cfg_vlm.get('retry_attempts', 3)} attempts: {e}"
                logger.error(error_msg)
                return VLMAnalysis(error_message=error_msg, processing_time_seconds=time.time() - start_time)
        
            time.sleep(cfg_vlm.get('retry_delay_seconds', 5))

        # If we reach here, all retries are exhausted without success
        error_msg = f"VLM analysis failed after {cfg_vlm.get('retry_attempts', 3)} attempts"
        logger.error(error_msg)
        return VLMAnalysis(error_message=error_msg, processing_time_seconds=time.time() - start_time)
        
    except Exception as e:
        # Catch any other unexpected errors (e.g., image download failures)
        error_msg = f"VLM analysis failed due to unexpected error: {e}"
        logger.error(error_msg, exc_info=True)
        return VLMAnalysis(error_message=error_msg, processing_time_seconds=time.time() - start_time)


def _validate_vlm_request_size(encoded_images: list[str], prompt_text: str, max_context_size: int) -> None:
    """
    Validates that the VLM request size doesn't exceed context window limits.
    
    Args:
        encoded_images: List of base64-encoded image strings
        prompt_text: Combined system and user prompt text
        max_context_size: Maximum context window size in tokens
        
    Raises:
        VLMResponseError: If request size exceeds limits
    """
    # Rough estimation: 1 token ≈ 4 characters for text, images vary greatly
    # Base64 encoding increases size by ~33%, plus image processing overhead
    
    text_tokens = len(prompt_text) // 4  # Rough text token estimation
    
    # Estimate image tokens (very rough - actual depends on model and image size)
    # Typical vision models use 100-1000 tokens per image depending on resolution
    total_image_size = sum(len(img) for img in encoded_images)
    token_estimate = config.get('vlm', {}).get('image_token_estimate', 500)
    estimated_image_tokens = len(encoded_images) * token_estimate  # Conservative estimate
    
    total_estimated_tokens = text_tokens + estimated_image_tokens
    
    logger.debug(f"VLM request size validation: {len(encoded_images)} images, "
                f"{len(prompt_text)} chars text, ~{total_estimated_tokens} tokens estimated")
    
    if total_estimated_tokens > max_context_size:
        raise VLMResponseError(
            f"VLM request too large: ~{total_estimated_tokens} tokens "
            f"exceeds context window of {max_context_size} tokens. "
            f"Reduce image count or use smaller images."
        )
    
    # Also check for unreasonably large individual images
    max_image_size = config.get('vlm', {}).get('max_image_size_bytes', 2 * 1024 * 1024)  # Default 2MB
    for i, img in enumerate(encoded_images):
        if len(img) > max_image_size:
            raise VLMResponseError(
                f"Image {i} is too large ({len(img)} chars base64). "
                f"Maximum individual image size is {max_image_size} chars."
            )