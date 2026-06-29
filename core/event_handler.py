import json
import logging
import threading

from core.bot import (
    download_images,
    handle_text_only,
    handle_with_files,
    handle_with_images,
    is_self_triggered_message,
    perform_reflection_and_retry,
    send_response,
)
from core.dependencies import event_manager
from core.message_parser import (
    extract_image_keys_from_post,
    extract_text_from_message,
    extract_text_from_post,
    get_quoted_message_info,
)
from llm.humanized_responses import designer


def _parse_file_item(content_str):
    try:
        content_data = json.loads(content_str)
        file_key = content_data.get("file_key")
        file_name = content_data.get("file_name")
        if file_key:
            return {"file_key": file_key, "file_name": file_name}
    except Exception as e:
        logging.error(f"Failed to parse file message: {e}")
    return None


def _run_with_task_reaction(tenant, message_id, func):
    feishu = tenant.config.feishu
    reaction_id = tenant.message_api_client.add_reaction(
        message_id,
        feishu.processing_emoji or "OnIt",
    )
    failed = False
    try:
        return func()
    except Exception:
        failed = True
        raise
    finally:
        tenant.message_api_client.finish_task_reaction(
            message_id,
            processing_reaction_id=reaction_id,
            done_emoji=feishu.done_emoji,
            failed_emoji=feishu.failed_emoji,
            failed=failed,
        )


@event_manager.register("url_verification")
def request_url_verify_handler(req_data, tenant):
    print(f"url_verification handler invoked for tenant={tenant.tenant_id}")
    if req_data.event.token != tenant.config.feishu.verification_token:
        raise Exception("VERIFICATION_TOKEN is invalid")
    return {"challenge": req_data.event.challenge}


