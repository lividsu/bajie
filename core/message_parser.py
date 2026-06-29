import json
import logging

from config import config


def extract_image_keys_from_post(content_str, max_images: int | None = None):
    try:
        content_data = json.loads(content_str)
        image_keys = []
        limit = max_images or config.MAX_IMAGES

        if "content" in content_data:
            for line in content_data["content"]:
                for element in line:
                    if element.get("tag") == "img" and "image_key" in element:
                        image_keys.append(element["image_key"])
                        if len(image_keys) >= limit:
                            return image_keys

        return image_keys
    except Exception as e:
        logging.error(f"Failed to parse post images: {e}")
        return []


def extract_text_from_post(content_str):
    try:
        content_data = json.loads(content_str)
        text_parts = []

        if "title" in content_data and content_data["title"]:
            text_parts.append(content_data["title"])

        if "content" in content_data:
            for line in content_data["content"]:
                for element in line:
                    if element.get("tag") == "text" and "text" in element:
                        text_parts.append(element["text"])

        return " ".join(text_parts).strip()
    except Exception as e:
        logging.error(f"Failed to parse post text: {e}")
        return ""


def extract_text_from_message(message):
    try:
        if message.message_type == "text":
            content_data = json.loads(message.content)
            return content_data.get("text", "").replace("@_user_1 ", "").strip()
        if message.message_type == "post":
            return extract_text_from_post(message.content)
        return ""
    except Exception:
        return ""


def get_quoted_message_info(message, message_api_client):
    if not hasattr(message, "parent_id") or not message.parent_id:
        return None

    parent_message_id = message.parent_id
    print(f"Detected quoted message, parent_id: {parent_message_id}")

    try:
        parent_message = message_api_client.get_message_content(parent_message_id)
        print(f"Fetched quoted message: {parent_message}")
        return parent_message
    except Exception as e:
        logging.error(f"Failed to fetch quoted message: {e}")
        return None
