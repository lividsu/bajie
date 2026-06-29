import logging
import os
import threading
import time

from llm.humanized_responses import designer


def download_image(tenant, message_id, image_key):
    try:
        image_path = tenant.message_api_client.download_image_from_message(message_id, image_key)
        print(f"Image downloaded: {image_path}")
        return image_path
    except Exception as e:
        logging.error(f"Failed to download image: {e}")
        return None


def download_images(tenant, message_id, image_keys):
    image_paths = []
    for image_key in image_keys:
        path = download_image(tenant, message_id, image_key)
        if path:
            image_paths.append(path)
    return image_paths


def download_file(tenant, message_id, file_key, file_name=None):
    try:
        file_path = tenant.message_api_client.download_file_from_message(message_id, file_key, file_name=file_name)
        print(f"File downloaded: {file_path}")
        return file_path
    except Exception as e:
        logging.error(f"Failed to download file: {e}")
        return None


def download_files(tenant, message_id, file_items):
    file_paths = []
    for item in file_items:
        file_key = item.get("file_key")
        file_name = item.get("file_name")
        if not file_key:
            continue
        path = download_file(tenant, message_id, file_key, file_name=file_name)
        if path:
            file_paths.append(path)
    return file_paths


def send_response(tenant, chat_id, response_dict):
    text_response = response_dict.get("text", "")
    image_path = response_dict.get("image_path")
    image_paths = response_dict.get("image_paths") or []
    file_path = response_dict.get("file_path") or response_dict.get("pdf_path")

    if image_path and image_path not in image_paths:
        image_paths = [image_path, *image_paths]

    for path in image_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            tenant.message_api_client.send_png_with_chat_id(chat_id, path)
            print(f"Image sent: {path}")
        except Exception as e:
            logging.error(f"Failed to send image: {e}")
            text_response = (text_response or "") + "\n\n(图片发送失败了)"

    if file_path and os.path.exists(file_path):
        try:
            tenant.message_api_client.send_file_with_chat_id(chat_id, file_path)
            print(f"File sent: {file_path}")
        except Exception as e:
            logging.error(f"Failed to send file: {e}")
            text_response = (text_response or "") + "\n\n(文件发送失败了)"

    if text_response:
        tenant.message_api_client.send_text_with_chat_id(chat_id, text_response)
        print("Text response sent")


def perform_reflection_and_retry(tenant, chat_id, reflection_context, reference_image_paths=None):
    try:
        print("=" * 60)
        print("Starting reflection flow")
        print("=" * 60)

        reflection_result = tenant.message_processor.reflect_and_decide(reflection_context)
        reflection_message = reflection_result.get("text", "")
        if reflection_message:
            tenant.message_api_client.send_text_with_chat_id(chat_id, reflection_message)

        if reflection_result.get("should_retry"):
            optimization_message = reflection_result["optimization_message"]

            if reference_image_paths:
                print("Retrying with reference images")

                def retry_with_images():
                    time.sleep(1)
                    response_dict = tenant.message_processor.process_image_message(
                        optimization_message,
                        chat_id,
                        reference_image_paths,
                    )
                    send_response(tenant, chat_id, response_dict)

                    if response_dict.get("needs_reflection") and response_dict.get("reflection_context"):
                        perform_reflection_and_retry(
                            tenant,
                            chat_id,
                            response_dict["reflection_context"],
                            reference_image_paths,
                        )

                threading.Thread(target=retry_with_images, daemon=True).start()
            else:
                print("Retrying text generation")

                def retry_text_gen():
                    time.sleep(1)
                    response_dict = tenant.message_processor.process_text_message(
                        optimization_message,
                        chat_id,
                    )
                    send_response(tenant, chat_id, response_dict)

                    if response_dict.get("needs_reflection") and response_dict.get("reflection_context"):
                        perform_reflection_and_retry(
                            tenant,
                            chat_id,
                            response_dict["reflection_context"],
                            None,
                        )

                threading.Thread(target=retry_text_gen, daemon=True).start()

    except Exception as e:
        logging.error(f"Reflection flow failed: {e}")
        print(f"Reflection flow failed: {e}")


def handle_text_only(tenant, chat_id, user_message):
    start_time = time.time()
    print(f"Handling text message: {user_message[:100]}...")

    response_dict = tenant.message_processor.process_text_message(user_message, chat_id)

    print(f"AI processing time: {time.time() - start_time:.2f}s")
    send_response(tenant, chat_id, response_dict)

    if response_dict.get("needs_reflection") and response_dict.get("reflection_context"):
        print("Reflection needed for generated image task")
        threading.Thread(
            target=perform_reflection_and_retry,
            args=(tenant, chat_id, response_dict["reflection_context"], None),
            daemon=True,
        ).start()


def handle_with_images(tenant, chat_id, message_id, image_keys, user_message):
    start_time = time.time()
    print(
        "Handling image message: "
        f"text='{user_message[:50] if user_message else ''}...', image_count={len(image_keys)}"
    )

    image_paths = download_images(tenant, message_id, image_keys)
    if not image_paths:
        tenant.message_api_client.send_text_with_chat_id(chat_id, designer.get_image_gen_failed())
        return

    print(f"Downloaded {len(image_paths)} images")
    response_dict = tenant.message_processor.process_image_message(user_message, chat_id, image_paths)

    print(f"AI processing time: {time.time() - start_time:.2f}s")
    send_response(tenant, chat_id, response_dict)

    if response_dict.get("needs_reflection") and response_dict.get("reflection_context"):
        print("Reflection needed for generated/edited image task")
        threading.Thread(
            target=perform_reflection_and_retry,
            args=(tenant, chat_id, response_dict["reflection_context"], image_paths),
            daemon=True,
        ).start()


def handle_with_image(tenant, chat_id, message_id, image_key, user_message):
    handle_with_images(tenant, chat_id, message_id, [image_key], user_message)


def handle_with_files(tenant, chat_id, message_id, file_items, user_message):
    start_time = time.time()
    print(
        "Handling file message: "
        f"text='{user_message[:50] if user_message else ''}...', file_count={len(file_items)}"
    )

    file_paths = download_files(tenant, message_id, file_items)
    if not file_paths:
        tenant.message_api_client.send_text_with_chat_id(chat_id, "文件下载失败，请重试。")
        return

    print(f"Downloaded {len(file_paths)} files")
    response_dict = tenant.message_processor.process_file_message(user_message, chat_id, file_paths)
    print(f"AI processing time: {time.time() - start_time:.2f}s")
    send_response(tenant, chat_id, response_dict)


def is_self_triggered_message(tenant, sender_id, message_content):
    if message_content and message_content.startswith("[优化重试]"):
        return True
    bot_open_id = tenant.config.feishu.bot_open_id
    if bot_open_id and sender_id == bot_open_id:
        return True
    return False
