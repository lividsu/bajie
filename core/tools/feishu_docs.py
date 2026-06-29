from __future__ import annotations

from typing import Any

from .base import Tool, ToolSpec


class FeishuDocsTool(Tool):
    def __init__(self):
        self.spec = ToolSpec(
            name="feishu_docs",
            description=(
                "Read and write Feishu Docs and Sheets. Supports actions: "
                "read_doc, append_doc_text, read_sheet, write_sheet, append_sheet."
            ),
            parameters={
                "action": "read_doc | append_doc_text | read_sheet | write_sheet | append_sheet",
                "document_id": "Feishu docx document id, required for doc actions",
                "spreadsheet_token": "Feishu spreadsheet token, required for sheet actions",
                "range": "Sheet range, for example Sheet1!A1:C10",
                "text": "Text to append to a document",
                "values": "2D array of cell values for sheet write/append",
                "block_id": "Optional document block id for append_doc_text",
                "index": "Optional insertion index for append_doc_text",
            },
        )

    def execute(self, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "")).strip()
        tenant = runtime.get("tenant")
        client = runtime.get("message_api_client") or (
            tenant.message_api_client if tenant is not None else None
        )
        if client is None:
            raise ValueError("message_api_client is required in runtime")

        if action == "read_doc":
            document_id = _required(args, "document_id")
            result = client.read_doc(document_id=document_id, lang=int(args.get("lang", 0)))
            return {"text": result.get("content", ""), "data": result}

        if action == "append_doc_text":
            document_id = _required(args, "document_id")
            text = _required(args, "text")
            result = client.append_doc_text(
                document_id=document_id,
                text=text,
                block_id=args.get("block_id"),
                index=args.get("index"),
            )
            return {"text": "已写入飞书文档。", "data": result.get("data", result)}

        if action == "read_sheet":
            spreadsheet_token = _required(args, "spreadsheet_token")
            range_ = _required(args, "range")
            result = client.read_sheet_values(spreadsheet_token=spreadsheet_token, range_=range_)
            return {"text": _format_values(result), "data": result}

        if action == "write_sheet":
            spreadsheet_token = _required(args, "spreadsheet_token")
            range_ = _required(args, "range")
            values = _values(args)
            result = client.write_sheet_values(
                spreadsheet_token=spreadsheet_token,
                range_=range_,
                values=values,
            )
            return {"text": "已写入飞书表格。", "data": result}

        if action == "append_sheet":
            spreadsheet_token = _required(args, "spreadsheet_token")
            range_ = _required(args, "range")
            values = _values(args)
            result = client.append_sheet_values(
                spreadsheet_token=spreadsheet_token,
                range_=range_,
                values=values,
            )
            return {"text": "已追加到飞书表格。", "data": result}

        raise ValueError(f"Unsupported feishu_docs action: {action}")


def _required(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{key} is required")
    return str(value).strip()


def _values(args: dict[str, Any]) -> list[list[Any]]:
    values = args.get("values")
    if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
        raise ValueError("values must be a 2D array")
    return values


def _format_values(result: dict[str, Any]) -> str:
    value_range = result.get("valueRange") if isinstance(result, dict) else None
    values = value_range.get("values") if isinstance(value_range, dict) else None
    if not values:
        return "表格范围为空。"
    return "\n".join("\t".join("" if cell is None else str(cell) for cell in row) for row in values)