@event_manager.register("im.message.receive_v1")
def message_receive_event_handler(req_data, tenant):
    print("=" * 60)
    print(f"Received message event for tenant={tenant.tenant_id}")

    event_id = req_data.header.event_id
    if not tenant.processed_events.try_add(event_id):
        print(f"Skipping duplicated event: {event_id}")
        return {}

    tenant_config = tenant.config
    max_images = tenant_config.limits.max_images
    message_api_client = tenant.message_api_client
    message_processor = tenant.message_processor

    sender_id = req_data.event.sender.sender_id
    message = req_data.event.message
    open_id = sender_id.open_id

    print(f"Message type: {message.message_type}, chat type: {message.chat_type}")

    if message.chat_type == "p2p":
        message_api_client.add_reaction(message.message_id)
        message_api_client.send_text_with_open_id(open_id, "请在群里 @ 我吧，私聊我还不太会处理。")
        return {}

    if message.chat_type == "group":
        is_mentioned = False
        if hasattr(message, "mentions") and message.mentions:
            try:
                if tenant_config.feishu.bot_name and message.mentions[0].name in {tenant_config.feishu.bot_name}:
                    is_mentioned = True
            except Exception as e:
                print(f"Failed to inspect mentions: {e}")

        if not is_mentioned:
            print("Bot was not mentioned, skipping")
            return {}

        current_text = extract_text_from_message(message)

        if is_self_triggered_message(tenant, open_id, current_text):
            print("Detected self-triggered optimization message")
            _run_with_task_reaction(
                tenant,
                message.message_id,
                lambda: handle_text_only(tenant, message.chat_id, current_text),
            )
            return {}

        all_image_keys = []
        image_message_id = message.message_id
        quoted_file_item = None

        quoted_message = get_quoted_message_info(message, message_api_client)

        if quoted_message:
            quoted_msg_type = quoted_message.get("msg_type")
            quoted_content = quoted_message.get("content")
            quoted_message_id = quoted_message.get("message_id")

            print(f"Quoted message type: {quoted_msg_type}")

            if quoted_msg_type == "image":
                try:
                    content_data = json.loads(quoted_content)
                    image_key = content_data.get("image_key")
                    if image_key:
                        all_image_keys.append(image_key)
                        image_message_id = quoted_message_id
                except Exception as e:
                    logging.error(f"Failed to parse quoted image message: {e}")

            elif quoted_msg_type == "post":
                image_keys = extract_image_keys_from_post(quoted_content, max_images=max_images)
                if image_keys:
                    all_image_keys.extend(image_keys[:max_images])
                    image_message_id = quoted_message_id
                else:
                    quoted_text = extract_text_from_post(quoted_content)
                    combined_text = f"[引用内容: {quoted_text}]\n\n{current_text}" if quoted_text else current_text
                    _run_with_task_reaction(
                        tenant,
                        message.message_id,
                        lambda: handle_text_only(tenant, message.chat_id, combined_text),
                    )
                    return {}

            elif quoted_msg_type == "text":
                try:
                    quoted_text = json.loads(quoted_content).get("text", "")
                except Exception:
                    quoted_text = quoted_content
                combined_text = f"[引用内容: {quoted_text}]\n\n{current_text}"
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_text_only(tenant, message.chat_id, combined_text),
                )
                return {}

            elif quoted_msg_type == "file":
                quoted_file_item = _parse_file_item(quoted_content)

            if all_image_keys:
                current_image_keys = []

                if message.message_type == "post":
                    current_image_keys = extract_image_keys_from_post(message.content, max_images=max_images)
                elif message.message_type == "image":
                    try:
                        content_data = json.loads(message.content)
                        image_key = content_data.get("image_key")
                        if image_key:
                            current_image_keys = [image_key]
                    except Exception as e:
                        logging.error(f"Failed to parse current image message: {e}")

                remaining_slots = max_images - len(all_image_keys)
                if current_image_keys and remaining_slots > 0:
                    def process_quoted_and_current_images():
                        quoted_image_paths = download_images(tenant, quoted_message_id, all_image_keys)
                        current_image_paths = download_images(
                            tenant,
                            message.message_id,
                            current_image_keys[:remaining_slots],
                        )

                        all_image_paths = quoted_image_paths + current_image_paths
                        if all_image_paths:
                            response_dict = message_processor.process_image_message(
                                current_text,
                                message.chat_id,
                                all_image_paths,
                            )
                            send_response(tenant, message.chat_id, response_dict)

                            if response_dict.get("needs_reflection") and response_dict.get("reflection_context"):
                                threading.Thread(
                                    target=perform_reflection_and_retry,
                                    args=(tenant, message.chat_id, response_dict["reflection_context"], all_image_paths),
                                    daemon=True,
                                ).start()

                            total_found = len(all_image_keys) + len(current_image_keys)
                            if total_found > max_images:
                                notice = designer.get_multi_image_notice(max_images, total_found)
                                message_api_client.send_text_with_chat_id(message.chat_id, notice)

                    _run_with_task_reaction(
                        tenant,
                        message.message_id,
                        process_quoted_and_current_images,
                    )
                    return {}

            if all_image_keys:
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_with_images(tenant, message.chat_id, image_message_id, all_image_keys, current_text),
                )
                return {}

            if quoted_file_item:
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_with_files(tenant, message.chat_id, quoted_message_id, [quoted_file_item], current_text),
                )
                return {}

        if message.message_type == "image":
            try:
                content_data = json.loads(message.content)
                image_key = content_data.get("image_key")
                if image_key:
                    _run_with_task_reaction(
                        tenant,
                        message.message_id,
                        lambda: handle_with_images(tenant, message.chat_id, message.message_id, [image_key], current_text),
                    )
                else:
                    message_api_client.send_text_with_chat_id(message.chat_id, designer.get_image_info_failed())
            except Exception as e:
                logging.error(f"Failed to process image message: {e}")
                message_api_client.send_text_with_chat_id(message.chat_id, designer.get_image_process_failed())
            return {}

        if message.message_type == "post":
            image_keys = extract_image_keys_from_post(message.content, max_images=max_images)
            if image_keys:
                keys_to_process = image_keys[:max_images]
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_with_images(tenant, message.chat_id, message.message_id, keys_to_process, current_text),
                )

                if len(image_keys) > max_images:
                    notice = designer.get_multi_image_notice(max_images, len(image_keys))
                    message_api_client.send_text_with_chat_id(message.chat_id, notice)
            else:
                if current_text:
                    _run_with_task_reaction(
                        tenant,
                        message.message_id,
                        lambda: handle_text_only(tenant, message.chat_id, current_text),
                    )
                else:
                    message_api_client.send_text_with_chat_id(message.chat_id, designer.get_empty_message_reply())
            return {}

        if message.message_type == "file":
            file_item = _parse_file_item(message.content)
            if file_item:
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_with_files(tenant, message.chat_id, message.message_id, [file_item], current_text),
                )
            else:
                message_api_client.send_text_with_chat_id(message.chat_id, "文件消息解析失败，请重新发送。")
            return {}

        if message.message_type == "text":
            if current_text:
                _run_with_task_reaction(
                    tenant,
                    message.message_id,
                    lambda: handle_text_only(tenant, message.chat_id, current_text),
                )
            else:
                message_api_client.send_text_with_chat_id(message.chat_id, designer.get_empty_text_reply())
            return {}

    return {}
