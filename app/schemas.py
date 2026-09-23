from pydantic import BaseModel, Field
from typing import Any, Optional


class ExcelUploadCreate(BaseModel):
    filename: str = Field(..., min_length=1)
    row_count: int = 0


class ExcelUploadOut(BaseModel):
    id: int
    filename: str
    row_count: int
    status: str

    class Config:
        orm_mode = True


class RawDataRowOut(BaseModel):
    id: int
    upload_id: int
    source_row: int
    data: str

    class Config:
        orm_mode = True
