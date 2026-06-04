from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .exporter import BASE_HEADERS, PRICE_HEADERS, build_sheet_values
from .models import PriceMatrix

SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]


@dataclass(frozen=True)
class GoogleSheetConfig:
    spreadsheet_id: str
    sheet_name: str
    service_account_file: str | None = None
    service_account_json: str | None = None


class GoogleSheetsExporter:
    def __init__(self, config: GoogleSheetConfig):
        self.config = config
        self.service = self._build_service()

    def sync_matrix(self, matrix: PriceMatrix) -> None:
        sheet_id = self._ensure_sheet()
        values = build_sheet_values(matrix)
        self._clear_sheet()
        self._write_values(values)
        self._apply_formatting(sheet_id, matrix)

    def _build_service(self):
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google dependencies are not installed. Run `pip install -e .`."
            ) from exc

        if self.config.service_account_json:
            info = json.loads(self.config.service_account_json)
            credentials = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPE)
        elif self.config.service_account_file:
            credentials = Credentials.from_service_account_file(
                self.config.service_account_file,
                scopes=SHEETS_SCOPE,
            )
        else:
            raise ValueError("Service account credentials are required")
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _ensure_sheet(self) -> int:
        spreadsheet = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.config.spreadsheet_id)
            .execute()
        )
        for sheet in spreadsheet.get("sheets", []):
            props = sheet["properties"]
            if props["title"] == self.config.sheet_name:
                return int(props["sheetId"])

        response = (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=self.config.spreadsheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": self.config.sheet_name,
                                    "gridProperties": {"frozenRowCount": 3, "frozenColumnCount": len(BASE_HEADERS)},
                                }
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])

    def _clear_sheet(self) -> None:
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.config.spreadsheet_id,
            range=f"'{self.config.sheet_name}'",
            body={},
        ).execute()

    def _write_values(self, values: list[list[Any]]) -> None:
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.config.spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {
                        "range": f"'{self.config.sheet_name}'!A2",
                        "values": values,
                    }
                ],
            },
        ).execute()

    def _apply_formatting(self, sheet_id: int, matrix: PriceMatrix) -> None:
        width = len(BASE_HEADERS) + len(matrix.dates) * len(PRICE_HEADERS)
        requests: list[dict[str, Any]] = [
            # Drop any merges left over from a previous (differently shaped) sync,
            # otherwise the new date-header merges collide and the API returns 400.
            {
                "unmergeCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 3,
                        "startColumnIndex": 0,
                        "endColumnIndex": max(width, 1),
                    }
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 3, "frozenColumnCount": len(BASE_HEADERS)},
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 3,
                        "startColumnIndex": 0,
                        "endColumnIndex": max(width, 1),
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {"bold": True},
                            "wrapStrategy": "WRAP",
                        }
                    },
                    "fields": (
                        "userEnteredFormat("
                        "horizontalAlignment,verticalAlignment,textFormat,wrapStrategy)"
                    ),
                }
            },
        ]

        start_col = len(BASE_HEADERS)
        for date_index in range(len(matrix.dates)):
            first_col = start_col + date_index * len(PRICE_HEADERS)
            requests.append(
                {
                    "mergeCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 2,
                            "startColumnIndex": first_col,
                            "endColumnIndex": first_col + len(PRICE_HEADERS),
                        },
                        "mergeType": "MERGE_ALL",
                    }
                }
            )

        if width:
            requests.append(
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": width,
                        }
                    }
                }
            )

        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.config.spreadsheet_id,
            body={"requests": requests},
        ).execute()
