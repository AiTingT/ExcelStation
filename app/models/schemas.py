from pydantic import BaseModel, Field
from typing import Optional, Any, List, Dict
from datetime import datetime


class UploadResponse(BaseModel):
    """文件上传响应"""
    sessionId: str
    fileName: str
    sheetName: str
    rowCount: int
    columnCount: int
    headers: List[str]


class TableQuery(BaseModel):
    """表格查询请求"""
    sessionId: str
    sheetName: str
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=100, ge=1, le=1000)
    sortColumn: Optional[str] = None
    sortOrder: Optional[str] = Field(default="asc", pattern="^(asc|desc)$")
    filters: Optional[Dict[str, Any]] = None


class TableData(BaseModel):
    """表格数据响应"""
    headers: List[str]
    rows: List[List[Any]]
    totalRows: int
    page: int
    pageSize: int
    totalPages: int


class StatsResponse(BaseModel):
    """统计数据响应"""
    sheetName: str
    rowCount: int
    columnCount: int
    numericColumns: List[str]
    stats: Dict[str, Dict[str, Any]]


class FilterOption(BaseModel):
    """筛选选项"""
    column: str
    values: List[Any]
    count: int


class UpdateDataRequest(BaseModel):
    """数据更新请求"""
    tableName: str
    rowId: int
    updates: Dict[str, Any]


class AiQueryRequest(BaseModel):
    """AI查询请求"""
    sessionId: str
    question: str
    apiKey: Optional[str] = None
    provider: str = Field(default="deepseek", pattern="^(deepseek|openai|qwen|ollama)$")


class InsertRowsRequest(BaseModel):
    """插入行请求"""
    tableName: str
    rows: List[Dict[str, Any]]


class DeleteRowsRequest(BaseModel):
    """删除行请求"""
    tableName: str
    rowIds: List[int]


class AddColumnRequest(BaseModel):
    """新增列请求"""
    tableName: str
    columnName: str
    defaultValue: str = ""


class RenameColumnRequest(BaseModel):
    """重命名列请求"""
    tableName: str
    newName: str


class RegexReplaceRequest(BaseModel):
    """正则替换请求"""
    tableName: str
    column: str
    pattern: str
    replacement: str


class SplitColumnRequest(BaseModel):
    """列拆分请求"""
    tableName: str
    sourceColumn: str
    delimiter: str = ","
    maxSplits: int = 0


class ConvertTypeRequest(BaseModel):
    """数据类型转换请求"""
    tableName: str
    column: str
    targetType: str  # "string" | "number" | "date"
    dateFormat: str = ""


class CleanPreviewRequest(BaseModel):
    """清洗预览请求（dry-run，不修改数据）"""
    operation: str  # deduplicate | fill-empty | regex-replace | split-column | convert-type
    tableName: str
    columns: Optional[List[str]] = None   # deduplicate
    fillColumn: str = ""                  # fill-empty
    fillValue: str = ""                   # fill-empty
    column: str = ""                      # regex-replace / convert-type
    pattern: str = ""                     # regex-replace
    replacement: str = ""                 # regex-replace
    sourceColumn: str = ""                # split-column
    delimiter: str = ","                  # split-column
    maxSplits: int = 0                    # split-column
    targetType: str = ""                  # convert-type
    dateFormat: str = ""                  # convert-type


class ValidationRule(BaseModel):
    """单条校验规则"""
    column: str
    ruleType: str  # "email" | "phone" | "range" | "regex" | "not_empty"
    params: Dict[str, Any] = {}


class ValidateRequest(BaseModel):
    """数据校验请求"""
    tableName: str
    rules: List[ValidationRule]


class NL2SQLRequest(BaseModel):
    """多轮 NL2SQL 请求"""
    question: str
    tableName: str = ""
    history: List[Dict[str, str]] = []
