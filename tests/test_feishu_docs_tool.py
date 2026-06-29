from core.tools.feishu_docs import FeishuDocsTool


class FakeFeishuClient:
    def read_doc(self, document_id, lang=0):
        return {"document_id": document_id, "content": "hello doc"}

    def append_doc_text(self, document_id, text, block_id=None, index=None):
        return {"data": {"document_id": document_id, "text": text, "block_id": block_id, "index": index}}

    def read_sheet_values(self, spreadsheet_token, range_):
        return {"valueRange": {"range": range_, "values": [["A", "B"], [1, 2]]}}

    def write_sheet_values(self, spreadsheet_token, range_, values):
        return {"spreadsheetToken": spreadsheet_token, "range": range_, "values": values}

    def append_sheet_values(self, spreadsheet_token, range_, values):
        return {"spreadsheetToken": spreadsheet_token, "range": range_, "values": values}


def test_read_doc():
    result = FeishuDocsTool().execute(
        {"action": "read_doc", "document_id": "doc123"},
        {"message_api_client": FakeFeishuClient()},
    )

    assert result["text"] == "hello doc"
    assert result["data"]["document_id"] == "doc123"


def test_read_sheet_formats_values():
    result = FeishuDocsTool().execute(
        {"action": "read_sheet", "spreadsheet_token": "sheet123", "range": "Sheet1!A1:B2"},
        {"message_api_client": FakeFeishuClient()},
    )

    assert result["text"] == "A\tB\n1\t2"


def test_write_sheet_requires_2d_values():
    try:
        FeishuDocsTool().execute(
            {
                "action": "write_sheet",
                "spreadsheet_token": "sheet123",
                "range": "Sheet1!A1",
                "values": ["not-a-row"],
            },
            {"message_api_client": FakeFeishuClient()},
        )
    except ValueError as exc:
        assert "2D array" in str(exc)
    else:
        raise AssertionError("expected ValueError")
