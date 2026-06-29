import json
import logging
from pathlib import Path

class MessageApiClient(object):
    def __init__(self, app_id, app_secret, lark_host, image_cache_dir=None, file_cache_dir=None):
        self._app_id = app_id
        self._app_secret = app_secret
        self._lark_host = lark_host
        
        # 创建图片缓存目录
        self._image_cache_dir = Path(image_cache_dir or "image_cache")
        self._image_cache_dir.mkdir(parents=True, exist_ok=True)
        self._file_cache_dir = Path(file_cache_dir or "file_cache")
        self._file_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化飞书官方 SDK 客户端 (lark_oapi)
        try:
            import lark_oapi as lark
            self._lark_client = lark.Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .domain(self._lark_host) \
                .log_level(lark.LogLevel.INFO) \
                .build()
        except ImportError:
            logging.warning("lark_oapi not installed, official SDK features will be disabled")
            self._lark_client = None

    def add_reaction(self, message_id, emoji_type="THUMBSUP"):
        if not self._lark_client:
            logging.error("lark_oapi client is not initialized")
            return None

        from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji

        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()

            response = self._lark_client.im.v1.message_reaction.create(request)

            if not response.success():
                logging.warning(f"Failed to add reaction: code={response.code}, msg={response.msg}")
                return None
            logging.debug(f"Added {emoji_type} reaction to message {message_id}")
            return response.data.reaction_id if getattr(response, "data", None) else None
        except Exception as e:
            logging.warning(f"Error adding reaction: {e}")
            return None

    def delete_reaction(self, message_id, reaction_id):
        if not self._lark_client or not reaction_id:
            return

        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

        try:
            request = DeleteMessageReactionRequest.builder() \
                .message_id(message_id) \
                .reaction_id(reaction_id) \
                .build()
            response = self._lark_client.im.v1.message_reaction.delete(request)
            if not response.success():
                logging.debug(f"Failed to delete reaction: code={response.code}, msg={response.msg}")
        except Exception as e:
            logging.debug(f"Error deleting reaction: {e}")

    def finish_task_reaction(
        self,
        message_id,
        processing_reaction_id=None,
        done_emoji="DONE",
        failed_emoji="",
        failed=False,
    ):
        self.delete_reaction(message_id, processing_reaction_id)
        emoji = failed_emoji if failed and failed_emoji else done_emoji
        if emoji:
            self.add_reaction(message_id, emoji)

    def send_text_with_open_id(self, open_id, content):
        self.send("open_id", open_id, "text", content)

    def send_text_with_chat_id(self, chat_id, content):
        self.send("chat_id", chat_id, "text", content)

    def send_image_with_chat_id(self, chat_id, image_key, message_id=None):
        """通过chat_id发送图片消息 - 如果提供message_id则先下载再上传"""
        try:
            if message_id:
                # 从消息中下载图片
                image_path = self.download_image_from_message(message_id, image_key)
                
                # 上传图片获取新的image_key
                new_image_key = self.upload_image(image_path)
                
                # 使用新的image_key发送
                content = json.dumps({"image_key": new_image_key})
                self.send("chat_id", chat_id, "image", content)
            else:
                # 直接使用提供的image_key发送
                content = json.dumps({"image_key": image_key})
                self.send("chat_id", chat_id, "image", content)
            
        except Exception as e:
            logging.error(f"发送图片失败: {e}")
            raise

    def send_png_with_open_id(self, open_id, png_path):
        """直接发送PNG图片给指定用户"""
        image_key = self.upload_image(png_path)
        self.send_image_with_open_id(open_id, image_key)

    def send_png_with_chat_id(self, chat_id, png_path):
        """直接发送PNG图片到指定群组"""
        image_key = self.upload_image(png_path)
        content = json.dumps({"image_key": image_key})
        self.send("chat_id", chat_id, "image", content)

    def send_image_with_open_id(self, open_id, image_key):
        """通过open_id发送图片消息"""
        content = json.dumps({"image_key": image_key})
        self.send("open_id", open_id, "image", content)

    def send_file_with_chat_id(self, chat_id, file_path):
        """通过chat_id发送文件消息"""
        file_key = self.upload_file(file_path)
        content = json.dumps({"file_key": file_key})
        self.send("chat_id", chat_id, "file", content)

    def send(self, receive_id_type, receive_id, msg_type, content):
        """使用官方 SDK 发送消息"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")

        if msg_type == "text":
            content_str = json.dumps({"text": str(content)})
        else:
            content_str = content if isinstance(content, str) else json.dumps(content)

        request = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content_str)
                .build()
            ).build()
            
        print(f"发送消息请求: receive_id={receive_id}, msg_type={msg_type}")
        
        response = self._lark_client.im.v1.message.create(request)
        
        if not response.success():
            error_msg = f"发送消息失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)

    def get_message_content(self, message_id):
        """获取指定消息的内容 - 使用官方 SDK"""
        from lark_oapi.api.im.v1 import GetMessageRequest
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")
            
        print(f"获取消息内容: {message_id}")
        request = GetMessageRequest.builder().message_id(message_id).build()
        response = self._lark_client.im.v1.message.get(request)
        
        if not response.success():
            error_msg = f"获取消息内容失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)
            
        items = getattr(response.data, 'items', [])
        if not items:
            error_msg = f"未找到消息内容,items为空"
            logging.error(error_msg)
            print(error_msg)
            return {
                'message_id': message_id,
                'msg_type': None,
                'content': None,
                'create_time': None,
                'sender': None
            }
            
        message_data = items[0]
        # lark_oapi objects have attributes
        msg_type = message_data.msg_type
        content = message_data.body.content if message_data.body else None
        
        print(f"解析到消息: msg_type={msg_type}, content长度={len(content) if content else 0}")
        
        return {
            'message_id': message_data.message_id,
            'msg_type': msg_type,
            'content': content,
            'create_time': message_data.create_time,
            'sender': None # Sender info is complex in SDK, typically not heavily needed here based on previous usage
        }

    def download_image_from_message(self, message_id, file_key):
        """从消息中下载图片资源 - 使用官方 SDK"""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")
            
        print(f"从消息 {message_id} 下载图片: {file_key}")
        request = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type("image") \
            .build()
            
        response = self._lark_client.im.v1.message_resource.get(request)
        
        if not response.success():
            error_msg = f"下载图片失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)
            
        image_path = self._image_cache_dir / f"{file_key}.png"
        
        # response.file is typically a stream or bytes in lark_oapi
        file_data = response.file
        if hasattr(file_data, 'read'):
            file_data = file_data.read()
            
        with open(image_path, 'wb') as f:
            f.write(file_data)
            
        print(f"图片已保存到: {image_path}")
        return str(image_path)

    def download_file_from_message(self, message_id, file_key, file_name=None):
        """从消息中下载文件资源 - 使用官方 SDK"""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")

        print(f"从消息 {message_id} 下载文件: {file_key}")
        request = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type("file") \
            .build()

        response = self._lark_client.im.v1.message_resource.get(request)

        if not response.success():
            error_msg = f"下载文件失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)

        safe_name = file_name if file_name else f"{file_key}.bin"
        file_path = self._file_cache_dir / Path(safe_name).name

        file_data = response.file
        if hasattr(file_data, 'read'):
            file_data = file_data.read()

        with open(file_path, 'wb') as f:
            f.write(file_data)

        print(f"文件已保存到: {file_path}")
        return str(file_path)

    def upload_image(self, image_path):
        """上传图片到飞书服务器,返回image_key - 使用官方 SDK"""
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")
            
        print(f"上传图片: {image_path}")
        
        with open(image_path, 'rb') as f:
            request = CreateImageRequest.builder() \
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                ).build()
            response = self._lark_client.im.v1.image.create(request)
            
        if not response.success():
            error_msg = f"图片上传失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)
            
        new_image_key = response.data.image_key
        print(f"上传成功,新image_key: {new_image_key}")
        return new_image_key

    def upload_file(self, file_path):
        """上传文件到飞书服务器,返回file_key - 使用官方 SDK"""
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")

        file_name = Path(file_path).name
        print(f"上传文件: {file_path}")

        with open(file_path, 'rb') as f:
            request = CreateFileRequest.builder() \
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(file_name)
                    .file(f)
                    .build()
                ).build()
            response = self._lark_client.im.v1.file.create(request)

        if not response.success():
            error_msg = f"文件上传失败: code={response.code}, msg={response.msg}"
            logging.error(error_msg)
            print(error_msg)
            raise Exception(error_msg)

        file_key = response.data.file_key
        print(f"上传成功,新file_key: {file_key}")
        return file_key

    def _raw_json_request(self, method, uri, *, paths=None, queries=None, body=None):
        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")

        from lark_oapi.core.const import APPLICATION_JSON, CONTENT_TYPE, UTF_8
        from lark_oapi.core.enum import AccessTokenType, HttpMethod
        from lark_oapi.core.http import Transport
        from lark_oapi.core.model import BaseRequest, RequestOption
        from lark_oapi.core.token import verify

        request = BaseRequest()
        request.http_method = getattr(HttpMethod, method.upper())
        request.uri = uri
        request.token_types = {AccessTokenType.TENANT}
        request.paths = {str(k): str(v) for k, v in (paths or {}).items()}
        request.body = body
        for key, value in (queries or {}).items():
            request.add_query(key, value)

        option = RequestOption()
        verify(self._lark_client._config, request, option)
        if body is not None:
            option.headers[CONTENT_TYPE] = f"{APPLICATION_JSON}; charset=utf-8"
        response = Transport.execute(self._lark_client._config, request, option)
        payload = json.loads(str(response.content, UTF_8)) if response.content else {}
        if response.status_code >= 400 or payload.get("code", 0) != 0:
            raise Exception(f"Feishu API failed: status={response.status_code}, payload={payload}")
        return payload

    def read_doc(self, document_id, lang=0):
        from lark_oapi.api.docx.v1 import RawContentDocumentRequest

        if not self._lark_client:
            raise Exception("lark_oapi client is not initialized")
        request = RawContentDocumentRequest.builder().document_id(document_id).lang(lang).build()
        response = self._lark_client.docx.v1.document.raw_content(request)
        if not response.success():
            raise Exception(f"Read doc failed: code={response.code}, msg={response.msg}")
        data = getattr(response, "data", None)
        return {
            "document_id": document_id,
            "content": getattr(data, "content", "") if data else "",
        }

    def append_doc_text(self, document_id, text, block_id=None, index=None):
        block_id = block_id or document_id
        body = {
            "children": [
                {
                    "block_type": 2,
                    "text": {
                        "elements": [
                            {
                                "text_run": {
                                    "content": str(text),
                                }
                            }
                        ]
                    },
                }
            ]
        }
        if index is not None:
            body["index"] = int(index)
        return self._raw_json_request(
            "POST",
            "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children",
            paths={"document_id": document_id, "block_id": block_id},
            body=body,
        )

    def read_sheet_values(self, spreadsheet_token, range_):
        return self._raw_json_request(
            "GET",
            "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range",
            paths={"spreadsheet_token": spreadsheet_token, "range": range_},
        ).get("data", {})

    def write_sheet_values(self, spreadsheet_token, range_, values):
        return self._raw_json_request(
            "PUT",
            "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values",
            paths={"spreadsheet_token": spreadsheet_token},
            body={"valueRange": {"range": range_, "values": values}},
        ).get("data", {})

    def append_sheet_values(self, spreadsheet_token, range_, values, insert_data_option="INSERT_ROWS"):
        return self._raw_json_request(
            "POST",
            "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append",
            paths={"spreadsheet_token": spreadsheet_token},
            queries={"insertDataOption": insert_data_option},
            body={"valueRange": {"range": range_, "values": values}},
        ).get("data", {})
